"""SQLite sidecar for tool embedding vectors (single source for vectors)."""

from __future__ import annotations

from drone_graph.embeddings.backfill import (
    ToolEmbeddingSource,
    canonical_description_text,
    maybe_index_tool_description,
)
from drone_graph.embeddings.search import cosine_similarity, rank_tools_by_query
from drone_graph.embeddings.sqlite_store import SQLiteEmbeddingStore, StoredEmbedding
from drone_graph.embeddings.types import SCOPE_DESCRIPTION, Embedder
from drone_graph.embeddings.vectors import blob_to_floats, floats_to_blob

__all__ = [
    "SCOPE_DESCRIPTION",
    "Embedder",
    "SQLiteEmbeddingStore",
    "StoredEmbedding",
    "ToolEmbeddingSource",
    "blob_to_floats",
    "canonical_description_text",
    "cosine_similarity",
    "floats_to_blob",
    "maybe_index_tool_description",
    "rank_tools_by_query",
]
