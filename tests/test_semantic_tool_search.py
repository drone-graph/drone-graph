"""Semantic ranking of tools by query embedding vs stored description vectors."""

from __future__ import annotations

from pathlib import Path

from drone_graph.embeddings import (
    SCOPE_DESCRIPTION,
    SQLiteEmbeddingStore,
    cosine_similarity,
    rank_tools_by_query,
)


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


def test_cosine_similarity_orthogonal() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_rank_tools_by_query_ordering(tmp_path: Path) -> None:
    db = tmp_path / "emb.sqlite"
    store = SQLiteEmbeddingStore(db)
    embedder = _KeywordEmbedder()
    try:
        for name, desc in [
            ("tool_alpha", "alpha widget helper"),
            ("tool_beta", "beta gadget helper"),
        ]:
            text = f"{name}\n{desc}"
            vec = embedder.embed(text)
            store.upsert(
                tool_name=name,
                scope=SCOPE_DESCRIPTION,
                model_id=embedder.model_id,
                vector=vec,
                source_hash="test",
            )

        alpha_first = rank_tools_by_query(
            "find alpha things",
            store=store,
            embedder=embedder,
        )
        assert alpha_first[0] == "tool_alpha"
        assert alpha_first[1] == "tool_beta"

        beta_first = rank_tools_by_query(
            "find beta things",
            store=store,
            embedder=embedder,
        )
        assert beta_first[0] == "tool_beta"
        assert beta_first[1] == "tool_alpha"
    finally:
        store.close()


def test_rank_tools_empty_store(tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite"
    store = SQLiteEmbeddingStore(db)
    embedder = _KeywordEmbedder()
    try:
        assert rank_tools_by_query("any", store=store, embedder=embedder) == []
    finally:
        store.close()


def test_rank_tools_respects_limit(tmp_path: Path) -> None:
    db = tmp_path / "lim.sqlite"
    store = SQLiteEmbeddingStore(db)
    embedder = _KeywordEmbedder()
    try:
        for name, desc in (("tool_alpha", "alpha"), ("tool_beta", "beta")):
            text = f"{name}\n{desc}"
            store.upsert(
                tool_name=name,
                scope=SCOPE_DESCRIPTION,
                model_id=embedder.model_id,
                vector=embedder.embed(text),
                source_hash="x",
            )
        out = rank_tools_by_query(
            "alpha",
            store=store,
            embedder=embedder,
            limit=1,
        )
        assert len(out) == 1
    finally:
        store.close()
