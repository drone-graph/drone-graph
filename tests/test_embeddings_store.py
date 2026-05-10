"""SQLite embedding store and ToolStore backfill."""

from __future__ import annotations

import json
from pathlib import Path

from drone_graph.embeddings import (
    SCOPE_DESCRIPTION,
    SQLiteEmbeddingStore,
    floats_to_blob,
)
from drone_graph.substrate import Substrate
from drone_graph.tools.records import Tool, ToolKind
from drone_graph.tools.store import ToolStore


class _FakeEmbedder:
    """Deterministic 8-dim vector for tests."""

    @property
    def model_id(self) -> str:
        return "test-fake"

    def embed(self, text: str) -> list[float]:
        _ = text
        return [float(i) for i in range(8)]


def test_sqlite_embedding_store_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "emb.sqlite"
    store = SQLiteEmbeddingStore(db)
    try:
        vec = [0.25, -1.0, 2.5]
        store.upsert(
            tool_name="cm_x",
            scope=SCOPE_DESCRIPTION,
            model_id="test-fake",
            vector=vec,
            source_hash="abc",
        )
        got = store.get("cm_x", SCOPE_DESCRIPTION, "test-fake")
        assert got is not None
        assert got.vector == vec
        assert got.dim == 3
        assert got.source_hash == "abc"
        assert floats_to_blob(vec) == floats_to_blob(got.vector)
    finally:
        store.close()


def test_register_installed_backfills_embedding(
    substrate: Substrate,
    tmp_path: Path,
) -> None:
    db = tmp_path / "emb.sqlite"
    emb_store = SQLiteEmbeddingStore(db)
    embedder = _FakeEmbedder()
    try:
        tool_store = ToolStore(
            substrate,
            embedding_store=emb_store,
            embedder=embedder,
        )
        tool_store.register_installed(
            Tool(
                name="emb_test_tool",
                description="hello embed",
                input_schema_json=json.dumps(
                    {"type": "object", "properties": {}}
                ),
                kind=ToolKind.installed,
                usage="emb_test_tool --help",
                installed_by_drone_id="d1",
            )
        )
        row = emb_store.get("emb_test_tool", SCOPE_DESCRIPTION, "test-fake")
        assert row is not None
        assert row.vector == [float(i) for i in range(8)]
        assert row.dim == 8
    finally:
        emb_store.close()


def test_upsert_builtin_backfills_embedding(
    substrate: Substrate,
    tmp_path: Path,
) -> None:
    db = tmp_path / "emb.sqlite"
    emb_store = SQLiteEmbeddingStore(db)
    embedder = _FakeEmbedder()
    try:
        tool_store = ToolStore(
            substrate,
            embedding_store=emb_store,
            embedder=embedder,
        )
        tool_store.upsert_builtin(
            Tool(
                name="builtin_emb_probe",
                description="builtin description for embed",
                input_schema_json="{}",
                kind=ToolKind.builtin,
            )
        )
        row = emb_store.get("builtin_emb_probe", SCOPE_DESCRIPTION, "test-fake")
        assert row is not None
        assert len(row.vector) == 8
    finally:
        emb_store.close()


def test_embedding_failure_does_not_block_registration(
    substrate: Substrate,
    tmp_path: Path,
) -> None:
    """Best-effort: broken embedder still leaves Tool in Neo4j."""

    class _BadEmbedder:
        @property
        def model_id(self) -> str:
            return "bad"

        def embed(self, text: str) -> list[float]:
            raise RuntimeError("no api")

    db = tmp_path / "emb.sqlite"
    emb_store = SQLiteEmbeddingStore(db)
    try:
        tool_store = ToolStore(
            substrate,
            embedding_store=emb_store,
            embedder=_BadEmbedder(),
        )
        tool_store.register_installed(
            Tool(
                name="still_registered_tool",
                description="x",
                input_schema_json=json.dumps(
                    {"type": "object", "properties": {}}
                ),
                kind=ToolKind.installed,
                usage="u",
                installed_by_drone_id="d1",
            )
        )
        assert tool_store.get("still_registered_tool") is not None
        assert emb_store.get("still_registered_tool", SCOPE_DESCRIPTION, "bad") is None
    finally:
        emb_store.close()
