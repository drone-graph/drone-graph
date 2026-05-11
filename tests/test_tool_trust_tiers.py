"""Trust tiers: autoload high-tier suggestions; cm_request_tool gating for low/blocked."""

from __future__ import annotations

import json

from drone_graph.drones.providers import ChatResponse, Provider, Usage
from drone_graph.drones.runtime import run_drone
from drone_graph.gaps import GapStore
from drone_graph.tools import Tool, ToolKind, TrustTier
from drone_graph.tools.builtins.worker import cm_request_tool
from drone_graph.tools.registry import DroneContext
from drone_graph.tools.store import ToolStore


class _CaptureToolsClient:
    provider = Provider.anthropic
    model = "claude-test"

    def __init__(self) -> None:
        self.first_turn_tools: list[dict[str, object]] | None = None

    def chat(
        self,
        system: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        max_tokens: int | None = None,
    ) -> ChatResponse:
        if self.first_turn_tools is None:
            self.first_turn_tools = list(tools)
        return ChatResponse(
            text="",
            tool_calls=[],
            raw_assistant_content=[],
            usage=Usage(tokens_in=0, tokens_out=0),
        )


def test_autoload_high_tier_builtin_from_suggestions(substrate) -> None:
    """``decompose`` is builtin (high) but not in default emergent loadout."""
    gap_store = GapStore(substrate)
    tool_store = ToolStore(substrate)
    gap = gap_store.create_root(
        intent="x",
        criteria="y",
        tool_suggestions=["decompose"],
    )
    client = _CaptureToolsClient()
    run_drone(
        gap,
        store=gap_store,
        tool_store=tool_store,
        client=client,
        tick=1,
        max_turns=1,
    )
    assert client.first_turn_tools is not None
    names = {t["name"] for t in client.first_turn_tools}
    assert "decompose" in names


def test_cm_request_tool_low_blocked_without_suggestion(substrate) -> None:
    gap_store = GapStore(substrate)
    tool_store = ToolStore(substrate)
    schema = json.dumps({"type": "object", "properties": {}})
    tool_store.register_installed(
        Tool(
            name="low_only_tool",
            description="low trust demo",
            input_schema_json=schema,
            kind=ToolKind.installed,
            usage="echo low",
            installed_by_drone_id="test-drone",
            trust_tier=TrustTier.low,
        )
    )
    gap = gap_store.create_root(intent="a", criteria="b", tool_suggestions=[])
    ctx = DroneContext(
        gap_id=gap.id,
        drone_id="d1",
        tick=1,
        store=gap_store,
        tool_store=tool_store,
        active_tool_names={"cm_get_gap"},
        suggested_tool_names=set(),
    )
    r = cm_request_tool({"name": "low_only_tool"}, ctx)
    assert "ERROR:" in r.content
    assert "low_only_tool" not in ctx.active_tool_names


def test_cm_request_tool_low_allowed_when_suggested(substrate) -> None:
    gap_store = GapStore(substrate)
    tool_store = ToolStore(substrate)
    schema = json.dumps({"type": "object", "properties": {}})
    tool_store.register_installed(
        Tool(
            name="low_suggested_tool",
            description="low trust demo",
            input_schema_json=schema,
            kind=ToolKind.installed,
            usage="echo low",
            installed_by_drone_id="test-drone",
            trust_tier=TrustTier.low,
        )
    )
    gap = gap_store.create_root(
        intent="a",
        criteria="b",
        tool_suggestions=["low_suggested_tool"],
    )
    ctx = DroneContext(
        gap_id=gap.id,
        drone_id="d1",
        tick=1,
        store=gap_store,
        tool_store=tool_store,
        active_tool_names={"cm_get_gap"},
        suggested_tool_names=set(gap.tool_suggestions),
    )
    r = cm_request_tool({"name": "low_suggested_tool"}, ctx)
    assert "ERROR:" not in r.content
    assert "low_suggested_tool" in ctx.active_tool_names


def test_cm_request_tool_blocked_even_when_suggested(substrate) -> None:
    gap_store = GapStore(substrate)
    tool_store = ToolStore(substrate)
    schema = json.dumps({"type": "object", "properties": {}})
    tool_store.register_installed(
        Tool(
            name="blocked_tool",
            description="blocked demo",
            input_schema_json=schema,
            kind=ToolKind.installed,
            usage="echo blocked",
            installed_by_drone_id="test-drone",
            trust_tier=TrustTier.blocked,
        )
    )
    gap = gap_store.create_root(
        intent="a",
        criteria="b",
        tool_suggestions=["blocked_tool"],
    )
    ctx = DroneContext(
        gap_id=gap.id,
        drone_id="d1",
        tick=1,
        store=gap_store,
        tool_store=tool_store,
        active_tool_names={"cm_get_gap"},
        suggested_tool_names=set(gap.tool_suggestions),
    )
    r = cm_request_tool({"name": "blocked_tool"}, ctx)
    assert "ERROR:" in r.content
    assert "blocked" in r.content.lower()
    assert "blocked_tool" not in ctx.active_tool_names
