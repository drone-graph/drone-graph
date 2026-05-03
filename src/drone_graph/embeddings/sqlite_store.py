"""SQLite-backed embedding vector storage."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from drone_graph.embeddings.vectors import blob_to_floats, floats_to_blob

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class StoredEmbedding:
    tool_name: str
    scope: str
    model_id: str
    dim: int
    vector: list[float]
    source_hash: str | None
    updated_at: float


class SQLiteEmbeddingStore:
    """File-backed store for tool_embedding rows. WAL + RLock like SignalStore."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,
            timeout=5.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    @property
    def path(self) -> Path:
        return self._path

    def _init_schema(self) -> None:
        ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._lock:
            self._conn.executescript(ddl)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert(
        self,
        *,
        tool_name: str,
        scope: str,
        model_id: str,
        vector: list[float],
        source_hash: str | None,
    ) -> None:
        dim = len(vector)
        blob = floats_to_blob(vector)
        ts = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tool_embedding (tool_name, scope, model_id, dim, "
                "vector, source_hash, updated_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(tool_name, scope, model_id) DO UPDATE SET "
                "dim=excluded.dim, vector=excluded.vector, "
                "source_hash=excluded.source_hash, updated_at=excluded.updated_at",
                (tool_name, scope, model_id, dim, blob, source_hash, ts),
            )

    def get(self, tool_name: str, scope: str, model_id: str) -> StoredEmbedding | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT tool_name, scope, model_id, dim, vector, source_hash, "
                "updated_at FROM tool_embedding WHERE tool_name = ? AND scope = ? "
                "AND model_id = ?",
                (tool_name, scope, model_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return _row_to_stored(row)

    def list_by_scope(self, scope: str, model_id: str) -> list[StoredEmbedding]:
        """Return all stored embeddings for a scope and model (e.g. semantic search)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT tool_name, scope, model_id, dim, vector, source_hash, "
                "updated_at FROM tool_embedding WHERE scope = ? AND model_id = ? "
                "ORDER BY tool_name ASC",
                (scope, model_id),
            )
            rows = cur.fetchall()
        return [_row_to_stored(row) for row in rows]

    def delete(self, tool_name: str, scope: str, model_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM tool_embedding WHERE tool_name = ? AND scope = ? "
                "AND model_id = ?",
                (tool_name, scope, model_id),
            )


def _row_to_stored(row: sqlite3.Row) -> StoredEmbedding:
    raw = row["vector"]
    dim = int(row["dim"])
    vec = blob_to_floats(bytes(raw))
    if len(vec) != dim:
        raise ValueError(f"stored dim {dim} does not match blob length {len(vec)}")
    sh = row["source_hash"]
    return StoredEmbedding(
        tool_name=str(row["tool_name"]),
        scope=str(row["scope"]),
        model_id=str(row["model_id"]),
        dim=dim,
        vector=vec,
        source_hash=str(sh) if sh not in (None, "") else None,
        updated_at=float(row["updated_at"]),
    )
