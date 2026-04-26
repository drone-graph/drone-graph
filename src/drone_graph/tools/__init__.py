"""Tool registry — graph-side metadata + Python-side dispatch.

Importing this package eagerly loads all builtin tools so they are available
in the registry before any drone runs.
"""

from drone_graph.tools import builtins  # noqa: F401  (eager import for side effects)
from drone_graph.tools.records import Tool, ToolKind, empty_input_schema
from drone_graph.tools.registry import (
    BuiltinTool,
    DroneContext,
    ToolResult,
    builtin_to_record,
    get_builtin,
    list_builtins,
    register_tool,
    to_anthropic_tool_def,
    universal_query_tool_names,
)
from drone_graph.tools.store import ToolStore

__all__ = [
    "BuiltinTool",
    "DroneContext",
    "Tool",
    "ToolKind",
    "ToolResult",
    "ToolStore",
    "builtin_to_record",
    "empty_input_schema",
    "get_builtin",
    "list_builtins",
    "register_tool",
    "to_anthropic_tool_def",
    "universal_query_tool_names",
]


def mirror_builtins_to_graph(tool_store: ToolStore) -> int:
    """Upsert every registered builtin into the graph as a ``Tool`` node.

    Idempotent — call at substrate init. Returns the number of builtins synced.
    """
    n = 0
    for b in list_builtins():
        record = builtin_to_record(b.name)
        if record is None:
            continue
        tool_store.upsert_builtin(record)
        n += 1
    return n
