"""Skill packages referenced via context_preload appear in the initial drone payload."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from drone_graph.drones.providers import ChatResponse, Provider, ToolCall, Usage
from drone_graph.drones.runtime import run_drone
from drone_graph.gaps import GapStore
from drone_graph.orchestrator.preload import render_preloads
from drone_graph.substrate import Substrate
from drone_graph.tools.store import ToolStore

_FIXTURE_MINIMAL = (
    Path(__file__).resolve().parent / "fixtures" / "skill_packages" / "minimal"
)


@pytest.fixture
def gap_store_tool_store(substrate: Substrate) -> Iterator[tuple[GapStore, ToolStore]]:
    store = GapStore(substrate)
    tstore = ToolStore(substrate)
    yield store, tstore


def test_render_preloads_skill_package_injects_skill_body(
    gap_store_tool_store: tuple[GapStore, ToolStore],
) -> None:
    store, _ = gap_store_tool_store
    token = f"skill_package:{_FIXTURE_MINIMAL}"
    out = render_preloads(store, [token])
    assert "My Skill" in out
    assert "Do the thing." in out
    assert "## Skill:" in out
    assert "`minimal`" in out


class _FakeChatClient:
    provider = Provider.anthropic
    model = "claude-test"

    def __init__(self) -> None:
        self.captured_messages: list[list[dict[str, object]]] | None = None

    def chat(
        self,
        system: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ChatResponse:
        if self.captured_messages is None:
            self.captured_messages = [messages]
        raw = [
            {
                "type": "tool_use",
                "id": "tu_fill",
                "name": "cm_write_finding",
                "input": {"kind": "fill", "summary": "done"},
            }
        ]
        return ChatResponse(
            text="",
            tool_calls=[
                ToolCall(
                    id="tu_fill",
                    name="cm_write_finding",
                    input={"kind": "fill", "summary": "done"},
                )
            ],
            raw_assistant_content=raw,
            usage=Usage(tokens_in=1, tokens_out=2),
        )


def test_run_drone_first_user_message_contains_skill_text(
    gap_store_tool_store: tuple[GapStore, ToolStore],
) -> None:
    store, tool_store = gap_store_tool_store
    gap = store.create_root(intent="work", criteria="ok")
    gap = gap.model_copy(
        update={
            "context_preload": [f"skill_package:{_FIXTURE_MINIMAL}"],
        }
    )
    client = _FakeChatClient()
    result = run_drone(
        gap,
        store=store,
        tool_store=tool_store,
        client=client,
        tick=1,
        max_turns=5,
    )
    assert result.outcome == "fill"
    assert client.captured_messages is not None
    first = client.captured_messages[0]
    assert len(first) >= 1
    user0 = first[0]
    assert user0["role"] == "user"
    content = str(user0["content"])
    assert "Substrate context (auto-loaded for you)" in content
    assert "Do the thing." in content
    assert "My Skill" in content
