"""SQLite-backed shared page ledger.

Maps ``gap_id → CDP target_id`` across drone subprocesses so a successor
drone for the same gap can find and reuse the previous drone's browser tab
instead of creating a new one.

Thread-safe and cross-process-safe via SQLite WAL mode.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


# ── DB path ────────────────────────────────────────────────────────────


def _db_path() -> Path:
    """Return the path to the shared page ledger database."""
    base = Path.home() / ".drone-graph"
    base.mkdir(parents=True, exist_ok=True)
    return base / "browser-pages.db"


# ── Schema ─────────────────────────────────────────────────────────────


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the ``gap_pages`` table if missing, enable WAL mode."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gap_pages (
            gap_id        TEXT PRIMARY KEY,
            target_id     TEXT NOT NULL,
            drone_id      TEXT,
            registered_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


# ── Connection helpers ─────────────────────────────────────────────────


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=10.0)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


# ── Target ID extraction ───────────────────────────────────────────────


def target_id_of(page: Any) -> str:
    """Extract the CDP target ID from a Playwright ``Page`` object.

    Uses the internal ``_impl_obj._target._target_id`` attribute, which is
    stable across Playwright 1.35–1.50+. This is the only reliable way to
    match a page across separate CDP connections.
    """
    try:
        return page._impl_obj._target._target_id  # type: ignore[attr-defined]
    except AttributeError:
        # Fallback for very new Playwright versions with different internals.
        try:
            return page._target_id  # type: ignore[attr-defined]
        except AttributeError:
            return str(id(page))


# ── Public API ─────────────────────────────────────────────────────────


def register_page(gap_id: str, drone_id: str, page: Any) -> None:
    """Record that *gap_id* owns the CDP target backing *page*.

    Uses ``INSERT OR REPLACE`` so a stale entry (e.g. from a lost claim)
    is silently overwritten.
    """
    tid = target_id_of(page)
    conn = _connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO gap_pages (gap_id, target_id, drone_id) "
            "VALUES (?, ?, ?)",
            (gap_id, tid, drone_id),
        )
        conn.commit()
    finally:
        conn.close()


def lookup_page(gap_id: str, browser: Any) -> Any | None:
    """Find an open page for *gap_id* in the shared Chrome instance.

    Algorithm:
      1. Query the SQLite ledger for the target_id associated with *gap_id*.
      2. Iterate ``browser.contexts[0].pages`` to find a page with a matching
         target_id.
      3. Verify the page is still alive via ``page.url``.
      4. If the page is dead (stale entry) unregister it and return ``None``.

    Returns the ``Page`` if found and alive, otherwise ``None``.
    """
    conn = _connection()
    try:
        row = conn.execute(
            "SELECT target_id FROM gap_pages WHERE gap_id = ?",
            (gap_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    expected_tid: str = row["target_id"]

    # Iterate all open pages across all CDP connections.
    try:
        context = browser.contexts[0]
        for page in context.pages:
            try:
                if target_id_of(page) == expected_tid:
                    _ = page.url  # liveness check
                    return page
            except Exception:
                # Dead or inaccessible page — skip.
                continue
    except Exception:
        pass

    # Page not found or dead — clean up the stale ledger entry.
    unregister_page(gap_id)
    return None


def unregister_page(gap_id: str) -> None:
    """Remove the ledger entry for *gap_id*, if any."""
    conn = _connection()
    try:
        conn.execute("DELETE FROM gap_pages WHERE gap_id = ?", (gap_id,))
        conn.commit()
    finally:
        conn.close()


def reap_filled_gaps(store: Any) -> int:
    """Remove ledger entries for gaps that are filled or retired.

    Called periodically by the scheduler to prevent unbounded growth of the
    ledger when gaps complete.  Returns the number of entries cleaned up.
    """
    conn = _connection()
    try:
        rows = conn.execute("SELECT gap_id FROM gap_pages").fetchall()
        if not rows:
            return 0
        cleaned = 0
        for row in rows:
            gap_id: str = row["gap_id"]
            gap = store.get(gap_id)
            if gap is not None and gap.status in ("filled", "retired"):
                conn.execute(
                    "DELETE FROM gap_pages WHERE gap_id = ?", (gap_id,)
                )
                cleaned += 1
        if cleaned:
            conn.commit()
        return cleaned
    finally:
        conn.close()
