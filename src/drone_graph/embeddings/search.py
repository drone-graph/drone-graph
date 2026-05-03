"""Rank tools by cosine similarity between query embedding and stored vectors."""

from __future__ import annotations

import logging
import math

from drone_graph.embeddings.sqlite_store import SQLiteEmbeddingStore
from drone_graph.embeddings.types import SCOPE_DESCRIPTION, Embedder

_LOG = logging.getLogger(__name__)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]; ``0.0`` if lengths differ or a norm is zero."""
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def rank_tools_by_query(
    query: str,
    *,
    store: SQLiteEmbeddingStore,
    embedder: Embedder,
    scope: str = SCOPE_DESCRIPTION,
    limit: int | None = None,
) -> list[str]:
    """Embed ``query`` and return tool names ranked by cosine similarity (desc).

    Uses rows with the given ``scope`` and ``embedder.model_id``. Rows whose
    vector dimension differs from the query embedding are skipped.
    """
    qvec = embedder.embed(query)
    qdim = len(qvec)
    candidates = store.list_by_scope(scope, embedder.model_id)
    scored: list[tuple[float, str]] = []
    for row in candidates:
        if len(row.vector) != qdim:
            _LOG.debug(
                "skip tool %s: dim %s vs query dim %s",
                row.tool_name,
                len(row.vector),
                qdim,
            )
            continue
        score = cosine_similarity(qvec, row.vector)
        scored.append((score, row.tool_name))
    scored.sort(key=lambda t: (-t[0], t[1]))
    names = [t[1] for t in scored]
    if limit is not None:
        return names[:limit]
    return names
