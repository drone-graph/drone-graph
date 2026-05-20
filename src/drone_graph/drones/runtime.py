"""Unified drone runtime — one ``run_drone`` for every gap.

There is one drone class. It loads ``hivemind.md`` as system prompt, reads the
gap it was dispatched against, computes its tool surface from the gap's
``tool_loadout`` (plus universal cm_* query tools), runs a multi-turn message
loop, and exits when:

  - it calls ``cm_write_finding`` with kind=fill or fail (emergent gaps), or
  - the structural / observational verb on a preset gap completes its work
    (preset gaps don't terminate via fill — they hit a soft turn limit), or
  - it hits ``max_turns`` without closing.

The gap's intent + criteria + ``context_preload`` rendering is the only
role-specific input. There is no per-role module; preset behavior comes from
the gap's intent text and tool loadout.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4


def _llm_payload_logging_enabled() -> bool:
    """Truthy ``DRONE_GRAPH_LOG_LLM_PAYLOADS`` env var → log full prompts +
    responses to the drone tape on each turn. Off by default."""
    v = os.environ.get("DRONE_GRAPH_LOG_LLM_PAYLOADS", "").strip().lower()
    return v in ("1", "true", "yes", "on")


_NARRATION_SYSTEM = (
    "You narrate drone activity to an operator running a swarm. Your output "
    "appears in the operator's chat panel as a message from the drone. Write "
    "1–2 plain English sentences directed at the operator, first person from "
    "the drone (\"I…\"). Lead with any real-world action the drone took "
    "(deploying, pushing to a remote, registering, sending) — these are the "
    "things the operator needs to know happened. If the drone is blocked on a "
    "human action, say what's needed. No technical jargon, no markdown, no "
    "preamble. Just the sentence(s)."
)


def _narrate_drone_exit(
    *,
    gap: Gap,
    terminal_finding: Finding,
    outcome: str | None,
    provider: Provider,
) -> str | None:
    """Produce a 1–2 sentence chat-rail narration of what this drone did or
    is asking for. Returns ``None`` on any failure — narration is
    observability, not correctness; we never let it break a drone exit.

    Uses the operator's provider with the ``nano`` tier model so cost is
    bounded to a few cents per swarm session even at high drone counts.

    Honest-narration guard: outcomes that aren't the drone's own
    decision (cancelled, budget_exceeded) get a fixed string instead of
    an LLM call. Previously the nano narrator invented plausible-sounding
    "I stopped because I need X" reasons for cancellations the drone
    didn't actually choose — misleading the operator into thinking the
    drone was asking for help when it had just been shut down.
    """
    outcome_label = outcome or terminal_finding.kind.value
    if outcome_label == "cancelled":
        return (
            "My work on this gap was cancelled before I could finish — "
            "the scheduler signaled stop (likely a restart, retire, or "
            "shutdown). No action needed on your part; a future drone "
            "will pick this gap up if it's still unfilled."
        )
    if outcome_label == "budget_exceeded":
        return (
            "Exiting because the swarm hit its cost ceiling. Raise the "
            "ceiling in the top bar to let me continue."
        )
    try:
        from drone_graph.gaps.records import ModelTier
        from drone_graph.model_registry.registry import ModelRegistry

        reg = ModelRegistry.load_auto()
        if not reg.is_populated:
            return None
        entry = reg.resolve_for_tier(ModelTier.nano, provider)
        narrator = make_client(entry.provider, entry.vendor_model_id)

        # Trim long finding summaries so we don't pay to narrate 200-line dumps.
        # The model only needs the gist — the operator can click through for
        # the full thing.
        summary = terminal_finding.summary[:2000]
        intent_short = gap.intent[:400]
        outcome_label = outcome or terminal_finding.kind.value
        user_msg = (
            f"Gap the drone was working on:\n{intent_short}\n\n"
            f"Drone outcome: {outcome_label}.\n"
            f"Drone's own finding summary:\n{summary}\n\n"
            f"Write 1–2 sentences for the operator's chat panel."
        )
        resp = narrator.chat(
            system=_NARRATION_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            tools=[],
        )
        text = (resp.text or "").strip()
        # Strip leading quotes/markdown artefacts some small models add.
        for prefix in ('"', "'", "**", "- "):
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip()
        return text or None
    except Exception:
        return None

def _narrate_drone_progress(
    *,
    gap: Gap,
    turn: int,
    recent_tool_calls: list[str],
    provider: Provider,
) -> str | None:
    """Mid-run narration: a 1-2 sentence "what I'm doing right now" line.

    Fires at turn checkpoints (3, 8, 13 …) on emergent workers. Cheap
    nano-tier call. Lets the operator see progress without waiting for
    exit narration — especially useful when ``max_workers`` is high and
    workers run for many turns. Returns ``None`` on any failure;
    narration is observability, not correctness.
    """
    try:
        from drone_graph.gaps.records import ModelTier
        from drone_graph.model_registry.registry import ModelRegistry

        reg = ModelRegistry.load_auto()
        if not reg.is_populated:
            return None
        entry = reg.resolve_for_tier(ModelTier.nano, provider)
        narrator = make_client(entry.provider, entry.vendor_model_id)

        intent_short = gap.intent[:400]
        # Just the last 10 tool-call names — enough signal, doesn't blow
        # the prompt up. The narrator infers intent from the sequence.
        calls = ", ".join(recent_tool_calls[-10:]) or "(no tool calls yet)"
        user_msg = (
            f"Gap I'm working on:\n{intent_short}\n\n"
            f"I'm at turn {turn}. Recent tool calls: {calls}.\n\n"
            "Write 1-2 sentences for the operator's chat panel describing "
            "what I'm currently doing. Present tense, first person. No "
            "technical jargon, no preamble, no markdown."
        )
        resp = narrator.chat(
            system=_NARRATION_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            tools=[],
        )
        text = (resp.text or "").strip()
        for prefix in ('"', "'", "**", "- "):
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip()
        return text or None
    except Exception:
        return None


if TYPE_CHECKING:
    from drone_graph.orchestrator.tape import EventTape as EventTape

from drone_graph.drones.providers import (
    ChatClient,
    Provider,
    ToolCall,
    Usage,
    cost_usd,
    make_client,
)
from drone_graph.gaps import Finding, Gap, GapStore
from drone_graph.gaps.records import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    FindingAuthor,
    FindingKind,
)
from drone_graph.prompts import load_hivemind
from drone_graph.signals import SignalStore
from drone_graph.substrate import Substrate
from drone_graph.terminal import Terminal, TerminalDead, TerminalTimeout
from drone_graph.tools import (
    DroneContext,
    ToolStore,
    get_builtin,
    to_anthropic_tool_def,
    universal_query_tool_names,
)
from drone_graph.tools.records import TrustTier
from drone_graph.tools.trust import effective_trust

DEFAULT_MAX_TURNS = 20
DEFAULT_COMMAND_TIMEOUT_S = 60.0
DEFAULT_CLAIM_TTL_S = 60.0
DEFAULT_HEARTBEAT_PERIOD_S = 20.0

# Turn numbers at which an emergent worker emits a mid-run narration so
# the operator's chat panel shows progress while a long drone grinds.
# Spaced so a typical 8-12 turn worker emits 1-2 status lines and a
# 20-turn worker emits 4. Each line costs a few cents (nano-tier).
_NARRATE_AT_TURNS = frozenset({3, 8, 13, 18})

# Tools every emergent (non-preset) gap gets unless the gap explicitly
# overrides ``tool_loadout``. Universal cm_* query tools are added on top.
DEFAULT_EMERGENT_LOADOUT = [
    "terminal_run",
    "cm_read_gap",
    "cm_write_finding",
    "cm_register_tool",
    "cm_request_tool",
    "cm_acquire_file",
    "cm_release_file",
    "cm_install_package",
    "cm_browser",
    "cm_skill_registry",
]

# Tools that imply this drone needs a real bash terminal.
_TERMINAL_TOOLS = {"terminal_run"}


@dataclass
class DroneResult:
    # "fill" | "fail" | "preset_done" | "max_turns" | "error" | "cancelled"
    # | "claim_lost"
    drone_id: str
    gap_id: str
    outcome: str
    finding_id: str | None
    findings_written: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    turns_used: int
    error: str | None = None


class _Heartbeat(threading.Thread):
    """Background renewer for the gap claim. Stops itself on lost claim."""

    def __init__(
        self,
        signals: SignalStore,
        kind: str,
        key: str,
        drone_id: str,
        ttl_s: float,
        period_s: float,
    ) -> None:
        super().__init__(daemon=True, name=f"heartbeat-{drone_id[:8]}")
        self._signals = signals
        self._kind = kind
        self._key = key
        self._drone_id = drone_id
        self._ttl_s = ttl_s
        self._period_s = period_s
        self._stop_event = threading.Event()
        self._lost = False

    def run(self) -> None:
        while not self._stop_event.wait(self._period_s):
            if not self._signals.heartbeat(
                self._kind, self._key, self._drone_id, self._ttl_s
            ):
                self._lost = True
                return

    def stop(self) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=2.0)

    @property
    def lost(self) -> bool:
        return self._lost


@dataclass
class _RuntimeState:
    """Mutable per-drone state that the runtime + tool dispatchers share."""

    ctx: DroneContext
    findings_written: int = 0
    terminate: tuple[Finding, str] | None = None  # (finding, outcome)
    invocations: list[dict[str, Any]] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class _TerminalBox:
    """Holds the drone's current terminal, letting tool dispatchers swap it on death."""

    def __init__(self, cwd: str | None = None) -> None:
        self._cwd = cwd
        self._terminal: Terminal = Terminal(cwd=cwd)

    def get(self) -> Terminal:
        return self._terminal

    def respawn(self) -> None:
        try:
            self._terminal.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup of a dead shell
            pass
        self._terminal = Terminal(cwd=self._cwd)

    def close(self) -> None:
        try:
            self._terminal.close()
        except Exception:  # noqa: BLE001
            pass


def _resolve_loadout(gap: Gap) -> list[str]:
    """Compute the *active* tool name set for a gap.

    Rules:
      - If ``gap.tool_loadout`` is non-empty: that's the explicit set (preset
        gaps and locked-down emergent gaps).
      - Otherwise: default emergent loadout.
      - Universal cm_* query tools are always added on top.
    """
    if gap.tool_loadout:
        base = list(gap.tool_loadout)
    else:
        base = list(DEFAULT_EMERGENT_LOADOUT)
    universal = universal_query_tool_names()
    out: list[str] = []
    seen: set[str] = set()
    for name in base + universal:
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _render_tool_defs(active_names: set[str]) -> list[dict[str, Any]]:
    defs: list[dict[str, Any]] = []
    for name in active_names:
        d = to_anthropic_tool_def(name)
        if d is None:
            continue
        defs.append(d)
    return defs


def _build_initial_messages(
    gap: Gap, store: GapStore, max_turns: int
) -> list[dict[str, Any]]:
    parts: list[str] = []
    parts.append(f"# Your gap\n")
    parts.append(f"id: {gap.id}")
    if gap.preset_kind is not None:
        parts.append(f"preset_kind: {gap.preset_kind}")
    # The browser is authenticated with the operator's signed-in Google
    # account.  Inject this in the user message (not the system prompt) so
    # all drones — emergent, GF, Alignment — see it.
    parts.append(
        "\nThe browser is authenticated with the operator's Google account — "
        "first check if you are already signed in (look for avatar/user menu on the homepage). "
        "If already signed in, proceed directly. If not, use \"Sign in with Google\" when a platform offers it.\n"
    )
    # For emergent gaps, inject a mandatory skill-check block in the SAME
    # message as the intent.  LLMs prioritise user-message content over
    # system-prompt rules, so placing the instruction here makes it
    # impossible for the model to "forget" it when it reads the intent.
    if gap.preset_kind is None:
        parts.append(
            "\n--- SKILL CHECK ---\n"
            "Check suggested_tools first. If one matches: cm_request_tool(name).\n"
            "Otherwise: scan_local -> install matching -> derive tool_name "
            "(non-alphanum->underscore, lowercase) -> cm_request_tool(name).\n"
            "Follow skill steps precisely — general automation fails on skilled platforms.\n"
            "--- END SKILL CHECK ---"
        )

    # For the Gap Finding preset, inject a skill-aware decomposition check.
    # GF must scan skills, embed matching skill names into child gap
    # intents, AND set tool_suggestions so workers can skip discovery.
    # GF must NOT try to install/use skills or do browser work itself
    # (its job is structural decomposition, not execution).
    if gap.preset_kind == "gap_finding":
        parts.append(
            "\n--- SKILL CHECK ---\n"
            "Before decomposing, scan_local. If a skill matches a child's work:\n"
            "  - Embed skill name in child intent\n"
            "  - Set tool_suggestions to derived tool_name "
            "(non-alphanum->underscore, lowercase)\n"
            "  - Set max_worker_turns=60 for complex multi-step gaps\n"
            "Do NOT install/use skills yourself or use cm_browser — "
            "create children for execution.\n"
            "--- END SKILL CHECK ---"
        )
    parts.append(
        "\n--- SELF-DEBUG ---\n"
        "Errors? Research the error, inspect output/screenshots, check skill "
        "registry, adapt — don't blindly repeat.\n"
        "--- END SELF-DEBUG ---"
    )
    parts.append(f"\nintent:\n{gap.intent}")
    parts.append(f"\ncriteria:\n{gap.criteria}\n")
    if gap.tool_suggestions:
        parts.append(
            "\n## Suggested tools (not preloaded — call cm_request_tool to activate):"
        )
        for s in gap.tool_suggestions:
            parts.append(f"- {s}")
        parts.append("")
    if gap.context_preload:
        # Imported lazily to dodge a drones ↔ orchestrator import cycle.
        from drone_graph.orchestrator.preload import render_preloads

        parts.append("\n# Substrate context (auto-loaded for you)\n")
        parts.append(render_preloads(store, list(gap.context_preload)))

    # Inject past findings for this gap so the drone can build on prior work.
    try:
        past = store.findings_for_gap(gap.id, limit=15)
    except Exception:
        past = []
    if past:
        lines = ["\n# Past findings for this gap (from earlier runs)"]
        for f in past:
            kind = f.kind.value if f.kind else "?"
            lines.append(f"  [{kind}] {f.summary}")
        parts.append("\n".join(lines))

    parts.append(f"\n{max_turns} turns.")
    return [{"role": "user", "content": "\n".join(parts)}]


def _turn_reminder(turns_remaining: int) -> str:
    return f"[turns remaining: {turns_remaining}]"


def run_drone(
    gap_or_id: Gap | str,
    *,
    store: GapStore,
    tool_store: ToolStore,
    client: ChatClient,
    tick: int,
    max_turns: int = DEFAULT_MAX_TURNS,
    command_timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S,
    tape: "EventTape | None" = None,
    signals: SignalStore | None = None,
    claim_ttl_s: float = DEFAULT_CLAIM_TTL_S,
    heartbeat_period_s: float = DEFAULT_HEARTBEAT_PERIOD_S,
    run_id: str | None = None,
    workspace_dir: str | None = None,
) -> DroneResult:
    """Dispatch one drone against ``gap_or_id`` and run until termination.

    When ``signals`` is supplied (Phase 3+), the runtime acquires a ``gap``
    claim before the first turn, heartbeats it on a background thread, checks
    for a soft-cancel signal at every turn boundary, and releases on exit. If
    the claim cannot be acquired (another drone already holds it), the
    runtime returns ``outcome='claim_lost'`` without making any model calls.
    """
    gap = (
        gap_or_id
        if isinstance(gap_or_id, Gap)
        else store.get(gap_or_id) if isinstance(gap_or_id, str) else None
    )
    if gap is None:
        raise ValueError(f"no gap with id {gap_or_id}")

    drone_id = str(uuid4())
    spawned_at = _now_iso()

    heartbeat: _Heartbeat | None = None
    if signals is not None:
        if not signals.try_acquire(
            "gap", gap.id, drone_id, ttl_s=claim_ttl_s
        ):
            if tape is not None:
                tape.emit(
                    "drone.claim_lost",
                    drone_id=drone_id,
                    gap_id=gap.id,
                )
            return DroneResult(
                drone_id=drone_id,
                gap_id=gap.id,
                outcome="claim_lost",
                finding_id=None,
                findings_written=0,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                turns_used=0,
                error=None,
            )
        heartbeat = _Heartbeat(
            signals,
            "gap",
            gap.id,
            drone_id,
            ttl_s=claim_ttl_s,
            period_s=heartbeat_period_s,
        )
        heartbeat.start()

    active = set(_resolve_loadout(gap))
    for raw in gap.tool_suggestions or []:
        name = str(raw).strip()
        if not name or name in active:
            continue
        tier = effective_trust(name, tool_store)
        if tier is TrustTier.high:
            active.add(name)
    suggested = set(gap.tool_suggestions or [])

    needs_terminal = any(name in _TERMINAL_TOOLS for name in active)

    # Create a dedicated workspace directory for this gap so all files the
    # drone generates (CSVs, Excel files, websites, reports, etc.) land in
    # a predictable location instead of polluting the project root.
    # Sanitise gap ID for the filesystem — Windows reserves < > : " / \ | ? *
    # and gap IDs like "preset:gap_finding" would otherwise crash with
    # NotADirectoryError (WinError 267).
    _safe_id = gap.id.translate(
        str.maketrans({c: "_" for c in '<>:"/\\|?*'})
    )
    base = Path(workspace_dir) if workspace_dir else Path("workspace")
    # Each gap gets a per-gap subfolder inside ``drone-graph-work/``.
    # The project root (``workspace/<project-name>/``) is created by the
    # scheduler; the drone only creates its own work subfolder.
    gap_workspace = base / "drone-graph-work" / _safe_id
    gap_workspace.mkdir(parents=True, exist_ok=True)
    workspace_str = str(gap_workspace.resolve())

    terminal_box = (
        _TerminalBox(cwd=workspace_str) if needs_terminal else None
    )

    ctx = DroneContext(
        gap_id=gap.id,
        drone_id=drone_id,
        tick=tick,
        store=store,
        tool_store=tool_store,
        terminal_box=terminal_box,
        tape=tape,
        signals=signals,
        active_tool_names=active,
        suggested_tool_names=suggested,
        workspace_dir=workspace_str,
    )
    state = _RuntimeState(ctx=ctx)

    messages = _build_initial_messages(gap, store, max_turns)
    system = load_hivemind()

    usage_total = Usage()
    accumulated_cost: float = 0.0
    turns_used = 0
    error: str | None = None
    outcome = "max_turns"  # default if nothing terminates
    # IDs of operator->drone chat findings already injected into the
    # message stream — so we don't re-inject the same operator nudge on
    # every turn.
    seen_chat_ids: set[str] = set()
    # Per-turn output cap, resolved from gap.max_output_tokens or the
    # tier default. Used both for the SDK call and for the runaway guard.
    turn_max_tokens = (
        int(gap.max_output_tokens)
        if gap.max_output_tokens
        else DEFAULT_MAX_OUTPUT_TOKENS.get(gap.model_tier, 4096)
    )
    # Consecutive turns where the model hit the output cap. The runaway
    # guard exits the drone after 3 in a row (almost always an output
    # loop, not legitimate progress).
    cap_hits = 0
    # Running history of tool-call names the drone has made, used by the
    # mid-run narrator to give the operator a plain-English status line.
    tool_call_history: list[str] = []

    if tape is not None:
        tape.emit(
            "drone.spawn",
            drone_id=drone_id,
            gap_id=gap.id,
            preset_kind=gap.preset_kind,
            model=client.model,
            active_tools=sorted(ctx.active_tool_names),
        )

    is_preset = gap.preset_kind is not None

    try:
        while turns_used < max_turns:
            turns_used += 1
            if signals is not None:
                lost = heartbeat is not None and heartbeat.lost
                cancelled_flag = signals.is_cancelled("gap", gap.id)
                if cancelled_flag or lost:
                    reason = "cancelled" if cancelled_flag else "lease lost"
                    cancelled = store.append_finding(
                        tick=tick,
                        author=FindingAuthor.worker,
                        kind=FindingKind.cancelled,
                        summary=(
                            f"drone {drone_id[:8]} {reason} at turn "
                            f"{turns_used} after writing "
                            f"{state.findings_written} findings"
                        ),
                        affected_gap_ids=[gap.id],
                    )
                    state.terminate = (cancelled, "cancelled")
                    state.findings_written += 1
                    outcome = "cancelled"
                    if tape is not None:
                        tape.emit(
                            "drone.cancelled",
                            drone_id=drone_id,
                            gap_id=gap.id,
                            turn=turns_used,
                            reason=reason,
                            finding_id=cancelled.id,
                        )
                    break
            tool_defs = _render_tool_defs(ctx.active_tool_names)
            # Opt-in: when DRONE_GRAPH_LOG_LLM_PAYLOADS is truthy, write the
            # full request/response (system prompt, full messages, full
            # tools, assistant text + tool calls) to the drone tape. Off by
            # default — payloads are voluminous (10–100KB per turn) and not
            # always wanted. Set the env var when you need to debug GF /
            # Alignment behavior or audit what a worker actually saw.
            log_llm = _llm_payload_logging_enabled()
            if log_llm and tape is not None:
                tape.emit(
                    "llm.request",
                    drone_id=drone_id,
                    turn=turns_used,
                    provider=client.provider.value,
                    model=client.model,
                    system=system,
                    messages=messages,
                    tools=tool_defs,
                )
            try:
                resp = client.chat(
                    system=system,
                    messages=messages,
                    tools=tool_defs,
                    max_tokens=turn_max_tokens,
                )
            except Exception as e:  # noqa: BLE001 - bubble client errors up as drone error
                error = f"client error: {type(e).__name__}: {e}"
                outcome = "error"
                break
            usage_total.tokens_in += resp.usage.tokens_in
            usage_total.tokens_out += resp.usage.tokens_out
            usage_total.cache_read_input_tokens += resp.usage.cache_read_input_tokens

            # Runaway-output guard: a drone that emits ``tokens_out >=
            # max_tokens`` three turns in a row is almost certainly stuck
            # in an output loop (generating max-length essays / repeating
            # itself). Exit with a structured ``fail`` so GF can react,
            # rather than burning the rest of the turn budget.
            if resp.usage.tokens_out >= turn_max_tokens - 8:
                cap_hits += 1
            else:
                cap_hits = 0
            if cap_hits >= 3 and not is_preset:
                ro = store.apply_fail(
                    gap_id=gap.id,
                    summary=(
                        f"drone {drone_id[:8]} exited at turn {turns_used} after "
                        f"3 consecutive max-output-token turns "
                        f"(cap={turn_max_tokens}). Likely an output-generation "
                        "loop or runaway synthesis. GF should decompose the gap "
                        "into smaller leaves or lower its max_output_tokens."
                    ),
                    tick=tick,
                )
                state.terminate = (ro, "fail")
                state.findings_written += 1
                outcome = "fail"
                if tape is not None:
                    tape.emit(
                        "drone.runaway_output",
                        drone_id=drone_id,
                        gap_id=gap.id,
                        turn=turns_used,
                        cap=turn_max_tokens,
                        finding_id=ro.id,
                    )
                break

            turn_cost = cost_usd(client.provider, client.model, resp.usage)
            accumulated_cost += turn_cost
            ceiling_crossed = False
            if signals is not None and run_id is not None:
                ceiling_crossed = not signals.add_cost(run_id, turn_cost)

            tool_call_history.extend(tc.name for tc in resp.tool_calls)
            if tape is not None:
                tape.emit(
                    "drone.turn",
                    drone_id=drone_id,
                    turn=turns_used,
                    stop_reason=resp.stop_reason,
                    tokens_in=resp.usage.tokens_in,
                    tokens_out=resp.usage.tokens_out,
                    cache_read_input_tokens=resp.usage.cache_read_input_tokens,
                    cost_usd=turn_cost,
                    gap_id=gap.id,
                    tool_calls=[tc.name for tc in resp.tool_calls],
                )
                # Mid-run narration. Fires for emergent workers at turns
                # 3, 8, 13, … — gives the operator a plain-English status
                # line in the chat while a long-running drone is still
                # mid-flight. Cheap nano-tier call; failures swallowed.
                if not is_preset and turns_used in _NARRATE_AT_TURNS:
                    progress = _narrate_drone_progress(
                        gap=gap,
                        turn=turns_used,
                        recent_tool_calls=tool_call_history,
                        provider=client.provider,
                    )
                    if progress:
                        tape.emit(
                            "drone.narrate",
                            drone_id=drone_id,
                            gap_id=gap.id,
                            outcome="in_progress",
                            text=progress,
                        )
                if log_llm:
                    tape.emit(
                        "llm.response",
                        drone_id=drone_id,
                        turn=turns_used,
                        text=resp.text,
                        tool_calls=[
                            {"name": tc.name, "input": tc.input}
                            for tc in resp.tool_calls
                        ],
                        stop_reason=resp.stop_reason,
                        raw_assistant_content=resp.raw_assistant_content,
                    )

            if ceiling_crossed:
                be = store.append_finding(
                    tick=tick,
                    author=FindingAuthor.worker,
                    kind=FindingKind.budget_exceeded,
                    summary=(
                        f"drone {drone_id[:8]} hit swarm cost ceiling at "
                        f"turn {turns_used}; total run spend exceeded "
                        f"--max-cost-usd"
                    ),
                    affected_gap_ids=[gap.id],
                )
                state.terminate = (be, "budget_exceeded")
                state.findings_written += 1
                outcome = "budget_exceeded"
                if tape is not None:
                    tape.emit(
                        "drone.budget_exceeded",
                        drone_id=drone_id,
                        gap_id=gap.id,
                        turn=turns_used,
                        finding_id=be.id,
                    )
                break

            if not resp.tool_calls:
                # Preset drones may have nothing to do this turn — no_issue
                # alignment will have called write_alignment_finding; if they
                # *didn't* call any tool, treat that as "preset done."
                if is_preset and state.findings_written > 0:
                    outcome = "preset_done"
                else:
                    error = "drone ended turn without using any tool"
                    outcome = "error"
                break

            messages.append({"role": "assistant", "content": resp.raw_assistant_content})
            tool_results: list[dict[str, Any]] = []
            for call in resp.tool_calls:
                content = _dispatch_one(call, state)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": content,
                    }
                )

            turns_remaining = max_turns - turns_used
            user_content: list[dict[str, Any]] = list(tool_results)
            # Inject any operator messages that arrived during the turn so
            # the drone reads them at the next turn boundary even if it
            # never called cm_browser.await_operator. Mark IDs as seen so
            # we don't re-inject on subsequent turns.
            for chat_text in _drain_operator_chat(store, gap.id, seen_chat_ids):
                user_content.append(
                    {"type": "text", "text": f"[operator chat] {chat_text}"}
                )
            user_content.append({"type": "text", "text": _turn_reminder(turns_remaining)})
            messages.append({"role": "user", "content": user_content})

            if state.terminate is not None:
                _, outcome = state.terminate
                break

            # Preset drones don't write fill/fail terminals — they exit when
            # they've recorded their findings and have nothing else to add.
            # Heuristic: a preset drone that emitted at least one structural
            # or alignment finding this turn is "done" if its next turn would
            # be empty. We let the model decide; max_turns is the safety net.
        else:
            outcome = "max_turns"
    except TerminalTimeout as e:
        error = f"terminal timeout: {e}"
        outcome = "error"
    except Exception as e:  # noqa: BLE001
        error = f"drone error: {type(e).__name__}: {e}"
        outcome = "error"
    finally:
        if terminal_box is not None:
            terminal_box.close()
        if heartbeat is not None:
            heartbeat.stop()
        # Best-effort authenticated (real Chrome) browser cleanup.
        # Closes the persistent page for this drone so its tab doesn't
        # linger after the drone exits.
        try:
            from drone_graph.tools.builtins.browser.authenticated.tool import (
                cleanup_for_drone as auth_cleanup,
            )

            auth_cleanup(drone_id)
        except Exception:  # noqa: BLE001
            pass
        if signals is not None:
            signals.release_all_for_drone(drone_id)

    # If an emergent drone exited without a terminal finding, record a fail
    # finding so Gap Finding has something to react to. Preset drones don't
    # need this — their outputs are findings emitted along the way.
    terminal_finding: Finding | None = None
    if state.terminate is not None:
        terminal_finding, _ = state.terminate
    elif not is_preset:
        terminal_finding = store.apply_fail(
            gap_id=gap.id,
            summary=error or "drone exited without a terminal finding",
            tick=tick,
        )
        outcome = "fail"

    died_at = _now_iso()
    total_cost = accumulated_cost

    _write_drone_node(
        substrate=store.substrate,
        drone_id=drone_id,
        gap_id=gap.id,
        terminal_finding_id=terminal_finding.id if terminal_finding else None,
        spawned_at=spawned_at,
        died_at=died_at,
        provider=client.provider,
        model=client.model,
        usage=usage_total,
        cost=total_cost,
    )

    # Best-effort chat-rail narration. For emergent (worker) drones that
    # produced a terminal finding, ask a cheap (``nano`` tier) model to
    # write 1–2 plain-English sentences for the operator. Skipped silently
    # on any error — narration is observability, not correctness.
    if tape is not None and not is_preset and terminal_finding is not None:
        narration = _narrate_drone_exit(
            gap=gap,
            terminal_finding=terminal_finding,
            outcome=outcome,
            provider=client.provider,
        )
        if narration:
            tape.emit(
                "drone.narrate",
                drone_id=drone_id,
                gap_id=gap.id,
                outcome=outcome,
                text=narration,
                finding_id=terminal_finding.id,
            )

    if tape is not None:
        tape.emit(
            "drone.die",
            drone_id=drone_id,
            gap_id=gap.id,
            preset_kind=gap.preset_kind,
            outcome=outcome,
            turns_used=turns_used,
            tokens_in=usage_total.tokens_in,
            tokens_out=usage_total.tokens_out,
            cost_usd=total_cost,
            findings_written=state.findings_written,
            error=error,
        )

    return DroneResult(
        drone_id=drone_id,
        gap_id=gap.id,
        outcome=outcome,
        finding_id=terminal_finding.id if terminal_finding else None,
        findings_written=state.findings_written,
        tokens_in=usage_total.tokens_in,
        tokens_out=usage_total.tokens_out,
        cost_usd=total_cost,
        turns_used=turns_used,
        error=error,
    )


def _drain_operator_chat(
    store: GapStore, gap_id: str, seen: set[str]
) -> list[str]:
    """Pull any new operator->drone chat findings for this gap, mark them
    seen, and return their text bodies. Latest 50 findings only — bounded
    so this can't slow turn boundaries down in long-running swarms.
    """
    try:
        recent = store.recent_findings(limit=50)
    except Exception:  # noqa: BLE001 - poll is best-effort
        return []
    out: list[str] = []
    for f in recent:
        if f.id in seen:
            continue
        if f.kind != FindingKind.chat_with_drone:
            continue
        if f.author != FindingAuthor.user:
            continue
        if gap_id not in (f.affected_gap_ids or []):
            continue
        seen.add(f.id)
        out.append(f.summary)
    return out


def _dispatch_one(call: ToolCall, state: _RuntimeState) -> str:
    """Run one tool call against the registry; mutate ``state`` accordingly."""
    ctx = state.ctx
    if call.name not in ctx.active_tool_names:
        return (
            f"ERROR: tool {call.name!r} is not in your active tool set. "
            f"Available: {sorted(ctx.active_tool_names)}. "
            f"Use cm_list_tools / cm_request_tool to find and activate more."
        )
    builtin = get_builtin(call.name)
    if builtin is None:
        return f"ERROR: tool {call.name!r} is registered as a name but has no Python dispatcher."
    # Permission gate. Under ``ask_external`` / ``ask_everything`` this may
    # block the dispatcher until the operator answers. Under ``open`` (or for
    # read / substrate-write tools at any tier) it returns immediately.
    from drone_graph.permissions import check_or_wait

    decision = check_or_wait(
        signals=ctx.signals,
        drone_id=ctx.drone_id,
        gap_id=ctx.gap_id,
        tool_name=call.name,
        tool_input=call.input,
        tape=ctx.tape,
    )
    if not decision.granted:
        reason = decision.note or "denied"
        prefix = "PERMISSION_TIMEOUT" if decision.timed_out else "PERMISSION_DENIED"
        return (
            f"{prefix}: operator did not authorise {call.name!r}. "
            f"Note: {reason}. Try another approach or write a fail finding "
            "explaining what you can't do without it."
        )
    try:
        result = builtin.dispatch(call.input, ctx)
    except Exception as e:  # noqa: BLE001 - tool dispatchers shouldn't crash the drone
        return f"ERROR: {call.name} raised {type(e).__name__}: {e}"
    # Side-effects: terminal finding, finding count, USED_BY edge.
    if result.terminal_finding is not None and result.outcome is not None:
        state.terminate = (result.terminal_finding, result.outcome)
        state.findings_written += 1
    elif _emits_finding(call.name):
        state.findings_written += 1
    state.findings_written += result.extra_findings_written
    # Record tool usage on the gap (best-effort; ignore errors).
    try:
        ctx.tool_store.record_usage(call.name, ctx.gap_id)
    except Exception:  # noqa: BLE001
        pass
    return result.content


_FINDING_EMITTING_TOOLS = {
    "decompose",
    "create",
    "retire",
    "reopen",
    "rewrite_intent",
    "noop",
    "write_alignment_finding",
}


def _emits_finding(tool_name: str) -> bool:
    return tool_name in _FINDING_EMITTING_TOOLS


def _write_drone_node(
    *,
    substrate: Substrate,
    drone_id: str,
    gap_id: str,
    terminal_finding_id: str | None,
    spawned_at: str,
    died_at: str,
    provider: Provider,
    model: str,
    usage: Usage,
    cost: float,
) -> None:
    substrate.execute_write(
        "MATCH (g:Gap {id: $gap_id}) "
        "CREATE (d:Drone { "
        "  id: $drone_id, spawned_at: datetime($spawned_at), died_at: datetime($died_at), "
        "  provider: $provider, model: $model, gap_id: $gap_id, "
        "  tokens_in: $tokens_in, tokens_out: $tokens_out, cost_usd: $cost_usd "
        "}) "
        "CREATE (d)-[:WORKED]->(g)",
        drone_id=drone_id,
        gap_id=gap_id,
        spawned_at=spawned_at,
        died_at=died_at,
        provider=provider.value,
        model=model,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        cost_usd=cost,
    )
    if terminal_finding_id is not None:
        substrate.execute_write(
            "MATCH (d:Drone {id: $drone_id}), (f:Finding {id: $finding_id}) "
            "CREATE (f)-[:PRODUCED_BY]->(d)",
            drone_id=drone_id,
            finding_id=terminal_finding_id,
        )


