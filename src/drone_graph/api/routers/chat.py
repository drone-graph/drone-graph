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
@router.post("/api/chat/gap/{gap_id}")
def chat_with_drone(gap_id: str, req: _DroneChatRequest) -> dict[str, Any]:
    """Send a message from the operator to the swarm on this gap.

    The chat lives on the *gap*, not on a specific drone — that's the
    design. If there's a live drone working the gap, it sees the message
    at its next turn boundary (or wakes from ``cm_browser.await_operator``
    within ~1.5s). If there's no drone, the message stays in the
    substrate and the next drone dispatched against the gap sees it
    via context preload.

    Two paths to this endpoint:
    - ``/api/chat/drone/{gap_id}`` — legacy, original framing.
    - ``/api/chat/gap/{gap_id}``  — preferred. The chat surface lives
      on the gap itself, not on the ephemeral worker.
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


@router.get("/api/chat/gap/{gap_id}")
def chat_history(gap_id: str, limit: int = 100) -> dict[str, Any]:
    """Return the gap's full chat history (operator + drone messages).

    Built from ``chat_with_drone`` findings keyed to this gap. Survives
    drone exits — that's the point.
    """
    state = get_state()
    gap = state.store.get(gap_id)
    if gap is None:
        raise HTTPException(status_code=404, detail=f"no gap {gap_id!r}")
    try:
        findings = state.store.recent_findings(limit=max(50, limit * 2))
    except Exception:  # noqa: BLE001
        findings = []
    out: list[dict[str, Any]] = []
    for f in findings:
        if f.kind != FindingKind.chat_with_drone:
            continue
        if gap_id not in (f.affected_gap_ids or []):
            continue
        out.append(
            {
                "id": f.id,
                "author": f.author.value,
                "text": f.summary,
                "ts": f.created_at.isoformat(),
                "tick": f.tick,
            }
        )
        if len(out) >= limit:
            break
    return {"gap_id": gap_id, "messages": out}


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
