"""Gap Finding's structural tools.

Locked to drones working the ``gap_finding`` preset gap — these are the only
tools that mutate the gap tree. Each maps to a ``GapStore.apply_*`` method.
Errors propagate as tool results so a single bad edit doesn't kill the drone.
"""

from __future__ import annotations

from typing import Any

from drone_graph.tools.registry import DroneContext, ToolResult, register_tool


def _err_result(name: str, e: Exception) -> ToolResult:
    return ToolResult(content=f"{name} error: {type(e).__name__}: {e}")


@register_tool(
    "decompose",
    (
        "Attach children to an unfilled gap whose intent cannot be filled in "
        "one pass. Children's intents are fragments of the parent's — together "
        "they must fulfill the parent. Decomposition is additive: if the parent "
        "already has active children, new children are appended (duplicate "
        "intents are silently dropped). Use to add missing work — e.g. a "
        "consolidation leaf — to an already-decomposed gap rather than creating "
        "a top-level sibling."
    ),
    {
        "type": "object",
        "properties": {
            "parent_id": {"type": "string"},
            "children": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "intent": {"type": "string"},
                        "criteria": {"type": "string"},
                        "tool_loadout": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional explicit tool loadout for the child. "
                                "Empty/omitted = default emergent loadout."
                            ),
                        },
                        "tool_suggestions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional suggested tools the drone can pull in "
                                "with cm_request_tool."
                            ),
                        },
                    },
                    "required": ["intent", "criteria"],
                },
            },
            "rationale": {"type": "string"},
        },
        "required": ["parent_id", "children", "rationale"],
    },
)
def decompose(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    try:
        f = ctx.store.apply_decompose(
            parent_id=str(args["parent_id"]),
            children=list(args["children"]),
            rationale=str(args["rationale"]),
            tick=ctx.tick,
        )
    except (ValueError, KeyError, TypeError) as e:
        return _err_result("decompose", e)
    return ToolResult(content=f"decomposed: {f.id}")


@register_tool(
    "create",
    (
        "Add a new top-level gap in response to a finding that implies "
        "adjacent work — a user_input redefining scope, an alignment "
        "missing_subtree flag, or a worker fail suggesting unrelated work. "
        "Prefer 'decompose' when the new work is conceptually a child of an "
        "existing gap; use 'create' only when the new work is genuinely "
        "top-level and unrelated to the existing tree."
    ),
    {
        "type": "object",
        "properties": {
            "intent": {"type": "string"},
            "criteria": {"type": "string"},
            "rationale": {"type": "string"},
            "tool_loadout": {"type": "array", "items": {"type": "string"}},
            "tool_suggestions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["intent", "criteria", "rationale"],
    },
)
def create(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    try:
        f = ctx.store.apply_create(
            intent=str(args["intent"]),
            criteria=str(args["criteria"]),
            rationale=str(args["rationale"]),
            tick=ctx.tick,
        )
    except (ValueError, KeyError, TypeError) as e:
        return _err_result("create", e)
    return ToolResult(content=f"created: {f.id}")


@register_tool(
    "retire",
    (
        "Close off an unfilled subtree whose premise has been invalidated. "
        "The subtree stays in the graph with findings intact; only new claims "
        "are blocked. Use this on the root when a user pivot reframes the "
        "whole project — the old root + descendants stay as historical record, "
        "and a new top-level gap (or rewrite_intent) takes the live direction."
    ),
    {
        "type": "object",
        "properties": {
            "gap_id": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["gap_id", "reason"],
    },
)
def retire(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    try:
        f = ctx.store.apply_retire(
            gap_id=str(args["gap_id"]),
            reason=str(args["reason"]),
            tick=ctx.tick,
        )
    except (ValueError, KeyError, TypeError) as e:
        return _err_result("retire", e)
    return ToolResult(content=f"retired: {f.id}")


@register_tool(
    "reopen",
    (
        "Mark a filled gap unfilled when findings show its intent wasn't "
        "actually met. Children are preserved. Only works on filled gaps."
    ),
    {
        "type": "object",
        "properties": {
            "gap_id": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["gap_id", "reason"],
    },
)
def reopen(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    try:
        f = ctx.store.apply_reopen(
            gap_id=str(args["gap_id"]),
            reason=str(args["reason"]),
            tick=ctx.tick,
        )
    except (ValueError, KeyError, TypeError) as e:
        return _err_result("reopen", e)
    return ToolResult(content=f"reopened: {f.id}")


@register_tool(
    "rewrite_intent",
    (
        "Rewrite an unfilled gap's intent and criteria in place. Use ONLY "
        "when a prior signal (user_input or alignment_invalidated_premise) "
        "explicitly reframes the gap and the existing descendants remain "
        "coherent under the new intent. If most descendants would be "
        "invalidated, retire + create new is cleaner."
    ),
    {
        "type": "object",
        "properties": {
            "gap_id": {"type": "string"},
            "new_intent": {"type": "string"},
            "new_criteria": {"type": "string"},
            "rationale": {
                "type": "string",
                "description": (
                    "One to two sentences naming the signal that makes the "
                    "rewrite necessary."
                ),
            },
        },
        "required": ["gap_id", "new_intent", "new_criteria", "rationale"],
    },
)
def rewrite_intent(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    try:
        f = ctx.store.apply_rewrite_intent(
            gap_id=str(args["gap_id"]),
            new_intent=str(args["new_intent"]),
            new_criteria=str(args["new_criteria"]),
            rationale=str(args["rationale"]),
            tick=ctx.tick,
        )
    except (ValueError, KeyError, TypeError) as e:
        return _err_result("rewrite_intent", e)
    return ToolResult(content=f"rewrote intent: {f.id}")


@register_tool(
    "noop",
    (
        "Use when the leaf buffer is at target AND no finding warrants "
        "create / retire / reopen / rewrite_intent. Prefer a meaningful edit "
        "when any reasonable one is available."
    ),
    {
        "type": "object",
        "properties": {"rationale": {"type": "string"}},
        "required": ["rationale"],
    },
)
def noop(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    try:
        f = ctx.store.apply_noop(rationale=str(args["rationale"]), tick=ctx.tick)
    except (ValueError, KeyError, TypeError) as e:
        return _err_result("noop", e)
    return ToolResult(content=f"noop: {f.id}")
