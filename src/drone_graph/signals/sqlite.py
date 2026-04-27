"""SQLite-backed SignalStore.

Concurrency model:
  * One connection per process. ``check_same_thread=False`` plus an internal
    ``RLock`` serializes Python-level access; SQLite WAL mode handles
    cross-process contention with ``BEGIN IMMEDIATE`` + ``busy_timeout``.
  * All writes go through ``with self._lock, self._conn:`` so the implicit
    transaction commits or rolls back atomically.
  * Read paths take the lock too — sqlite3 connection objects are not
    threadsafe even for reads under our access pattern.

The whole implementation is stdlib; no new dependency.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from drone_graph.signals.store import ClaimRecord, InstallRecord

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _now() -> float:
    return datetime.now(UTC).timestamp()


def _row_to_claim(row: sqlite3.Row | tuple[Any, ...]) -> ClaimRecord:
    metadata_raw = row[6]
    return ClaimRecord(
        kind=row[0],
        key=row[1],
        drone_id=row[2],
        acquired_at=row[3],
        expires_at=row[4],
        cancelled=bool(row[5]),
        metadata=json.loads(metadata_raw) if metadata_raw else None,
    )


def _row_to_install(row: sqlite3.Row | tuple[Any, ...]) -> InstallRecord:
    return InstallRecord(
        key=row[0],
        installed_by=row[1],
        installed_at=row[2],
        install_commands=json.loads(row[3]),
        usage=row[4],
    )


class SQLiteSignalStore:
    """File-backed SignalStore. Safe across processes via SQLite WAL."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we drive transactions explicitly
            timeout=5.0,
        )
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

    # ---- Claims -----------------------------------------------------------

    def try_acquire(
        self,
        kind: str,
        key: str,
        drone_id: str,
        ttl_s: float,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        now = _now()
        expires = now + ttl_s
        meta_json = json.dumps(metadata) if metadata is not None else None
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # Sweep an expired row out of the way so the insert can land.
                self._conn.execute(
                    "DELETE FROM claims "
                    "WHERE kind=? AND key=? AND expires_at < ?",
                    (kind, key, now),
                )
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO claims "
                    "(kind, key, drone_id, acquired_at, expires_at, "
                    " cancelled, metadata) "
                    "VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (kind, key, drone_id, now, expires, meta_json),
                )
                acquired = cur.rowcount == 1
                self._conn.execute("COMMIT")
                return acquired
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def heartbeat(
        self, kind: str, key: str, drone_id: str, ttl_s: float
    ) -> bool:
        now = _now()
        expires = now + ttl_s
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "UPDATE claims SET expires_at=? "
                    "WHERE kind=? AND key=? AND drone_id=? "
                    "AND expires_at >= ?",
                    (expires, kind, key, drone_id, now),
                )
                renewed = cur.rowcount == 1
                self._conn.execute("COMMIT")
                return renewed
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def release(self, kind: str, key: str, drone_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM claims "
                "WHERE kind=? AND key=? AND drone_id=?",
                (kind, key, drone_id),
            )

    def is_cancelled(self, kind: str, key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT cancelled FROM claims WHERE kind=? AND key=?",
                (kind, key),
            ).fetchone()
        return bool(row[0]) if row else False

    def signal_cancel(self, kind: str, key: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE claims SET cancelled=1 WHERE kind=? AND key=?",
                (kind, key),
            )

    def reap_expired(self, now: float | None = None) -> list[ClaimRecord]:
        cutoff = now if now is not None else _now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    "SELECT kind, key, drone_id, acquired_at, expires_at, "
                    "       cancelled, metadata "
                    "FROM claims WHERE expires_at < ?",
                    (cutoff,),
                ).fetchall()
                if rows:
                    self._conn.execute(
                        "DELETE FROM claims WHERE expires_at < ?",
                        (cutoff,),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return [_row_to_claim(r) for r in rows]

    def get_claim(self, kind: str, key: str) -> ClaimRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT kind, key, drone_id, acquired_at, expires_at, "
                "       cancelled, metadata "
                "FROM claims WHERE kind=? AND key=?",
                (kind, key),
            ).fetchone()
        return _row_to_claim(row) if row else None

    def claims_by_drone(self, drone_id: str) -> list[ClaimRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, key, drone_id, acquired_at, expires_at, "
                "       cancelled, metadata "
                "FROM claims WHERE drone_id=?",
                (drone_id,),
            ).fetchall()
        return [_row_to_claim(r) for r in rows]

    def release_all_for_drone(self, drone_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM claims WHERE drone_id=?",
                (drone_id,),
            )
        return cur.rowcount

    # ---- Install registry -------------------------------------------------

    def install_lookup(self, key: str) -> InstallRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT key, installed_by, installed_at, install_commands, usage "
                "FROM installs WHERE key=?",
                (key,),
            ).fetchone()
        return _row_to_install(row) if row else None

    def install_register(
        self,
        key: str,
        drone_id: str,
        install_commands: list[str],
        usage: str | None = None,
    ) -> bool:
        now = _now()
        cmds_json = json.dumps(install_commands)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO installs "
                    "(key, installed_by, installed_at, install_commands, usage) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (key, drone_id, now, cmds_json, usage),
                )
                wrote = cur.rowcount == 1
                if wrote:
                    self._conn.execute(
                        "DELETE FROM claims "
                        "WHERE kind='install' AND key=? AND drone_id=?",
                        (key, drone_id),
                    )
                self._conn.execute("COMMIT")
                return wrote
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ---- Token bucket -----------------------------------------------------

    def configure_bucket(
        self, provider: str, capacity_tokens: int, refill_per_sec: float
    ) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO provider_buckets "
                "(provider, capacity_tokens, tokens_remaining, "
                " refill_per_sec, last_refill_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(provider) DO UPDATE SET "
                "  capacity_tokens=excluded.capacity_tokens, "
                "  refill_per_sec=excluded.refill_per_sec",
                (provider, capacity_tokens, float(capacity_tokens),
                 refill_per_sec, now),
            )

    def _refill_locked(self, provider: str) -> tuple[float, int, float] | None:
        """Apply refill in-place. Returns (tokens_remaining, capacity, refill_rate)
        or None if no bucket exists. Caller must hold self._lock and be inside
        a transaction."""
        row = self._conn.execute(
            "SELECT capacity_tokens, tokens_remaining, refill_per_sec, "
            "       last_refill_at "
            "FROM provider_buckets WHERE provider=?",
            (provider,),
        ).fetchone()
        if row is None:
            return None
        capacity, remaining, rate, last = row
        now = _now()
        elapsed = max(0.0, now - last)
        added = elapsed * rate
        new_remaining = min(float(capacity), remaining + added)
        self._conn.execute(
            "UPDATE provider_buckets "
            "SET tokens_remaining=?, last_refill_at=? WHERE provider=?",
            (new_remaining, now, provider),
        )
        return new_remaining, int(capacity), float(rate)

    def take_tokens(
        self, provider: str, count: int, timeout_s: float = 0.0
    ) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            with self._lock:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    state = self._refill_locked(provider)
                    if state is None:
                        self._conn.execute("COMMIT")
                        return True  # no bucket configured → unlimited
                    remaining, _capacity, rate = state
                    if remaining >= count:
                        self._conn.execute(
                            "UPDATE provider_buckets "
                            "SET tokens_remaining = tokens_remaining - ? "
                            "WHERE provider=?",
                            (float(count), provider),
                        )
                        self._conn.execute("COMMIT")
                        return True
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
            now = time.monotonic()
            if now >= deadline:
                return False
            deficit = count - remaining
            wait = deficit / rate if rate > 0 else (deadline - now)
            time.sleep(min(0.5, max(0.05, min(wait, deadline - now))))

    # ---- Cost meter -------------------------------------------------------

    def init_run(self, run_id: str, ceiling_usd: float | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO cost_meter (run_id, ceiling_usd, spent_usd, "
                "                        started_at) "
                "VALUES (?, ?, 0, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET ceiling_usd=excluded.ceiling_usd",
                (run_id, ceiling_usd, _now()),
            )

    def add_cost(self, run_id: str, usd: float) -> bool:
        """Always records actual spend; returns False once the ceiling has been
        crossed so the caller knows to stop spawning further work. The API
        call has already happened by the time we add — refusing to record
        would silently underreport spend."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT ceiling_usd, spent_usd FROM cost_meter "
                    "WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return True
                ceiling, spent = row
                new_spent = spent + usd
                self._conn.execute(
                    "UPDATE cost_meter SET spent_usd=? WHERE run_id=?",
                    (new_spent, run_id),
                )
                self._conn.execute("COMMIT")
                return ceiling is None or new_spent <= ceiling
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def spent(self, run_id: str) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT spent_usd FROM cost_meter WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return float(row[0]) if row else 0.0

    # ---- Lifecycle --------------------------------------------------------

    def reset_all(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM claims")
            self._conn.execute("DELETE FROM installs")
            self._conn.execute("DELETE FROM provider_buckets")
            self._conn.execute("DELETE FROM cost_meter")
