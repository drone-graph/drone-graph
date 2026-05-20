"""Per-tool permission gate.

Three pieces:

  * ``tool_category(name)`` — categorize each builtin: ``read``, ``local``,
    ``external``. Reads never prompt. Locals prompt only under
    ``ask_everything``. Externals prompt under both ``ask_external`` and
    ``ask_everything``.
  * ``tier_requires_prompt(tier, category)`` — pure function answering
    "does this tier prompt for that category?"
  * ``check_or_wait(...)`` — the synchronous gate the dispatcher calls.
    Inserts a row into ``signals.permissions``, polls until the operator
    answers via the API, removes the row, returns a decision.

The current process reads its tier from ``DRONE_GRAPH_PERMISSION_TIER`` (set
by ``settings.apply_to_env``). Missing or unknown values fall back to
``open`` — the safest behavior for headless runs that don't have a UI to
answer prompts.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from drone_graph.signals.store import SignalStore

# Default + cap for how long a dispatcher waits on the operator before giving
# up. Five minutes is generous but bounded; the deny carries that information
# back to the model so it can decide what to do next.
DEFAULT_TIMEOUT_S = 300.0
POLL_INTERVAL_S = 0.5

# ---- Tool categorization ---------------------------------------------------

# Read-only inspection. Never prompts under any tier.
_READ_TOOLS: frozenset[str] = frozenset({
    # Universal substrate queries.
    "cm_get_gap", "cm_list_gaps", "cm_children_of", "cm_parent_of",
    "cm_leaves", "cm_findings", "cm_finding",
    "cm_list_tools", "cm_get_tool",
    # Read-only structural / preset helpers.
    "cm_read_gap",
})

# Substrate-internal writes (findings, structural edits). These touch the
# collective mind, not the operator's machine. Never prompts — they're how
# the swarm thinks out loud.
_SUBSTRATE_WRITE_TOOLS: frozenset[str] = frozenset({
    "cm_write_finding",
    "decompose", "create", "retire", "reopen", "rewrite_intent",
    "noop", "write_alignment_finding",
})

# Touches the machine but not the world. Prompts under ``ask_everything``.
_LOCAL_TOOLS: frozenset[str] = frozenset({
    "terminal_run",
    "cm_acquire_file", "cm_release_file",
    "cm_request_tool",
})

# Reaches beyond the machine OR pulls remote code into it. Prompts under
# both ``ask_external`` and ``ask_everything``.
_EXTERNAL_TOOLS: frozenset[str] = frozenset({
    "cm_browser",
    "cm_check_browser",
    "cm_install_package",
    "cm_register_tool",
})


def tool_category(name: str) -> str:
    """One of ``'read'``, ``'substrate'``, ``'local'``, ``'external'``, or
    ``'unknown'``. Unknown tools are treated as ``'local'`` by
    ``tier_requires_prompt`` — fail safe, not silent."""
    if name in _READ_TOOLS:
        return "read"
    if name in _SUBSTRATE_WRITE_TOOLS:
        return "substrate"
    if name in _LOCAL_TOOLS:
        return "local"
    if name in _EXTERNAL_TOOLS:
        return "external"
    return "unknown"


def tier_requires_prompt(tier: str, category: str) -> bool:
    """Does ``tier`` require the operator to approve a tool of ``category``?"""
    if category in ("read", "substrate"):
        return False
    if tier == "open":
        return False
    if tier == "ask_external":
        return category == "external"
    if tier == "ask_everything":
        return category in ("local", "external", "unknown")
    # Unknown tier: fail safe (no prompt). Matches the headless-default
    # behavior above.
    return False


def current_tier() -> str:
    return os.environ.get("DRONE_GRAPH_PERMISSION_TIER", "open") or "open"


# ---- Synchronous gate ------------------------------------------------------


@dataclass(frozen=True)
class PermissionDecision:
    granted: bool
    note: str | None
    """Operator-supplied resolution note. ``None`` if not provided."""
    timed_out: bool = False


def _summarize(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Render a one-line operator-readable summary of the call.

    Keep it short (<200 chars). The UI shows it inline; verbose payloads
    just clutter the prompt.
    """
    if tool_name == "terminal_run":
        cmd = tool_input.get("command") or tool_input.get("cmd") or ""
        return f"terminal_run: {_clip(str(cmd), 200)}"
    if tool_name == "cm_browser":
        action = tool_input.get("action") or tool_input.get("verb") or ""
        url = tool_input.get("url") or ""
        text = tool_input.get("text") or ""
        bits = [b for b in (str(action), str(url), str(text)) if b]
        return f"cm_browser: {_clip(' · '.join(bits), 200)}"
    if tool_name == "cm_install_package":
        pkg = tool_input.get("package") or tool_input.get("name") or ""
        manager = tool_input.get("manager") or ""
        return f"cm_install_package: {manager} {pkg}".strip()
    if tool_name == "cm_register_tool":
        return f"cm_register_tool: {tool_input.get('name', '')}"
    if tool_name == "cm_acquire_file":
        return f"cm_acquire_file: {tool_input.get('path', '')}"
    if tool_name == "cm_request_tool":
        return f"cm_request_tool: {tool_input.get('name', '')}"
    return f"{tool_name}"


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def check_or_wait(
    *,
    signals: SignalStore | None,
    drone_id: str,
    gap_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    tape: Any | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> PermissionDecision:
    """Either return ``granted=True`` immediately, or block until the operator
    answers (or the timeout elapses).

    Headless runs without a signal store skip the gate — there's nowhere to
    write the prompt and no operator to answer.
    """
    category = tool_category(tool_name)
    tier = current_tier()
    if not tier_requires_prompt(tier, category):
        return PermissionDecision(granted=True, note=None)
    if signals is None:
        # No sidecar wired up (e.g. unit test). Behave as ``open``.
        return PermissionDecision(granted=True, note=None)

    request_id = uuid.uuid4().hex
    summary = _summarize(tool_name, tool_input)
    signals.request_permission(
        request_id=request_id,
        drone_id=drone_id,
        gap_id=gap_id,
        tier=tier,
        tool_name=tool_name,
        category=category,
        summary=summary,
    )
    if tape is not None:
        try:
            tape.emit(
                "permission.request",
                request_id=request_id,
                drone_id=drone_id,
                gap_id=gap_id,
                tier=tier,
                tool_name=tool_name,
                category=category,
                summary=summary,
            )
        except Exception:  # noqa: BLE001 - emission is best-effort
            pass

    deadline = time.monotonic() + max(1.0, timeout_s)
    while True:
        rec = signals.get_permission(request_id)
        if rec is None:
            # Operator-side garbage collection? Treat as denial so we don't
            # silently proceed.
            return PermissionDecision(
                granted=False,
                note="permission row vanished before operator answered",
            )
        if rec.status == "granted":
            note = rec.resolver_note
            signals.consume_permission(request_id)
            if tape is not None:
                try:
                    tape.emit(
                        "permission.resolved",
                        request_id=request_id,
                        gap_id=gap_id,
                        outcome="granted",
                    )
                except Exception:  # noqa: BLE001
                    pass
            return PermissionDecision(granted=True, note=note)
        if rec.status == "denied":
            note = rec.resolver_note
            signals.consume_permission(request_id)
            if tape is not None:
                try:
                    tape.emit(
                        "permission.resolved",
                        request_id=request_id,
                        gap_id=gap_id,
                        outcome="denied",
                    )
                except Exception:  # noqa: BLE001
                    pass
            return PermissionDecision(granted=False, note=note)
        if time.monotonic() >= deadline:
            signals.consume_permission(request_id)
            if tape is not None:
                try:
                    tape.emit(
                        "permission.resolved",
                        request_id=request_id,
                        gap_id=gap_id,
                        outcome="timeout",
                    )
                except Exception:  # noqa: BLE001
                    pass
            return PermissionDecision(
                granted=False,
                note=f"operator did not answer within {int(timeout_s)}s",
                timed_out=True,
            )
        time.sleep(POLL_INTERVAL_S)
