"""Integration: emergent drone calls cm_search_tools then fills via cm_write_finding."""

from __future__ import annotations

import json
from pathlib import Path

from drone_graph.drones.providers import ChatResponse, Provider, ToolCall, Usage
from drone_graph.drones.runtime import run_drone
from drone_graph.embeddings.sqlite_store import SQLiteEmbeddingStore
from drone_graph.gaps import GapStore
from drone_graph.orchestrator.tape import EventTape
from drone_graph.tools import Tool, ToolKind, empty_input_schema
from drone_graph.tools.store import ToolStore


class _KeywordEmbedder:
    """3-dim vectors: alpha -> x-axis, beta -> y-axis, else z-axis."""

    @property
    def model_id(self) -> str:
        return "step8-test"

    def embed(self, text: str) -> list[float]:
        t = text.lower()
        if "alpha" in t:
            return [1.0, 0.0, 0.0]
        if "beta" in t:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


class _FakeChatClient:
    provider = Provider.anthropic
    model = "claude-test"

    def __init__(self) -> None:
        self.turn = 0
        self.captured_messages: list[list[dict[str, object]]] = []

    def chat(
        self,
        system: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ChatResponse:
        self.turn += 1
        self.captured_messages.append(messages)
        if self.turn == 1:
            raw = [
                {
                    "type": "tool_use",
                    "id": "tu_search",
                    "name": "cm_search_tools",
                    "input": {"query": "find alpha things"},
                }
            ]
            return ChatResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="tu_search",
                        name="cm_search_tools",
                        input={"query": "find alpha things"},
                    )
                ],
                raw_assistant_content=raw,
                usage=Usage(tokens_in=1, tokens_out=2),
            )
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


def _first_cm_search_json(messages: list[dict[str, object]]) -> dict[str, object]:
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            raw = block.get("content")
            if not isinstance(raw, str) or not raw.strip().startswith("{"):
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "ranked_names" in obj:
                return obj
    raise AssertionError("no cm_search_tools JSON in messages")


def test_run_drone_cm_search_tools_then_fill(
    substrate,
    tmp_path: Path,
) -> None:
    db = tmp_path / "emb.sqlite"
    embed_store = SQLiteEmbeddingStore(db)
    embedder = _KeywordEmbedder()
    tool_store = ToolStore(
        substrate,
        embedding_store=embed_store,
        embedder=embedder,
    )
    gap_store = GapStore(substrate)
    schema = json.dumps(empty_input_schema())
    try:
        for name, desc in (
            ("tool_alpha", "alpha widget helper"),
            ("tool_beta", "beta gadget helper"),
        ):
            tool_store.register_installed(
                Tool(
                    name=name,
                    description=desc,
                    input_schema_json=schema,
                    kind=ToolKind.installed,
                    usage=f"run {name}",
                    installed_by_drone_id="fixture-drone",
                )
            )

        gap = gap_store.create_root(intent="try semantic search", criteria="ok")
        tape_path = tmp_path / "tape.jsonl"
        tape = EventTape(tape_path)
        client = _FakeChatClient()
        result = run_drone(
            gap,
            store=gap_store,
            tool_store=tool_store,
            client=client,
            tick=1,
            max_turns=5,
            tape=tape,
        )
        assert result.outcome == "fill"

        flat_msgs: list[dict[str, object]] = []
        for batch in client.captured_messages:
            flat_msgs.extend(batch)
        search_payload = _first_cm_search_json(flat_msgs)
        ranked = search_payload["ranked_names"]
        assert ranked[0] == "tool_alpha"
        assert "tool_alpha" in json.dumps(search_payload)

        lines = tape_path.read_text(encoding="utf-8").strip().splitlines()
        turn_events = [json.loads(line) for line in lines if line]
        first_turn = next(e for e in turn_events if e.get("event") == "drone.turn")
        assert first_turn.get("turn") == 1
        assert "cm_search_tools" in first_turn.get("tool_calls", [])
    finally:
        embed_store.close()
