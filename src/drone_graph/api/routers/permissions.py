"""Synchronous permission-prompt API.

The drone tool dispatcher writes a row into ``signals.permissions`` and
polls it until the operator answers. These endpoints let the UI surface
those pending rows and post the resolution.

  * ``GET  /api/permissions/pending`` — list of rows awaiting a decision
  * ``POST /api/permissions/{id}/grant`` — approve, optional note
  * ``POST /api/permissions/{id}/deny``  — reject, optional note

The dispatcher polls every 500ms with a 5-minute timeout. After resolution
it deletes the row, so this list shrinks to empty when the operator catches
up.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from drone_graph.api.state import get_state
from drone_graph.signals.store import PermissionRecord

router = APIRouter(prefix="/api/permissions", tags=["permissions"])


class _ResolveRequest(BaseModel):
    note: str | None = None


def _to_dto(rec: PermissionRecord) -> dict[str, Any]:
    return {
        "id": rec.id,
        "drone_id": rec.drone_id,
        "gap_id": rec.gap_id,
        "tier": rec.tier,
        "tool_name": rec.tool_name,
        "category": rec.category,
        "summary": rec.summary,
        "status": rec.status,
        "created_at": rec.created_at,
        "resolved_at": rec.resolved_at,
        "resolver_note": rec.resolver_note,
    }


@router.get("/pending")
def list_pending() -> list[dict[str, Any]]:
    s = get_state()
    return [_to_dto(r) for r in s.signals.list_pending_permissions()]


@router.post("/{request_id}/grant")
def grant(request_id: str, req: _ResolveRequest | None = None) -> dict[str, Any]:
    s = get_state()
    note = req.note if req is not None else None
    updated = s.signals.resolve_permission(
        request_id, granted=True, note=note,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="no pending permission")
    s.event_bus.publish(
        "permission.resolved",
        request_id=request_id,
        gap_id=updated.gap_id,
        outcome="granted",
    )
    return _to_dto(updated)


@router.post("/{request_id}/deny")
def deny(request_id: str, req: _ResolveRequest | None = None) -> dict[str, Any]:
    s = get_state()
    note = req.note if req is not None else None
    updated = s.signals.resolve_permission(
        request_id, granted=False, note=note,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="no pending permission")
    s.event_bus.publish(
        "permission.resolved",
        request_id=request_id,
        gap_id=updated.gap_id,
        outcome="denied",
    )
    return _to_dto(updated)
