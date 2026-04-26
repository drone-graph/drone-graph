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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from drone_graph.drones.providers import (
    ChatClient,
    Provider,
    ToolCall,
    Usage,
    cost_usd,
)
from drone_graph.gaps import Finding, Gap, GapStore
from drone_graph.prompts import load_hivemind
from drone_graph.substrate import Substrate
from drone_graph.terminal import Terminal, TerminalDead, TerminalTimeout
from drone_graph.tools import (
    DroneContext,
    ToolStore,
    get_builtin,
    to_anthropic_tool_def,
    universal_query_tool_names,
)

DEFAULT_MAX_TURNS = 20
DEFAULT_COMMAND_TIMEOUT_S = 60.0

# Tools every emergent (non-preset) gap gets unless the gap explicitly
# overrides ``tool_loadout``. Universal cm_* query tools are added on top.
DEFAULT_EMERGENT_LOADOUT = [
    "terminal_run",
    "cm_read_gap",
    "cm_write_finding",
    "cm_register_tool",
    "cm_request_tool",
]

# Tools that imply this drone needs a real bash terminal.
_TERMINAL_TOOLS = {"terminal_run"}


@dataclass
class DroneResult:
    drone_id: str
    gap_id: str
    outcome: str  # "fill" | "fail" | "preset_done" | "max_turns" | "error"
    finding_id: str | None
    findings_written: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    turns_used: int
    error: str | None = None


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

    def __init__(self) -> None:
        self._terminal: Terminal = Terminal()

    def get(self) -> Terminal:
        return self._terminal

    def respawn(self) -> None:
        try:
            self._terminal.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup of a dead shell
            pass
        self._terminal = Terminal()

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
    parts.append(
        f"\nYou have {max_turns} turns. Each model call (one or more tool uses) "
        f"counts as one turn."
    )
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
) -> DroneResult:
    """Dispatch one drone against ``gap_or_id`` and run until termination."""
    gap = (
        gap_or_id
        if isinstance(gap_or_id, Gap)
        else store.get(gap_or_id) if isinstance(gap_or_id, str) else None
    )
    if gap is None:
        raise ValueError(f"no gap with id {gap_or_id}")

    drone_id = str(uuid4())
    spawned_at = _now_iso()

    active = set(_resolve_loadout(gap))
    suggested = set(gap.tool_suggestions or [])

    needs_terminal = any(name in _TERMINAL_TOOLS for name in active)
    terminal_box = _TerminalBox() if needs_terminal else None

    ctx = DroneContext(
        gap_id=gap.id,
        drone_id=drone_id,
        tick=tick,
        store=store,
        tool_store=tool_store,
        terminal_box=terminal_box,
        tape=tape,
        active_tool_names=active,
        suggested_tool_names=suggested,
    )
    state = _RuntimeState(ctx=ctx)

    messages = _build_initial_messages(gap, store, max_turns)
    system = load_hivemind()

    usage_total = Usage()
    turns_used = 0
    error: str | None = None
    outcome = "max_turns"  # default if nothing terminates

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
            tool_defs = _render_tool_defs(ctx.active_tool_names)
            try:
                resp = client.chat(system=system, messages=messages, tools=tool_defs)
            except Exception as e:  # noqa: BLE001 - bubble client errors up as drone error
                error = f"client error: {type(e).__name__}: {e}"
                outcome = "error"
                break
            usage_total.tokens_in += resp.usage.tokens_in
            usage_total.tokens_out += resp.usage.tokens_out

            if tape is not None:
                tape.emit(
                    "drone.turn",
                    drone_id=drone_id,
                    turn=turns_used,
                    stop_reason=resp.stop_reason,
                    tokens_in=resp.usage.tokens_in,
                    tokens_out=resp.usage.tokens_out,
                    tool_calls=[tc.name for tc in resp.tool_calls],
                )

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
    total_cost = cost_usd(client.provider, client.model, usage_total)

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


# Forward-declare for type hint; the real class lives in orchestrator.tape.
class EventTape:  # pragma: no cover - structural protocol
    def emit(self, event: str, **fields: Any) -> None: ...
