"""SSE event stream.

One endpoint, ``GET /api/stream``. Clients connect, get a backfill of recent
events (last ~50 by default, or every event after ``?since_seq=N``), then a
live tail of scheduler and controller events as they happen.

The frontend uses this as its primary state-update channel after the
initial ``/api/snapshot`` paint.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Query, Request
from sse_starlette.sse import EventSourceResponse

from drone_graph.api.state import get_state

router = APIRouter(prefix="/api", tags=["stream"])


@router.get("/stream")
async def stream(
    request: Request,
    since_seq: int | None = Query(None, ge=0),
) -> EventSourceResponse:
    bus = get_state().event_bus

    async def _events() -> AsyncIterator[dict[str, Any]]:
        async with bus.subscribe(since_seq=since_seq) as q:
            # Initial hello so clients can confirm connection.
            yield {
                "event": "hello",
                "data": json.dumps(
                    {"since_seq": since_seq, "ts": _now_iso()},
                    default=str,
                ),
            }
            while True:
                if await request.is_disconnected():
                    break
                try:
                    rec = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Heartbeat — keeps proxies and the EventSource alive.
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {
                    "event": rec.get("event", "message"),
                    "data": json.dumps(rec, default=str),
                    "id": str(rec.get("_seq", "")),
                }

    return EventSourceResponse(_events())


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
