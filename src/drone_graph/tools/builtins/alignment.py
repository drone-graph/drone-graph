"""Alignment's only tool: write_alignment_finding.

Locked to drones working the ``alignment`` preset gap. Alignment never edits
structure — its finding is the only output. May be called multiple times in a
turn to surface concurrent issues; the runtime treats each call as one
batched observation.
"""

from __future__ import annotations

from typing import Any

from drone_graph.gaps import FindingAuthor, FindingKind
from drone_graph.tools.registry import DroneContext, ToolResult, register_tool

_KIND_PREFIX = "alignment_"
_VALID_KINDS = {"invalidated_premise", "unmet_intent", "missing_subtree", "no_issue"}


@register_tool(
    "write_alignment_finding",
    (
        "Write one Alignment finding describing what you observed. Call this "
        "tool once per concurrent issue you see (up to a small handful per "
        "turn) — e.g. one subtree with invalidated_premise AND another with "
        "missing_subtree. If the tree is sound, write a single 'no_issue'. "
        "Do not fabricate issues to fill the batch. You never edit structure; "
        "Gap Finding reads your findings on a later pass."
    ),
    {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": [
                    "invalidated_premise",
                    "unmet_intent",
                    "missing_subtree",
                    "no_issue",
                ],
            },
            "summary": {
                "type": "string",
                "description": "One tight sentence describing what you saw.",
            },
            "affected_gap_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Gap ids your finding refers to. Empty for no_issue.",
            },
        },
        "required": ["kind", "summary", "affected_gap_ids"],
    },
)
def write_alignment_finding(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    kind_short = str(args.get("kind", ""))
    if kind_short not in _VALID_KINDS:
        return ToolResult(content=f"invalid kind {kind_short!r}")
    try:
        kind_enum = FindingKind(_KIND_PREFIX + kind_short)
    except ValueError:
        return ToolResult(content=f"unknown alignment kind {kind_short!r}")
    try:
        f = ctx.store.append_finding(
            tick=ctx.tick,
            author=FindingAuthor.alignment,
            kind=kind_enum,
            summary=str(args.get("summary", "")),
            affected_gap_ids=list(args.get("affected_gap_ids", []) or []),
        )
    except (ValueError, KeyError, TypeError) as e:
        return ToolResult(content=f"write_alignment_finding error: {type(e).__name__}: {e}")
    return ToolResult(content=f"alignment finding recorded: {f.id} ({kind_short})")
