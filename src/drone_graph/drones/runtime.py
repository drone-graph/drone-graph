from __future__ import annotations

import json
from dataclasses import dataclass
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
from drone_graph.gaps import Finding, GapStatus
from drone_graph.prompts import load_hivemind
from drone_graph.substrate import Substrate
from drone_graph.terminal import Terminal, TerminalTimeout


DEFAULT_MAX_TURNS = 20
DEFAULT_COMMAND_TIMEOUT_S = 60.0

# Kinds of finding that terminate the drone.
_CLOSE_KIND = "close"
_ABANDON_KIND = "abandon"


@dataclass
class DroneResult:
    drone_id: str
    gap_id: str
    status: GapStatus
    finding_id: str | None
    tokens_in: int
    tokens_out: int
    cost_usd: float
    turns_used: int
    error: str | None = None


_TOOLS: list[dict[str, Any]] = [
    {
        "name": "terminal_run",
        "description": (
            "Run a bash command in your persistent shell. State (cwd, env, "
            "functions) persists across calls. Returns stdout, stderr, exit_code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Shell command to execute."},
                "timeout_s": {
                    "type": "number",
                    "description": "Per-command wall-clock timeout in seconds.",
                    "default": DEFAULT_COMMAND_TIMEOUT_S,
                },
            },
            "required": ["cmd"],
        },
    },
    {
        "name": "cm_read_gap",
        "description": "Re-read the full gap record you are working on.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cm_write_finding",
        "description": (
            "Deposit a finding into the collective mind. Use kind='close' to "
            "close the gap and exit. Use kind='abandon' if you cannot close it — "
            "the gap will remain open for another drone. Any other kind is a "
            "non-terminal note."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "close | abandon | note | <other>",
                },
                "summary": {"type": "string"},
                "payload_ref": {
                    "type": ["string", "null"],
                    "description": "Optional filesystem path or URI pointing at the artifact.",
                },
            },
            "required": ["kind", "summary"],
        },
    },
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_gap(substrate: Substrate, gap_id: str) -> dict[str, Any]:
    rows = substrate.execute_read(
        "MATCH (g:Gap {id: $id}) RETURN g",
        id=gap_id,
    )
    if not rows:
        raise ValueError(f"gap {gap_id} not found")
    return dict(rows[0]["g"])


def _build_initial_messages(gap: dict[str, Any], max_turns: int) -> list[dict[str, Any]]:
    criteria = gap.get("nl_criteria") or "Gap description is the acceptance criterion."
    body = (
        f"Gap id: {gap['id']}\n"
        f"Description: {gap['description']}\n"
        f"Acceptance criteria: {criteria}\n\n"
        f"You have {max_turns} turns. Each tool call uses one turn."
    )
    return [{"role": "user", "content": body}]


def _turn_reminder(turns_remaining: int) -> str:
    return f"[turns remaining: {turns_remaining}]"


def run_drone(
    gap_id: str,
    *,
    substrate: Substrate,
    client: ChatClient,
    max_turns: int = DEFAULT_MAX_TURNS,
    command_timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S,
    tape: "EventTape | None" = None,
) -> DroneResult:
    drone_id = str(uuid4())
    spawned_at = _now_iso()
    gap = _read_gap(substrate, gap_id)

    terminal = Terminal()
    messages = _build_initial_messages(gap, max_turns)
    system = load_hivemind()

    usage_total = Usage()
    turns_used = 0
    finding: Finding | None = None
    final_status = GapStatus.failed
    error: str | None = None

    if tape is not None:
        tape.emit("drone.spawn", drone_id=drone_id, gap_id=gap_id, model=client.model)

    try:
        while turns_used < max_turns:
            turns_used += 1
            resp = client.chat(system=system, messages=messages, tools=_TOOLS)
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
                # Model ended turn without a closing finding — treat as failure.
                error = "drone ended turn without writing a close/abandon finding"
                break

            messages.append({"role": "assistant", "content": resp.raw_assistant_content})

            tool_results: list[dict[str, Any]] = []
            terminate_with: Finding | None = None
            terminal_status: GapStatus | None = None
            for call in resp.tool_calls:
                result_content, maybe_finding, maybe_status = _dispatch_tool(
                    call,
                    gap=gap,
                    terminal=terminal,
                    command_timeout_s=command_timeout_s,
                    tape=tape,
                    drone_id=drone_id,
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": result_content,
                    }
                )
                if maybe_finding is not None:
                    terminate_with = maybe_finding
                    terminal_status = maybe_status

            turns_remaining = max_turns - turns_used
            user_content: list[dict[str, Any]] = list(tool_results)
            user_content.append({"type": "text", "text": _turn_reminder(turns_remaining)})
            messages.append({"role": "user", "content": user_content})

            if terminate_with is not None:
                finding = terminate_with
                assert terminal_status is not None
                final_status = terminal_status
                break
        else:
            error = f"drone hit max turns ({max_turns}) without closing"
    except TerminalTimeout as e:
        error = f"terminal timeout: {e}"
    except Exception as e:  # noqa: BLE001
        error = f"drone error: {type(e).__name__}: {e}"
    finally:
        terminal.close()

    died_at = _now_iso()
    total_cost = cost_usd(client.provider, client.model, usage_total)

    _write_drone_and_finding(
        substrate=substrate,
        drone_id=drone_id,
        gap_id=gap_id,
        spawned_at=spawned_at,
        died_at=died_at,
        provider=client.provider,
        model=client.model,
        usage=usage_total,
        cost=total_cost,
        finding=finding,
        final_status=final_status,
    )

    if tape is not None:
        tape.emit(
            "drone.die",
            drone_id=drone_id,
            gap_id=gap_id,
            status=final_status.value,
            turns_used=turns_used,
            tokens_in=usage_total.tokens_in,
            tokens_out=usage_total.tokens_out,
            cost_usd=total_cost,
            error=error,
        )

    return DroneResult(
        drone_id=drone_id,
        gap_id=gap_id,
        status=final_status,
        finding_id=finding.id if finding else None,
        tokens_in=usage_total.tokens_in,
        tokens_out=usage_total.tokens_out,
        cost_usd=total_cost,
        turns_used=turns_used,
        error=error,
    )


def _dispatch_tool(
    call: ToolCall,
    *,
    gap: dict[str, Any],
    terminal: Terminal,
    command_timeout_s: float,
    tape: "EventTape | None",
    drone_id: str,
) -> tuple[str, Finding | None, GapStatus | None]:
    """Run one tool call. Returns (tool_result_text, finding_if_terminal, status_if_terminal)."""
    if call.name == "terminal_run":
        cmd = str(call.input.get("cmd", ""))
        timeout = float(call.input.get("timeout_s", command_timeout_s))
        if tape is not None:
            tape.emit("tool.terminal_run", drone_id=drone_id, cmd=cmd, timeout_s=timeout)
        try:
            r = terminal.run(cmd, timeout=timeout)
        except TerminalTimeout as e:
            return f"TIMEOUT: {e}", None, None
        payload = {
            "stdout": r.stdout,
            "stderr": r.stderr,
            "exit_code": r.exit_code,
        }
        return json.dumps(payload), None, None

    if call.name == "cm_read_gap":
        return json.dumps({k: _json_safe(v) for k, v in gap.items()}), None, None

    if call.name == "cm_write_finding":
        kind = str(call.input.get("kind", "")).strip()
        summary = str(call.input.get("summary", ""))
        payload_ref = call.input.get("payload_ref")
        payload_ref = str(payload_ref) if payload_ref else None
        finding = Finding(kind=kind, summary=summary, payload_ref=payload_ref)
        if tape is not None:
            tape.emit(
                "tool.write_finding",
                drone_id=drone_id,
                kind=kind,
                finding_id=finding.id,
            )
        if kind == _CLOSE_KIND:
            return f"finding recorded: {finding.id}. Gap will close.", finding, GapStatus.closed
        if kind == _ABANDON_KIND:
            return (
                f"finding recorded: {finding.id}. Gap will be re-opened.",
                finding,
                GapStatus.open,
            )
        # Non-terminal finding; not written to graph in Phase 0.
        return f"note recorded (non-terminal): {finding.id}", None, None

    return f"unknown tool: {call.name}", None, None


def _json_safe(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _write_drone_and_finding(
    *,
    substrate: Substrate,
    drone_id: str,
    gap_id: str,
    spawned_at: str,
    died_at: str,
    provider: Provider,
    model: str,
    usage: Usage,
    cost: float,
    finding: Finding | None,
    final_status: GapStatus,
) -> None:
    params: dict[str, Any] = {
        "drone_id": drone_id,
        "gap_id": gap_id,
        "spawned_at": spawned_at,
        "died_at": died_at,
        "provider": provider.value,
        "model": model,
        "tokens_in": usage.tokens_in,
        "tokens_out": usage.tokens_out,
        "cost_usd": cost,
        "status": final_status.value,
        "closed_at": died_at if final_status is GapStatus.closed else None,
    }
    cypher = [
        "MATCH (g:Gap {id: $gap_id})",
        "CREATE (d:Drone {",
        "  id: $drone_id, spawned_at: datetime($spawned_at), died_at: datetime($died_at),",
        "  provider: $provider, model: $model, gap_id: $gap_id,",
        "  tokens_in: $tokens_in, tokens_out: $tokens_out, cost_usd: $cost_usd",
        "})",
        "CREATE (d)-[:WORKED]->(g)",
        "SET g.status = $status",
        (
            "SET g.closed_at = CASE WHEN $closed_at IS NULL "
            "THEN null ELSE datetime($closed_at) END"
        ),
    ]
    if finding is not None:
        params.update(
            {
                "finding_id": finding.id,
                "finding_kind": finding.kind,
                "finding_summary": finding.summary,
                "finding_payload_ref": finding.payload_ref,
                "finding_created_at": finding.created_at.isoformat(),
            }
        )
        cypher += [
            "CREATE (f:Finding {",
            "  id: $finding_id, kind: $finding_kind, summary: $finding_summary,",
            "  payload_ref: $finding_payload_ref, created_at: datetime($finding_created_at)",
            "})",
            "CREATE (f)-[:PRODUCED_BY]->(d)",
        ]
        if final_status is GapStatus.closed:
            cypher.append("CREATE (g)-[:CLOSED_WITH]->(f)")
    substrate.execute_write("\n".join(cypher), **params)


# Forward-declare for type hint; the real class lives in orchestrator.tape.
class EventTape:  # pragma: no cover - structural protocol
    def emit(self, event: str, **fields: Any) -> None: ...
