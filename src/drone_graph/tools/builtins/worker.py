"""Worker tools — terminal, gap-read, finding-write, and runtime tool registration.

These are the default-emergent loadout: every drone working a non-preset gap
gets these unless the gap explicitly restricts them.
"""

from __future__ import annotations

import json
from typing import Any

from drone_graph.terminal import TerminalDead, TerminalTimeout
from drone_graph.tools.records import Tool, ToolKind
from drone_graph.tools.registry import (
    DroneContext,
    ToolResult,
    get_builtin,
    register_tool,
)

DEFAULT_COMMAND_TIMEOUT_S = 60.0


@register_tool(
    "terminal_run",
    (
        "Run a bash command in your persistent shell. State (cwd, env, "
        "functions) persists across calls. Returns stdout, stderr, exit_code. "
        "If the shell dies on a syntax error or crash, it is respawned and you "
        "get an error tool result — retry with a corrected command."
    ),
    {
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
)
def terminal_run(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    if ctx.terminal_box is None:
        return ToolResult(content="ERROR: this drone has no terminal available")
    cmd = str(args.get("cmd", ""))
    timeout = float(args.get("timeout_s", DEFAULT_COMMAND_TIMEOUT_S))
    if ctx.tape is not None:
        ctx.tape.emit(
            "tool.terminal_run", drone_id=ctx.drone_id, cmd=cmd, timeout_s=timeout
        )
    if not cmd.strip():
        return ToolResult(
            content="ERROR: empty command rejected. Pass a non-empty bash command."
        )
    try:
        r = ctx.terminal_box.get().run(cmd, timeout=timeout)
    except TerminalTimeout as e:
        return ToolResult(content=f"TIMEOUT: {e}")
    except TerminalDead as e:
        ctx.terminal_box.respawn()
        if ctx.tape is not None:
            ctx.tape.emit(
                "tool.terminal_respawn", drone_id=ctx.drone_id, reason=str(e)
            )
        return ToolResult(
            content=(
                f"ERROR: terminal died ({e}); a fresh shell has been started. "
                f"Previous shell state (cwd, env, unsaved variables) is gone. Retry."
            )
        )
    payload = {"stdout": r.stdout, "stderr": r.stderr, "exit_code": r.exit_code}
    return ToolResult(content=json.dumps(payload))


@register_tool(
    "cm_read_gap",
    "Re-read the full record of the gap you are currently working on.",
    {"type": "object", "properties": {}},
)
def cm_read_gap(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    g = ctx.store.get(ctx.gap_id)
    if g is None:
        return ToolResult(content=f"gap {ctx.gap_id} not found")
    return ToolResult(content=g.model_dump_json())


@register_tool(
    "cm_write_finding",
    (
        "Deposit a finding into the collective mind. Use kind='fill' when the "
        "gap's acceptance criteria are met — the gap will be marked filled and "
        "you will exit. Use kind='fail' if you cannot meet the criteria — the "
        "finding records why, the gap stays unfilled, and Gap Finding will "
        "decide on a later pass whether to decompose, retire, or create "
        "adjacent work. Any other kind is a non-terminal note. Attach 'paths' "
        "for any on-disk artefact (.md report, generated file, etc.) the "
        "finding references — keep 'summary' short."
    ),
    {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "description": "fill | fail | note | <other>",
            },
            "summary": {"type": "string"},
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Absolute paths to files this finding references. Other "
                    "drones will read these directly."
                ),
            },
        },
        "required": ["kind", "summary"],
    },
)
def cm_write_finding(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    kind = str(args.get("kind", "")).strip()
    summary = str(args.get("summary", ""))
    raw_paths = args.get("paths") or []
    paths = [str(p) for p in raw_paths if p] if isinstance(raw_paths, list) else []
    if kind == "fill":
        f = ctx.store.apply_fill(
            gap_id=ctx.gap_id,
            summary=summary,
            tick=ctx.tick,
            artefact_paths=paths,
        )
        if ctx.tape is not None:
            ctx.tape.emit(
                "tool.write_finding",
                drone_id=ctx.drone_id,
                kind=kind,
                finding_id=f.id,
                paths=paths,
            )
        return ToolResult(
            content=f"finding recorded: {f.id}. Gap filled.",
            terminal_finding=f,
            outcome="fill",
        )
    if kind == "fail":
        f = ctx.store.apply_fail(
            gap_id=ctx.gap_id,
            summary=summary,
            tick=ctx.tick,
            artefact_paths=paths,
        )
        if ctx.tape is not None:
            ctx.tape.emit(
                "tool.write_finding",
                drone_id=ctx.drone_id,
                kind=kind,
                finding_id=f.id,
                paths=paths,
            )
        return ToolResult(
            content=(
                f"finding recorded: {f.id}. Gap stays unfilled; Gap Finding will react."
            ),
            terminal_finding=f,
            outcome="fail",
        )
    return ToolResult(
        content=f"note acknowledged (non-terminal, not persisted): kind={kind!r}",
    )


@register_tool(
    "cm_register_tool",
    (
        "Register a tool you've installed (e.g. via pip / npm / apt) so future "
        "drones can discover it via cm_list_tools. The tool is recorded as "
        "documentation: the 'usage' string is what a future drone will run via "
        "terminal_run, with placeholders for inputs. Alignment may flag the "
        "registration if it looks suspicious; the tool is still visible to "
        "future drones but they'll see the flag."
    ),
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Globally unique tool name. Convention: snake_case verb_noun "
                    "(e.g. 'playwright_screenshot')."
                ),
            },
            "description": {"type": "string"},
            "usage": {
                "type": "string",
                "description": (
                    "Runnable example (literal command or invocation), with "
                    "placeholders for inputs. E.g. 'python -c \"... screenshot(URL, OUT)\"'."
                ),
            },
            "input_schema": {
                "type": "object",
                "description": "JSON schema describing the tool's inputs.",
            },
            "install_commands": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The bash commands you ran to install this tool. Recorded "
                    "for posterity and so future drones can re-install if needed."
                ),
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of other tools this one needs available.",
            },
        },
        "required": ["name", "description", "usage"],
    },
)
def cm_register_tool(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    name = str(args.get("name", "")).strip()
    if not name:
        return ToolResult(content="ERROR: tool name required")
    schema = args.get("input_schema") or {"type": "object", "properties": {}}
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    try:
        record = Tool(
            name=name,
            description=str(args.get("description", "")),
            input_schema_json=json.dumps(schema),
            kind=ToolKind.installed,
            usage=str(args.get("usage", "")),
            install_commands=[str(c) for c in (args.get("install_commands") or [])],
            depends_on=[str(d) for d in (args.get("depends_on") or [])],
            installed_by_drone_id=ctx.drone_id,
        )
        ctx.tool_store.register_installed(record)
    except (ValueError, KeyError, TypeError) as e:
        return ToolResult(content=f"cm_register_tool error: {type(e).__name__}: {e}")
    if ctx.tape is not None:
        ctx.tape.emit(
            "tool.register",
            drone_id=ctx.drone_id,
            name=name,
            kind="installed",
        )
    return ToolResult(
        content=f"registered tool {name!r}. Future drones can discover it via cm_list_tools.",
    )


@register_tool(
    "cm_request_tool",
    (
        "Pull a tool from the registry into your active tool set so you can "
        "use it on the next turn. Use this when (a) the gap suggested it, "
        "(b) cm_list_tools shows you a tool you need, or (c) you've just "
        "registered a new tool with cm_register_tool. The tool must already "
        "exist in the registry."
    ),
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Tool name to activate."},
        },
        "required": ["name"],
    },
)
def cm_request_tool(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    name = str(args.get("name", "")).strip()
    if not name:
        return ToolResult(content="ERROR: tool name required")
    if name in ctx.active_tool_names:
        return ToolResult(content=f"tool {name!r} is already active")
    # Check that it's known (in graph or in builtin registry).
    record = ctx.tool_store.get(name)
    if record is None and get_builtin(name) is None:
        return ToolResult(
            content=(
                f"tool {name!r} not found in registry. Use cm_list_tools to "
                f"see what's available, or cm_register_tool to add a new one."
            )
        )
    if record is not None and record.kind == ToolKind.installed and not record.usage:
        return ToolResult(
            content=(
                f"tool {name!r} is registered but has no usage string — it is "
                f"documentation only. Use cm_get_tool to read its install_commands "
                f"and invoke via terminal_run yourself."
            )
        )
    ctx.active_tool_names.add(name)
    return ToolResult(
        content=(
            f"activated {name!r}. Available on next turn. (Note: installed-kind "
            f"tools are documentation; their schema may not be Anthropic-callable. "
            f"Read cm_get_tool for the usage example and invoke via terminal_run.)"
        )
    )
