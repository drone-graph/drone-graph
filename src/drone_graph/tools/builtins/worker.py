"""Worker tools — terminal, gap-read, finding-write, and runtime tool registration.

These are the default-emergent loadout: every drone working a non-preset gap
gets these unless the gap explicitly restricts them.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import time
from pathlib import Path
from typing import Any

from drone_graph.gaps.records import FindingAuthor, FindingKind
from drone_graph.skills_marketplace.skill_packages.parse import load_skill_package
from drone_graph.skills_marketplace.skill_packages.paths import (
    SKILL_ROOT_ENV,
    resolve_skill_package_path,
)
from drone_graph.skills_marketplace.skill_packages.records import SkillPackageError
from drone_graph.terminal import (
    TerminalDead,
    TerminalTimeout,
    resolve_venv_activate_script,
)
from drone_graph.tools.records import Tool, ToolKind, TrustTier
from drone_graph.tools.registry import (
    DroneContext,
    ToolResult,
    get_builtin,
    register_tool,
)
from drone_graph.tools.trust import effective_trust

DEFAULT_COMMAND_TIMEOUT_S = 60.0


def _parse_trust_tier_arg(args: dict[str, Any]) -> TrustTier | str:
    raw = args.get("trust_tier")
    if raw is None or raw == "":
        return TrustTier.standard
    s = str(raw).strip().lower()
    try:
        return TrustTier(s)
    except ValueError:
        valid = ", ".join(sorted(TrustTier.__members__))
        return f"trust_tier must be one of {valid}; got {raw!r}"


def _skill_link_from_register_args(
    args: dict[str, Any],
) -> tuple[str | None, str | None] | str:
    """Resolve optional skill package linkage for ``cm_register_tool``.

    Returns ``(skill_package_path, skill_package_id)`` as normalized strings,
    or an error message string on validation failure.
    """
    raw_path = args.get("skill_package_path")
    raw_id = args.get("skill_package_id")
    path_in = str(raw_path).strip() if raw_path not in (None, "") else ""
    id_in = str(raw_id).strip() if raw_id not in (None, "") else ""

    if not path_in and not id_in:
        return None, None

    if path_in:
        resolved = resolve_skill_package_path(path_in).resolve()
        try:
            pkg = load_skill_package(resolved)
        except SkillPackageError as e:
            return f"skill package validation failed: {e!s}"
        sid = id_in if id_in else pkg.skill_id
        return str(resolved), sid

    root = os.environ.get(SKILL_ROOT_ENV)
    if not root:
        return (
            "skill_package_id without skill_package_path requires "
            f"{SKILL_ROOT_ENV} to be set"
        )
    resolved = (Path(root) / id_in).resolve()
    try:
        pkg = load_skill_package(resolved)
    except SkillPackageError as e:
        return f"skill package validation failed: {e!s}"
    sid = id_in if id_in else pkg.skill_id
    return str(resolved), sid


def _record_terminal_skill_invocation(
    ctx: DroneContext,
    *,
    invocation_tool_name: str,
    outcome: str,
    summary: str,
    metrics_obj: dict[str, Any],
) -> None:
    ctx.store.append_finding(
        tick=ctx.tick,
        author=FindingAuthor.worker,
        kind=FindingKind.skill_invocation,
        summary=summary,
        affected_gap_ids=[ctx.gap_id],
        invocation_tool_name=invocation_tool_name,
        invocation_outcome=outcome,
        invocation_metrics_json=json.dumps(metrics_obj),
    )
    with contextlib.suppress(Exception):
        ctx.tool_store.record_usage(invocation_tool_name, ctx.gap_id)


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
            "invocation_tool_name": {
                "type": "string",
                "description": (
                    "If set, records a skill_invocation finding for this "
                    "installed tool (:Tool.name, kind=installed) after the "
                    "command finishes. Omit for ad-hoc shell work."
                ),
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
    raw_tool = args.get("invocation_tool_name")
    invocation_tool_name = (
        str(raw_tool).strip() if raw_tool not in (None, "") else None
    )
    if ctx.tape is not None:
        ctx.tape.emit(
            "tool.terminal_run",
            drone_id=ctx.drone_id,
            cmd=cmd,
            timeout_s=timeout,
            invocation_tool_name=invocation_tool_name,
        )
    tool_rec: Tool | None = None
    if invocation_tool_name is not None:
        if ctx.store is None or ctx.tool_store is None:
            return ToolResult(
                content=(
                    "ERROR: invocation_tool_name requires GapStore and ToolStore "
                    "(skill invocation recording)."
                )
            )
        tool_rec = ctx.tool_store.get(invocation_tool_name)
        if tool_rec is None:
            return ToolResult(
                content=(
                    f"ERROR: no tool named {invocation_tool_name!r} "
                    "(skill invocation requires an existing registered tool)."
                )
            )
        if tool_rec.kind is not ToolKind.installed:
            return ToolResult(
                content=(
                    f"ERROR: tool {invocation_tool_name!r} must be kind=installed "
                    "to record a skill_invocation."
                )
            )
    if not cmd.strip():
        return ToolResult(
            content="ERROR: empty command rejected. Pass a non-empty bash command."
        )
    effective_cmd = cmd
    if tool_rec is not None and tool_rec.needs_venv:
        activate, venv_err = resolve_venv_activate_script()
        if venv_err is not None or activate is None:
            return ToolResult(
                content=f"ERROR: {venv_err or 'venv activation unavailable'}",
            )
        effective_cmd = f"source {shlex.quote(str(activate))} && {cmd}"
    try:
        t0 = time.monotonic()
        r = ctx.terminal_box.get().run(effective_cmd, timeout=timeout)
        duration_ms = int((time.monotonic() - t0) * 1000)
    except TerminalTimeout as e:
        if invocation_tool_name is not None:
            assert ctx.store is not None and ctx.tool_store is not None
            _record_terminal_skill_invocation(
                ctx,
                invocation_tool_name=invocation_tool_name,
                outcome="failure",
                summary=(
                    f"skill_invocation {invocation_tool_name} outcome=failure "
                    "(timeout)"
                ),
                metrics_obj={"timeout": True},
            )
            return ToolResult(content=f"TIMEOUT: {e}", extra_findings_written=1)
        return ToolResult(content=f"TIMEOUT: {e}")
    except TerminalDead as e:
        ctx.terminal_box.respawn()
        if ctx.tape is not None:
            ctx.tape.emit(
                "tool.terminal_respawn", drone_id=ctx.drone_id, reason=str(e)
            )
        if invocation_tool_name is not None:
            assert ctx.store is not None and ctx.tool_store is not None
            _record_terminal_skill_invocation(
                ctx,
                invocation_tool_name=invocation_tool_name,
                outcome="failure",
                summary=(
                    f"skill_invocation {invocation_tool_name} "
                    "outcome=failure (terminal_dead)"
                ),
                metrics_obj={"terminal_dead": True},
            )
            return ToolResult(
                content=(
                    f"ERROR: terminal died ({e}); a fresh shell has been started. "
                    f"Previous shell state (cwd, env, unsaved variables) is gone. Retry."
                ),
                extra_findings_written=1,
            )
        return ToolResult(
            content=(
                f"ERROR: terminal died ({e}); a fresh shell has been started. "
                f"Previous shell state (cwd, env, unsaved variables) is gone. Retry."
            )
        )
    payload = {"stdout": r.stdout, "stderr": r.stderr, "exit_code": r.exit_code}
    content = json.dumps(payload)
    if invocation_tool_name is None:
        return ToolResult(content=content)
    assert ctx.store is not None and ctx.tool_store is not None
    outcome = "success" if r.exit_code == 0 else "failure"
    summary = f"skill_invocation {invocation_tool_name} outcome={outcome}"
    metrics_obj: dict[str, Any] = {
        "exit_code": r.exit_code,
        "duration_ms": duration_ms,
    }
    _record_terminal_skill_invocation(
        ctx,
        invocation_tool_name=invocation_tool_name,
        outcome=outcome,
        summary=summary,
        metrics_obj=metrics_obj,
    )
    return ToolResult(content=content, extra_findings_written=1)


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
            "skill_package_path": {
                "type": "string",
                "description": (
                    "Optional directory containing SKILL.md (absolute or relative "
                    "to DRONE_GRAPH_SKILL_ROOT / cwd). Validated before registration."
                ),
            },
            "skill_package_id": {
                "type": "string",
                "description": (
                    "Optional skill id; if omitted with skill_package_path, derived "
                    f"from the package. Id-only registration requires {SKILL_ROOT_ENV}."
                ),
            },
            "needs_venv": {
                "type": "boolean",
                "description": (
                    "If true, terminal_run activates DRONE_GRAPH_WORKSPACE/.venv "
                    "before the command when invocation_tool_name matches this tool."
                ),
            },
            "trust_tier": {
                "type": "string",
                "description": (
                    "Optional trust tier: high | standard | low | blocked. "
                    "Defaults to standard for new installed tools."
                ),
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
    trust_or_err = _parse_trust_tier_arg(args)
    if not isinstance(trust_or_err, TrustTier):
        return ToolResult(content=f"ERROR: {trust_or_err}")
    link = _skill_link_from_register_args(args)
    if isinstance(link, str):
        return ToolResult(content=f"ERROR: {link}")
    skill_path, skill_id = link
    nv_raw = args.get("needs_venv", False)
    needs_venv = nv_raw if isinstance(nv_raw, bool) else False
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
            skill_package_path=skill_path,
            skill_package_id=skill_id,
            needs_venv=needs_venv,
            trust_tier=trust_or_err,
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
        "exist in the registry. "
        "**Low-trust** installed tools can only be activated when this gap's "
        "**tool_suggestions** includes the tool name. **Blocked** tools cannot "
        "be activated. High-trust builtins suggested by the gap may already "
        "be active without calling this tool."
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
    tier = effective_trust(name, ctx.tool_store)
    if tier is None:
        return ToolResult(
            content=(
                f"tool {name!r} not found in registry. Use cm_list_tools to "
                f"see what's available, or cm_register_tool to add a new one."
            )
        )
    if tier is TrustTier.blocked:
        return ToolResult(
            content=(
                f"ERROR: tool {name!r} is blocked and cannot be activated."
            )
        )
    if tier is TrustTier.low:
        gap = ctx.store.get(ctx.gap_id)
        if gap is None:
            return ToolResult(
                content=f"ERROR: gap {ctx.gap_id!r} not found; cannot verify suggestions."
            )
        suggested = {str(s).strip() for s in (gap.tool_suggestions or []) if str(s).strip()}
        if name not in suggested:
            return ToolResult(
                content=(
                    f"ERROR: tool {name!r} is low-trust and was not listed in "
                    f"this gap's tool_suggestions — ask Gap Finding to suggest it, "
                    f"or use a different tool."
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
