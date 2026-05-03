"""Best-effort indexing of tool text into the embedding store."""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol

from drone_graph.embeddings.sqlite_store import SQLiteEmbeddingStore
from drone_graph.embeddings.types import SCOPE_DESCRIPTION, Embedder

_LOG = logging.getLogger(__name__)


class ToolEmbeddingSource(Protocol):
    """Structural type for anything with tool name + description (e.g. ``Tool``)."""

    name: str
    description: str


def canonical_description_text(tool: ToolEmbeddingSource) -> str:
    return f"{tool.name}\n{tool.description}".strip()


def maybe_index_tool_description(
    tool: ToolEmbeddingSource,
    embedding_store: SQLiteEmbeddingStore | None,
    embedder: Embedder | None,
) -> None:
    """Embed ``tool`` description scope if store and embedder are configured.

    Failures are swallowed so Neo4j tool writes never depend on sidecar SQLite
    or embedding APIs.
    """
    if embedding_store is None or embedder is None:
        return
    try:
        text = canonical_description_text(tool)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = embedding_store.get(tool.name, SCOPE_DESCRIPTION, embedder.model_id)
        if existing is not None and existing.source_hash == digest:
            return
        vec = embedder.embed(text)
        embedding_store.upsert(
            tool_name=tool.name,
            scope=SCOPE_DESCRIPTION,
            model_id=embedder.model_id,
            vector=vec,
            source_hash=digest,
        )
    except Exception:
        _LOG.debug(
            "embedding backfill skipped for tool %s",
            tool.name,
            exc_info=True,
        )
