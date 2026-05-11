"""Operator ↔ drone chat + browser-state endpoints.

The operator can talk directly to a live drone via the per-gap chat panel.
Messages land as ``chat_with_drone`` findings (author=user); the drone
either reads them on its next turn boundary (the runtime drains pending
ones into the next user message) or wakes from ``cm_browser.await_operator``
immediately and returns the message as a tool result.

The browser-state endpoint exposes the last-known ``cm_browser`` state for
a given drone gap so the panel can render a live screenshot + URL even
without an active SSE connection (e.g. on first page load).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from drone_graph.api.state import get_state
from drone_graph.gaps.records import FindingAuthor, FindingKind

router = APIRouter(tags=["chat"])


class _DroneChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/api/chat/drone/{gap_id}")
def chat_with_drone(gap_id: str, req: _DroneChatRequest) -> dict[str, Any]:
    """Send a message from the operator to the drone working on ``gap_id``.

    Always succeeds even if no drone is currently dispatched — the next
    drone on this gap will see the message in its initial preload context.
    The drone may also be polling in ``cm_browser.await_operator``, in
    which case it picks the message up within ~1.5s.
    """
    state = get_state()
    gap = state.store.get(gap_id)
    if gap is None:
        raise HTTPException(status_code=404, detail=f"no gap {gap_id!r}")
    controller = state.controller
    tick = controller.current_tick() if controller is not None else 0
    finding = state.store.append_finding(
        tick=tick,
        author=FindingAuthor.user,
        kind=FindingKind.chat_with_drone,
        summary=req.text,
        affected_gap_ids=[gap_id],
    )
    state.event_bus.publish(
        "chat.drone",
        gap_id=gap_id,
        author="user",
        text=req.text,
        finding_id=finding.id,
    )
    return {"ok": True, "finding_id": finding.id}


@router.get("/api/drones/{gap_id}/browser-state")
def browser_state(gap_id: str) -> dict[str, Any]:
    """Last-known ``cm_browser`` state for the drone on this gap.

    Returns ``{"active": false}`` if there's no recorded browser session.
    Otherwise returns the latest action event (url, title, action,
    screenshot_path) plus an optional inline screenshot if the file is
    still on disk.
    """
    state = get_state()
    tailer = getattr(state.controller, "drone_tape_tailer", None) if state.controller else None
    if tailer is None:
        return {"active": False}
    snap = tailer.browser_state_for(gap_id)
    if not snap:
        return {"active": False}
    out: dict[str, Any] = {"active": True, **snap}
    # Inline a small base64 of the screenshot so the panel renders without
    # a second round trip. Skip if the file is gone or larger than 1MB
    # (network would be a better path for big images).
    p = snap.get("screenshot_path")
    if p:
        try:
            path = Path(p)
            if path.exists():
                sz = path.stat().st_size
                if sz < 1_000_000:
                    out["screenshot_b64"] = base64.b64encode(path.read_bytes()).decode(
                        "ascii"
                    )
                    out["screenshot_bytes"] = sz
        except OSError:
            pass
    return out


@router.get("/api/drones/{gap_id}/screenshot")
def screenshot(gap_id: str) -> dict[str, Any]:
    """Raw bytes of the latest screenshot for this drone, as base64.

    Separate endpoint so the panel can poll cheaply at a high rate without
    re-reading the full browser-state JSON each time.
    """
    state = get_state()
    tailer = getattr(state.controller, "drone_tape_tailer", None) if state.controller else None
    if tailer is None:
        raise HTTPException(status_code=404, detail="no drone tailer")
    snap = tailer.browser_state_for(gap_id)
    if not snap or not snap.get("screenshot_path"):
        raise HTTPException(status_code=404, detail="no screenshot")
    path = Path(str(snap["screenshot_path"]))
    if not path.exists():
        raise HTTPException(status_code=404, detail="screenshot file gone")
    return {
        "b64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "ts": snap.get("ts"),
        "url": snap.get("url"),
        "title": snap.get("title"),
        "action": snap.get("action"),
    }
