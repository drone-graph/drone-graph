"""Universal query tools — read-only substrate inspection, available to every
drone regardless of gap loadout. Replaces the pre-rendered "whole tree" view
the old role-specific runtimes used to inject; drones now pull only what they
need.
"""

from __future__ import annotations

import json
from typing import Any

from drone_graph.tools.records import TrustTier
from drone_graph.tools.registry import DroneContext, ToolResult, register_tool
from drone_graph.tools.store import is_discoverable


def _serialize_gap(g: Any) -> dict[str, Any]:
    return {
        "id": g.id,
        "intent": g.intent,
        "criteria": g.criteria,
        "status": g.status.value,
        "model_tier": g.model_tier.value,
        "reopen_count": g.reopen_count,
        "retire_reason": g.retire_reason,
        "preset_kind": g.preset_kind,
        "tool_loadout": list(g.tool_loadout),
        "tool_suggestions": list(g.tool_suggestions),
        "created_at": g.created_at.isoformat(),
    }


def _serialize_finding(f: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": f.id,
        "tick": f.tick,
        "author": f.author.value,
        "kind": f.kind.value,
        "summary": f.summary,
        "affected_gap_ids": list(f.affected_gap_ids),
        "artefact_paths": list(f.artefact_paths),
        "created_at": f.created_at.isoformat(),
    }
    if f.invocation_tool_name is not None:
        out["invocation_tool_name"] = f.invocation_tool_name
    if f.invocation_outcome is not None:
        out["invocation_outcome"] = f.invocation_outcome
    if f.invocation_provider is not None:
        out["invocation_provider"] = f.invocation_provider
    if f.invocation_model is not None:
        out["invocation_model"] = f.invocation_model
    if f.invocation_cost_usd is not None:
        out["invocation_cost_usd"] = f.invocation_cost_usd
    if f.invocation_metrics_json is not None:
        out["invocation_metrics_json"] = f.invocation_metrics_json
    return out


def _serialize_tool(t: Any) -> dict[str, Any]:
    lu = getattr(t, "last_used_at", None)
    da = getattr(t, "deprecated_at", None)
    out: dict[str, Any] = {
        "name": t.name,
        "description": t.description,
        "kind": t.kind.value,
        "usage": t.usage,
        "depends_on": list(t.depends_on),
        "flagged_by_alignment": t.flagged_by_alignment,
        "needs_venv": getattr(t, "needs_venv", False),
        "last_used_at": lu.isoformat() if lu is not None else None,
        "deprecated_at": da.isoformat() if da is not None else None,
        "deprecated_reason": getattr(t, "deprecated_reason", None),
        "trust_tier": (
            tt.value
            if (tt := getattr(t, "trust_tier", None)) is not None
            else TrustTier.standard.value
        ),
    }
    if t.skill_package_path is not None:
        out["skill_package_path"] = t.skill_package_path
    if t.skill_package_id is not None:
        out["skill_package_id"] = t.skill_package_id
    return out


@register_tool(
    "cm_get_gap",
    "Look up a single gap by id (full id or unambiguous prefix). Returns the gap's intent, criteria, status, tool loadout, and metadata.",
    {
        "type": "object",
        "properties": {
            "gap_id": {"type": "string", "description": "Gap id or unique prefix."},
        },
        "required": ["gap_id"],
    },
    universal_query=True,
)
def cm_get_gap(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    g = ctx.store.get(str(args.get("gap_id", "")))
    if g is None:
        return ToolResult(content=f"no gap with id {args.get('gap_id')!r}")
    return ToolResult(content=json.dumps(_serialize_gap(g)))


@register_tool(
    "cm_list_gaps",
    "List gaps in the substrate, oldest first. Filter by status (unfilled|filled|retired) or preset_kind. Returns one-line entries: id, status, preset_kind, intent prefix.",
    {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["unfilled", "filled", "retired"],
                "description": "Optional status filter.",
            },
            "preset_kind": {
                "type": "string",
                "description": "Filter to a preset gap (e.g. 'gap_finding'). Empty string returns only emergent (non-preset) gaps.",
            },
            "limit": {"type": "integer", "description": "Max entries; default 50."},
        },
    },
    universal_query=True,
)
def cm_list_gaps(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    status = args.get("status")
    preset_kind = args.get("preset_kind", None)
    limit = int(args.get("limit", 50))
    gaps = ctx.store.all_gaps()
    if status:
        gaps = [g for g in gaps if g.status.value == status]
    if preset_kind is not None:
        if preset_kind == "":
            gaps = [g for g in gaps if g.preset_kind is None]
        else:
            gaps = [g for g in gaps if g.preset_kind == preset_kind]
    out = [
        {
            "id": g.id,
            "status": g.status.value,
            "preset_kind": g.preset_kind,
            "intent": (g.intent[:120] + "…") if len(g.intent) > 120 else g.intent,
        }
        for g in gaps[:limit]
    ]
    return ToolResult(content=json.dumps(out))


@register_tool(
    "cm_children_of",
    "List the direct children of a gap (any status).",
    {
        "type": "object",
        "properties": {"gap_id": {"type": "string"}},
        "required": ["gap_id"],
    },
    universal_query=True,
)
def cm_children_of(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    children = ctx.store.children_of(str(args["gap_id"]))
    return ToolResult(content=json.dumps([_serialize_gap(c) for c in children]))


@register_tool(
    "cm_parent_of",
    "Look up the parent of a gap. Returns null if the gap is a root.",
    {
        "type": "object",
        "properties": {"gap_id": {"type": "string"}},
        "required": ["gap_id"],
    },
    universal_query=True,
)
def cm_parent_of(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    p = ctx.store.parent_of(str(args["gap_id"]))
    return ToolResult(content=json.dumps(_serialize_gap(p) if p is not None else None))


@register_tool(
    "cm_leaves",
    "List active leaves: unfilled gaps with no non-retired children. The pool of work waiting for emergent drones.",
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max entries; default 50."},
        },
    },
    universal_query=True,
)
def cm_leaves(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    limit = int(args.get("limit", 50))
    leaves = ctx.store.leaves()[:limit]
    return ToolResult(content=json.dumps([_serialize_gap(g) for g in leaves]))


@register_tool(
    "cm_findings",
    (
        "Recent findings, oldest first within the returned window (substrate order "
        "is tick asc). Optionally filter by author, kind, gap_id, or skill "
        "invocation fields (invocation_tool_name, invocation_outcome). Typical "
        "skill query: kind=skill_invocation plus invocation_tool_name."
    ),
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max findings; default 30."},
            "author": {
                "type": "string",
                "description": "Filter by author: gap_finding|alignment|worker|user|system",
            },
            "kind": {"type": "string", "description": "Filter by finding kind."},
            "gap_id": {
                "type": "string",
                "description": "Only findings whose affected_gap_ids include this gap.",
            },
            "invocation_tool_name": {
                "type": "string",
                "description": (
                    "Exact match on Finding.invocation_tool_name "
                    "(skill_invocation rows)."
                ),
            },
            "invocation_outcome": {
                "type": "string",
                "description": (
                    "Exact match on Finding.invocation_outcome (e.g. success, failure)."
                ),
            },
        },
    },
    universal_query=True,
)
def cm_findings(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    limit = int(args.get("limit", 30))
    author = args.get("author")
    kind = args.get("kind")
    gap_id = args.get("gap_id")
    raw_inv_tool = args.get("invocation_tool_name")
    invocation_tool_name = (
        str(raw_inv_tool).strip() if raw_inv_tool not in (None, "") else ""
    )
    raw_inv_out = args.get("invocation_outcome")
    invocation_outcome = (
        str(raw_inv_out).strip() if raw_inv_out not in (None, "") else ""
    )
    findings = ctx.store.all_findings()
    if author:
        findings = [f for f in findings if f.author.value == author]
    if kind:
        findings = [f for f in findings if f.kind.value == kind]
    if gap_id:
        findings = [f for f in findings if gap_id in f.affected_gap_ids]
    if invocation_tool_name:
        findings = [
            f for f in findings if f.invocation_tool_name == invocation_tool_name
        ]
    if invocation_outcome:
        findings = [
            f for f in findings if f.invocation_outcome == invocation_outcome
        ]
    if limit > 0:
        findings = findings[-limit:]
    return ToolResult(content=json.dumps([_serialize_finding(f) for f in findings]))


@register_tool(
    "cm_finding",
    "Look up a single finding by id (full id or unambiguous prefix). Returns full summary and any attached artefact paths.",
    {
        "type": "object",
        "properties": {"finding_id": {"type": "string"}},
        "required": ["finding_id"],
    },
    universal_query=True,
)
def cm_finding(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    needle = str(args.get("finding_id", ""))
    matches = [f for f in ctx.store.all_findings() if f.id.startswith(needle)]
    if not matches:
        return ToolResult(content=f"no finding starting with {needle!r}")
    if len(matches) > 1:
        return ToolResult(
            content=f"ambiguous prefix {needle!r}: {len(matches)} matches"
        )
    return ToolResult(content=json.dumps(_serialize_finding(matches[0])))


@register_tool(
    "cm_list_tools",
    "Search the tool registry. Use this to discover what tools are available — both builtins and tools other drones installed earlier. Optional case-insensitive substring query over name and description.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional substring to filter on; empty returns the full registry.",
            },
            "include_deprecated": {
                "type": "boolean",
                "description": (
                    "If true, include soft-deprecated installed tools. Default false."
                ),
            },
        },
    },
    universal_query=True,
)
def cm_list_tools(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    q = str(args.get("query", "") or "")
    raw_inc = args.get("include_deprecated")
    include_deprecated = raw_inc if isinstance(raw_inc, bool) else False
    tools = ctx.tool_store.search(q) if q else ctx.tool_store.all_tools()
    if not include_deprecated:
        tools = [t for t in tools if is_discoverable(t)]
    return ToolResult(content=json.dumps([_serialize_tool(t) for t in tools]))


@register_tool(
    "cm_search_tools",
    (
        "Natural-language search over tool descriptions using embedding similarity "
        "(see ToolStore embedding sidecar). For substring filtering use cm_list_tools. "
        "Returns ranked tool names and serialized tool metadata."
    ),
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What you are looking for in natural language.",
            },
            "limit": {
                "type": "integer",
                "description": "Max tools to return (default 20).",
            },
        },
        "required": ["query"],
    },
    universal_query=True,
)
def cm_search_tools(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    if not ctx.tool_store.semantic_search_configured:
        return ToolResult(
            content=(
                "Semantic tool search is not configured for this run (embeddings "
                "store + embedder on ToolStore). Use cm_list_tools for substring search."
            )
        )
    q = str(args.get("query", "")).strip()
    if not q:
        return ToolResult(content="ERROR: query required for cm_search_tools")
    raw_limit = args.get("limit")
    limit = int(raw_limit) if raw_limit is not None else 20
    names = ctx.tool_store.semantic_rank_tool_names(q, limit=limit)
    tools_out: list[dict[str, Any]] = []
    filtered_names: list[str] = []
    for name in names:
        t = ctx.tool_store.get(name)
        if t is None:
            continue
        if not is_discoverable(t):
            continue
        filtered_names.append(name)
        tools_out.append(_serialize_tool(t))
    return ToolResult(
        content=json.dumps({"ranked_names": filtered_names, "tools": tools_out})
    )


@register_tool(
    "cm_get_tool",
    "Look up a single tool by name. Returns full description, input schema, usage example, install commands, and dependencies.",
    {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
    universal_query=True,
)
def cm_get_tool(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    t = ctx.tool_store.get(str(args["name"]))
    if t is None:
        return ToolResult(content=f"no tool named {args['name']!r}")
    out = _serialize_tool(t)
    out["input_schema"] = json.loads(t.input_schema_json)
    out["install_commands"] = list(t.install_commands)
    return ToolResult(content=json.dumps(out))
