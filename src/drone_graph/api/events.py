"""Event bus for mission-control SSE.

The scheduler writes JSONL events to its tape file. This module tails that
file and fans events out to in-memory asyncio queues, one per SSE subscriber.
A small ring buffer holds the last N events so reconnecting clients can
backfill without reloading the full snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# Big enough to cover roughly a minute of dense activity at the default tick
# cadence; cheap to hold.
_RING_CAPACITY = 2000


class EventBus:
    """Tail a JSONL tape into asyncio queues. Thread-safe.

    The bus is started before the scheduler is, with the (eventually-)tailed
    tape path. The scheduler creates the file lazily on first event; the
    tailer handles the file not existing yet.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ring: deque[dict[str, Any]] = deque(maxlen=_RING_CAPACITY)
        self._seq: int = 0
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tape_path: Path | None = None
        self._tail_thread: threading.Thread | None = None
        self._stop_tail = threading.Event()

    # ---- Lifecycle --------------------------------------------------------

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind to the FastAPI event loop. Called once during app startup."""
        self._loop = loop

    def set_tape_path(self, path: Path) -> None:
        """Switch the file the tailer watches. Stops any existing tailer."""
        with self._lock:
            if self._tape_path == path and self._tail_thread is not None:
                return
            if self._tail_thread is not None:
                self._stop_tail.set()
                self._tail_thread.join(timeout=2.0)
                self._stop_tail = threading.Event()
            self._tape_path = path
            self._tail_thread = threading.Thread(
                target=self._tail_loop,
                name="event-bus-tail",
                args=(path,),
                daemon=True,
            )
            self._tail_thread.start()

    def stop(self) -> None:
        self._stop_tail.set()
        if self._tail_thread is not None:
            self._tail_thread.join(timeout=2.0)

    # ---- Producer (in-process) -------------------------------------------

    def publish(self, event: str, **fields: Any) -> None:
        """Emit an event directly (for things the API does itself rather than
        the scheduler — e.g. user prompts, manual edits)."""
        record = {"ts": _now_iso(), "event": event, **fields}
        self._fan_out(record)

    # ---- Subscriber (SSE) -------------------------------------------------

    @contextlib.asynccontextmanager
    async def subscribe(
        self, since_seq: int | None = None
    ) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """Yield a queue of events for a single SSE client.

        ``since_seq`` lets the client request backfill from the ring buffer.
        New events arrive on the queue as the scheduler emits them.
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
        backfill: list[dict[str, Any]] = []
        with self._lock:
            self._subscribers.append(q)
            if since_seq is not None:
                backfill = [r for r in self._ring if int(r.get("_seq", 0)) > since_seq]
            else:
                # Default: hand over the tail of the ring so the client paints
                # immediately even if the substrate is at rest.
                backfill = list(self._ring)[-50:]
        try:
            for r in backfill:
                await q.put(r)
            yield q
        finally:
            with self._lock:
                if q in self._subscribers:
                    self._subscribers.remove(q)

    # ---- Internal --------------------------------------------------------

    def _fan_out(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            record["_seq"] = self._seq
            self._ring.append(record)
            subs = list(self._subscribers)
            loop = self._loop
        for q in subs:
            if loop is None:
                continue
            # Drop overflowing queues rather than block the tailer.
            loop.call_soon_threadsafe(_safe_put, q, record)

    def _tail_loop(self, path: Path) -> None:
        """Best-effort file tail. Polls the file for new lines."""
        pos = 0
        last_size = 0
        while not self._stop_tail.is_set():
            try:
                if not path.exists():
                    time.sleep(0.25)
                    continue
                size = path.stat().st_size
                if size < last_size:
                    # File truncated/rotated. Restart from the top.
                    pos = 0
                last_size = size
                if size <= pos:
                    time.sleep(0.15)
                    continue
                with path.open("rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                for raw in chunk.splitlines():
                    if not raw:
                        continue
                    try:
                        record = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    self._fan_out(record)
            except OSError:
                time.sleep(0.25)
            except Exception:
                time.sleep(0.5)


def _safe_put(q: asyncio.Queue[dict[str, Any]], record: dict[str, Any]) -> None:
    try:
        q.put_nowait(record)
    except asyncio.QueueFull:
        # Slow consumer. Drop the oldest to keep up.
        try:
            q.get_nowait()
            q.put_nowait(record)
        except Exception:
            pass


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# ---- Per-drone tape tailer (worker bash output streaming) ------------------


class DroneTapeTailer:
    """Tail every per-drone tape file under ``var/tapes/<run_id>/`` so we can
    surface last-N bash lines and turn counters for each active drone.

    The scheduler writes one JSONL tape per drone. We keep a small per-drone
    ring of the most recent terminal_run lines plus the last-known turn /
    cost / token state. We can also forward selected events (e.g.
    ``tool.register``) to the main EventBus so the frontend sees them live
    without waiting for the next drone-reaped snapshot.
    """

    def __init__(self, run_id: str, bus: "EventBus | None" = None) -> None:
        self._run_id = run_id
        self._dir = Path("var") / "tapes" / run_id
        self._lock = threading.Lock()
        self._state: dict[str, _DroneVitals] = {}
        self._stop = threading.Event()
        self._bus = bus
        self._thread = threading.Thread(
            target=self._loop, name="drone-tape-tail", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def vitals_for(self, gap_id: str) -> _DroneVitals | None:
        """Look up vitals for the drone working on this gap. Approximate —
        the controller is the authority on which drone is on which gap; we
        just index everything by gap id stamped on each event."""
        with self._lock:
            return self._state.get(gap_id)

    # ---- Internal --------------------------------------------------------

    def _loop(self) -> None:
        positions: dict[str, int] = {}
        sizes: dict[str, int] = {}
        while not self._stop.is_set():
            try:
                if not self._dir.exists():
                    time.sleep(0.5)
                    continue
                for entry in os.scandir(self._dir):
                    if not entry.name.endswith(".jsonl"):
                        continue
                    p = Path(entry.path)
                    try:
                        size = p.stat().st_size
                    except OSError:
                        continue
                    last = sizes.get(entry.name, 0)
                    if size < last:
                        positions[entry.name] = 0
                    sizes[entry.name] = size
                    if size <= positions.get(entry.name, 0):
                        continue
                    with p.open("rb") as f:
                        f.seek(positions.get(entry.name, 0))
                        chunk = f.read()
                        positions[entry.name] = f.tell()
                    for raw in chunk.splitlines():
                        if not raw:
                            continue
                        try:
                            rec = json.loads(raw.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        self._apply(rec)
                time.sleep(0.2)
            except Exception:
                time.sleep(0.5)

    def _apply(self, rec: dict[str, Any]) -> None:
        gap_id = rec.get("gap_id")
        if not isinstance(gap_id, str):
            return
        ev = rec.get("event", "")
        with self._lock:
            v = self._state.setdefault(gap_id, _DroneVitals())
            if ev == "drone.turn":
                turn = rec.get("turn")
                if isinstance(turn, int):
                    v.turn = turn
                tin = rec.get("tokens_in")
                tout = rec.get("tokens_out")
                cost = rec.get("cost_usd")
                if isinstance(tin, int):
                    v.tokens_in = tin
                if isinstance(tout, int):
                    v.tokens_out = tout
                if isinstance(cost, (int, float)):
                    v.cost_usd = float(cost)
            elif ev == "tool.terminal_run":
                cmd = rec.get("cmd") or rec.get("command")
                if isinstance(cmd, str):
                    v.last_command = cmd
                out = rec.get("stdout_tail") or rec.get("output")
                if isinstance(out, str):
                    lines = [ln for ln in out.splitlines() if ln.strip()]
                    v.tail = (v.tail + lines)[-3:]
            elif ev == "drone.start":
                mt = rec.get("max_turns")
                if isinstance(mt, int):
                    v.max_turns = mt
            elif ev == "tool.register":
                # Re-publish so the frontend can refresh the marketplace
                # without waiting for drone reap.
                if self._bus is not None:
                    self._bus.publish(
                        "tool.registered",
                        drone_id=rec.get("drone_id"),
                        name=rec.get("name"),
                        kind=rec.get("kind"),
                    )
            elif ev == "worker.realworld_action":
                # Real-time heads-up to the operator's chat the moment a
                # drone runs a side-effecting terminal command. No LLM,
                # zero cost — purely heuristic detection from worker.py.
                if self._bus is not None:
                    self._bus.publish(
                        "worker.realworld_action",
                        drone_id=rec.get("drone_id"),
                        gap_id=rec.get("gap_id"),
                        category=rec.get("category"),
                        description=rec.get("description"),
                        cmd=rec.get("cmd"),
                    )
            elif ev == "drone.narrate":
                # End-of-drone chat-rail summary, produced by a nano-tier
                # model. One per drone exit. Surface as a worker chat msg.
                if self._bus is not None:
                    self._bus.publish(
                        "drone.narrate",
                        drone_id=rec.get("drone_id"),
                        gap_id=rec.get("gap_id"),
                        outcome=rec.get("outcome"),
                        text=rec.get("text"),
                        finding_id=rec.get("finding_id"),
                    )


class _DroneVitals:
    __slots__ = (
        "turn",
        "max_turns",
        "last_command",
        "tail",
        "cost_usd",
        "tokens_in",
        "tokens_out",
    )

    def __init__(self) -> None:
        self.turn: int | None = None
        self.max_turns: int | None = None
        self.last_command: str | None = None
        self.tail: list[str] = []
        self.cost_usd: float | None = None
        self.tokens_in: int | None = None
        self.tokens_out: int | None = None
