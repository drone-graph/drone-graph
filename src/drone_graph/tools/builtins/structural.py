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
                        "max_output_tokens": {
                            "type": "integer",
                            "description": (
                                "Per-turn output-token cap for the worker. "
                                "Omit for the tier default (nano/mini 2048, "
                                "standard 4096, advanced 8192, frontier "
                                "16384). Drop lower (1024) for narrow "
                                "click-through gaps; raise for synthesis "
                                "gaps. A drone that hits the cap 3 turns in "
                                "a row is auto-exited as runaway."
                            ),
                        },
                        "max_worker_turns": {
                            "type": "integer",
                            "description": (
                                "Override the global worker max_turns (20) "
                                "for this child. Set higher (e.g. 60) for "
                                "complex multi-step gaps like account "
                                "creation that need room for skill install, "
                                "browser navigation, form fills, and "
                                "verification. Omit for the default."
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
        "top-level and unrelated to the existing tree. Set ``model_tier`` "
        "based on expected difficulty so the scheduler routes workers to the "
        "right cost/capability point: ``nano`` for trivial mechanical tasks "
        "(renames, format conversions), ``mini`` for cheap reasoning, "
        "``standard`` for typical work (default — pick when unsure), "
        "``advanced`` for harder multi-step reasoning, ``frontier`` for "
        "open-ended research / deep refactors / novel design. Tier maps to a "
        "concrete model via the registry's tier_defaults_by_provider (and "
        "operator overrides in Settings)."
    ),
    {
        "type": "object",
        "properties": {
            "intent": {"type": "string"},
            "criteria": {"type": "string"},
            "rationale": {"type": "string"},
            "model_tier": {
                "type": "string",
                "enum": ["nano", "mini", "standard", "advanced", "frontier"],
                "description": (
                    "Difficulty tier. Workers spawned against this gap will "
                    "use the registry's tier_defaults_by_provider[provider]"
                    "[model_tier] (or Settings override). Defaults to standard."
                ),
            },
            "tool_loadout": {"type": "array", "items": {"type": "string"}},
            "tool_suggestions": {"type": "array", "items": {"type": "string"}},
            "max_output_tokens": {
                "type": "integer",
                "description": (
                    "Per-turn output cap. Omit for tier default. Lower "
                    "for narrow gaps; raise for synthesis."
                ),
            },
            "max_worker_turns": {
                "type": "integer",
                "description": (
                    "Override the global 20-turn worker default for complex "
                    "multi-step gaps (e.g. account creation) that need room "
                    "to install skills, run browsers, and handle errors "
                    "without hitting the turn cap prematurely."
                ),
            },
        },
        "required": ["intent", "criteria", "rationale"],
    },
)
def create(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    from drone_graph.gaps.records import ModelTier

    tier = ModelTier.standard
    raw_tier = str(args.get("model_tier", "")).strip().lower()
    if raw_tier:
        try:
            tier = ModelTier(raw_tier)
        except ValueError:
            return ToolResult(
                content=(
                    f"create error: invalid model_tier {raw_tier!r}; "
                    f"expected one of nano | mini | standard | advanced | frontier"
                )
            )
    try:
        f = ctx.store.apply_create(
            intent=str(args["intent"]),
            criteria=str(args["criteria"]),
            rationale=str(args["rationale"]),
            tick=ctx.tick,
            tier=tier,
            tool_loadout=list(args.get("tool_loadout") or []) or None,
            tool_suggestions=list(args.get("tool_suggestions") or []) or None,
            max_output_tokens=(
                int(args["max_output_tokens"])
                if args.get("max_output_tokens") not in (None, "", 0)
                else None
            ),
            max_worker_turns=(
                int(args["max_worker_turns"])
                if args.get("max_worker_turns") not in (None, "", 0)
                else None
            ),
        )
    except (ValueError, KeyError, TypeError) as e:
        return _err_result("create", e)
    return ToolResult(content=f"created: {f.id} (tier={tier.value})")


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
