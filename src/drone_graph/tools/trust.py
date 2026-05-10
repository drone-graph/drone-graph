"""Resolve effective trust tier for a tool name (builtins vs graph)."""

from __future__ import annotations

from drone_graph.tools.records import TrustTier
from drone_graph.tools.registry import get_builtin
from drone_graph.tools.store import ToolStore


def effective_trust(name: str, tool_store: ToolStore) -> TrustTier | None:
    """Return the trust tier for ``name``, or None if the tool is unknown.

    Python builtins are always **high** (graph mirror is informational).
    """
    if get_builtin(name) is not None:
        return TrustTier.high
    rec = tool_store.get(name)
    if rec is None:
        return None
    return rec.trust_tier
