"""Operator hooks for tool registry hygiene (soft deprecation, stale pruning)."""

from __future__ import annotations

import json
from typing import Any

from drone_graph.tools.registry import DroneContext, ToolResult, register_tool


@register_tool(
    "cm_deprecate_stale_tools",
    (
        "Soft-deprecate installed tools that are alignment-flagged or unused "
        "longer than max_age_days. Does not delete graph nodes; clears embeddings "
        "sidecar rows when configured. Use dry_run to preview."
    ),
    {
        "type": "object",
        "properties": {
            "max_age_days": {
                "type": "number",
                "description": (
                    "Installed tools with last_used_at older than this window are stale."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": "If true, report candidates without writing deprecated_at.",
            },
            "deprecate_flagged": {
                "type": "boolean",
                "description": (
                    "If true (default), deprecate alignment-flagged installed tools too."
                ),
            },
        },
        "required": [],
    },
)
def cm_deprecate_stale_tools(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    raw_age = args.get("max_age_days", 90)
    try:
        max_age_days = float(raw_age)
    except (TypeError, ValueError):
        return ToolResult(content="ERROR: max_age_days must be a number")
    if max_age_days < 0:
        return ToolResult(content="ERROR: max_age_days must be non-negative")
    raw_dry = args.get("dry_run", False)
    dry_run = raw_dry if isinstance(raw_dry, bool) else False
    raw_flag = args.get("deprecate_flagged", True)
    deprecate_flagged = raw_flag if isinstance(raw_flag, bool) else True
    report = ctx.tool_store.deprecate_stale_installed_tools(
        max_age_days=max_age_days,
        deprecate_flagged=deprecate_flagged,
        dry_run=dry_run,
    )
    return ToolResult(content=json.dumps(report))
