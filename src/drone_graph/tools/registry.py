"""In-process registry of builtin tool implementations.

The graph holds the *metadata* (Tool nodes); this registry holds the
*dispatchers* — the Python callables that actually execute a builtin tool when
a drone uses it. ``mirror_builtins_to_graph`` syncs the metadata side at
substrate init so ``cm_list_tools`` returns the full set uniformly.

Design notes:

- Tools register via ``@register_tool(name, description, input_schema)``.
- Dispatcher signature: ``(input: dict, ctx: DroneContext) -> str``.
  The string return is the literal tool-result content sent back to the model.
- Universal query tools, structural verbs, and emergent worker tools all live
  here. The drone runtime computes the *visible* set per-gap based on the gap's
  ``tool_loadout`` and the universal-availability rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from drone_graph.tools.records import Tool, ToolKind


@dataclass
class BuiltinTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    dispatch: Callable[..., "ToolResult"]
    universal_query: bool = False
    """If True, available to every drone regardless of gap loadout (read-only
    substrate inspection: cm_get_gap, cm_list_gaps, cm_findings, etc)."""


@dataclass
class ToolResult:
    """What a tool dispatcher returns.

    ``content`` is the literal string sent back to the model as a ``tool_result``.
    ``terminal_finding`` and ``outcome`` are non-None only for ``cm_write_finding``
    when it writes a fill or fail — they tell the runtime to terminate.
    """

    content: str
    terminal_finding: Any = None  # Finding | None
    outcome: str | None = None  # "fill" | "fail" | None


@dataclass
class DroneContext:
    """State a tool dispatcher needs. The runtime owns + mutates this."""

    gap_id: str
    drone_id: str
    tick: int
    store: Any  # GapStore — typed Any to dodge cycles
    tool_store: Any  # ToolStore
    terminal_box: Any | None = None  # _TerminalBox | None (None for non-terminal drones)
    tape: Any | None = None
    # Mutable: cm_request_tool adds names; runtime re-renders tool defs each turn.
    active_tool_names: set[str] = field(default_factory=set)
    suggested_tool_names: set[str] = field(default_factory=set)
    """Tools the gap suggests but didn't preload — drone can pull these in
    via ``cm_request_tool``."""


_REGISTRY: dict[str, BuiltinTool] = {}


def register_tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    *,
    universal_query: bool = False,
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Decorator: add a builtin tool to the registry.

    The decorated function becomes the tool's dispatcher.
    """

    def deco(fn: Callable[..., str]) -> Callable[..., str]:
        if name in _REGISTRY:
            raise ValueError(f"tool {name!r} already registered")
        _REGISTRY[name] = BuiltinTool(
            name=name,
            description=description,
            input_schema=input_schema,
            dispatch=fn,
            universal_query=universal_query,
        )
        return fn

    return deco


def get_builtin(name: str) -> BuiltinTool | None:
    return _REGISTRY.get(name)


def list_builtins() -> list[BuiltinTool]:
    return list(_REGISTRY.values())


def universal_query_tool_names() -> list[str]:
    """Tools every drone gets regardless of gap loadout."""
    return [t.name for t in _REGISTRY.values() if t.universal_query]


def to_anthropic_tool_def(name: str) -> dict[str, Any] | None:
    t = _REGISTRY.get(name)
    if t is None:
        return None
    return {
        "name": t.name,
        "description": t.description,
        "input_schema": t.input_schema,
    }


def builtin_to_record(name: str) -> Tool | None:
    """Render a registered builtin as a graph-side ``Tool`` record."""
    t = _REGISTRY.get(name)
    if t is None:
        return None
    return Tool(
        name=t.name,
        description=t.description,
        input_schema_json=json.dumps(t.input_schema),
        kind=ToolKind.builtin,
    )
