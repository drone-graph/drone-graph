"""Mutation endpoints.

Two flavors, kept separate by design:

  * **Direct mutations** (retire, rewrite, trust-tier, marketplace approval) —
    these touch the graph immediately. They are the operator surface for
    things that need to happen now, not when Gap Finding gets around to it.
  * **Guidance** (the chat endpoint) — writes a ``user_input`` finding the
    swarm sees on its next tick. The hivemind decides what to do.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from drone_graph.api.models import (
    CancelDroneRequest,
    ChatRequest,
    InstallApprovalRequest,
    RetireRequest,
    RewriteIntentRequest,
    TrustTierRequest,
)
from drone_graph.api.routers.substrate import _finding_to_dto, _gap_to_dto
from drone_graph.api.state import get_state, require_controller
from drone_graph.gaps import FindingAuthor, FindingKind
from drone_graph.tools import TrustTier

router = APIRouter(prefix="/api/edit", tags=["edit"])


def _swarm_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            "swarm controller is not running — configure a provider key in "
            "Settings to wake it"
        ),
    )


# ---- Chat / guidance ------------------------------------------------------


@router.post("/chat")
def chat(req: ChatRequest) -> dict[str, Any]:
    s = get_state()
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="empty message")
    if s.controller is None:
        raise _swarm_unavailable()
    fid = s.controller.submit_prompt(req.message, target_gap_id=req.target_gap_id)
    return {"finding_id": fid}


# ---- Gap structural ops ---------------------------------------------------


@router.post("/gaps/{gap_id}/retire")
def retire_gap(gap_id: str, req: RetireRequest) -> dict[str, Any]:
    s = get_state()
    g = s.store.get(gap_id)
    if g is None:
        raise HTTPException(status_code=404, detail=f"no gap {gap_id}")
    tick = _next_tick(s)
    finding = s.store.apply_retire(
        gap_id=gap_id,
        reason=req.reason,
        tick=tick,
        author=FindingAuthor.user,
    )
    s.event_bus.publish(
        "user.retire",
        tick=tick,
        gap_id=gap_id,
        reason=req.reason,
        finding_id=finding.id,
    )
    return {"finding": _finding_to_dto(finding).model_dump()}


@router.post("/gaps/{gap_id}/rewrite")
def rewrite_gap(gap_id: str, req: RewriteIntentRequest) -> dict[str, Any]:
    s = get_state()
    g = s.store.get(gap_id)
    if g is None:
        raise HTTPException(status_code=404, detail=f"no gap {gap_id}")
    tick = _next_tick(s)
    try:
        finding = s.store.apply_rewrite_intent(
            gap_id=gap_id,
            new_intent=req.new_intent,
            new_criteria=req.new_criteria,
            rationale=req.rationale,
            tick=tick,
            author=FindingAuthor.user,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    s.event_bus.publish(
        "user.rewrite_intent",
        tick=tick,
        gap_id=gap_id,
        finding_id=finding.id,
    )
    updated = s.store.get(gap_id)
    return {
        "finding": _finding_to_dto(finding).model_dump(),
        "gap": _gap_to_dto(updated).model_dump() if updated else None,
    }


@router.post("/gaps/{gap_id}/reopen")
def reopen_gap(gap_id: str, req: RetireRequest) -> dict[str, Any]:
    """Manually mark a filled gap unfilled. Reuses the retire reason field."""
    s = get_state()
    g = s.store.get(gap_id)
    if g is None:
        raise HTTPException(status_code=404, detail=f"no gap {gap_id}")
    tick = _next_tick(s)
    try:
        finding = s.store.apply_reopen(
            gap_id=gap_id,
            reason=req.reason,
            tick=tick,
            author=FindingAuthor.user,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    s.event_bus.publish(
        "user.reopen", tick=tick, gap_id=gap_id, finding_id=finding.id
    )
    return {"finding": _finding_to_dto(finding).model_dump()}


# ---- Drones --------------------------------------------------------------


@router.post("/drones/{gap_id}/cancel")
def cancel_drone(gap_id: str, req: CancelDroneRequest) -> dict[str, Any]:
    s = get_state()
    if s.controller is None:
        raise _swarm_unavailable()
    s.controller.cancel_drone(gap_id, reason=req.reason)
    return {"ok": True}


# ---- Identity approval ---------------------------------------------------
#
# Operator-facing endpoints for the per-gap "approve operator-identity"
# flow. Triggered from the inbox UI when the scheduler has surfaced a
# requires_user_action finding asking permission to use the operator's
# real $HOME / env / cwd for a specific gap.


class _IdentityGrantRequest(BaseModel):
    note: str = ""


class _IdentityDenyRequest(BaseModel):
    reason: str = ""


@router.post("/gaps/{gap_id}/grant-identity")
def grant_identity(gap_id: str, req: _IdentityGrantRequest) -> dict[str, Any]:
    s = get_state()
    g = s.store.get(gap_id)
    if g is None:
        raise HTTPException(status_code=404, detail=f"no gap {gap_id}")
    if not getattr(g, "uses_operator_identity", False):
        raise HTTPException(
            status_code=400,
            detail="gap does not request operator identity",
        )
    tick = _next_tick(s)
    finding = s.store.apply_grant_identity(
        gap_id=gap_id, tick=tick, note=req.note, author=FindingAuthor.user
    )
    s.event_bus.publish(
        "user.identity_granted",
        tick=tick,
        gap_id=gap_id,
        finding_id=finding.id,
    )
    return {"finding": _finding_to_dto(finding).model_dump()}


@router.post("/gaps/{gap_id}/deny-identity")
def deny_identity(gap_id: str, req: _IdentityDenyRequest) -> dict[str, Any]:
    s = get_state()
    g = s.store.get(gap_id)
    if g is None:
        raise HTTPException(status_code=404, detail=f"no gap {gap_id}")
    tick = _next_tick(s)
    finding = s.store.apply_deny_identity(
        gap_id=gap_id,
        reason=req.reason or "denied by operator",
        tick=tick,
        author=FindingAuthor.user,
    )
    s.event_bus.publish(
        "user.identity_denied",
        tick=tick,
        gap_id=gap_id,
        finding_id=finding.id,
    )
    return {"finding": _finding_to_dto(finding).model_dump()}


# ---- Marketplace ---------------------------------------------------------


@router.post("/tools/{name}/trust")
def set_trust_tier(name: str, req: TrustTierRequest) -> dict[str, Any]:
    s = get_state()
    t = s.tool_store.get(name)
    if t is None:
        raise HTTPException(status_code=404, detail=f"no tool {name!r}")
    if t.kind.value == "builtin" and req.tier != "high":
        raise HTTPException(status_code=400, detail="builtin tools are always high")
    s.substrate.execute_write(
        "MATCH (t:Tool {name: $name}) SET t.trust_tier = $tier",
        name=name,
        tier=TrustTier(req.tier).value,
    )
    s.event_bus.publish("user.trust_tier_set", tool=name, tier=req.tier)
    return {"ok": True}


@router.post("/tools/{name}/flag")
def flag_tool(name: str) -> dict[str, Any]:
    s = get_state()
    t = s.tool_store.get(name)
    if t is None:
        raise HTTPException(status_code=404, detail=f"no tool {name!r}")
    s.tool_store.flag(name, flagged=True)
    s.event_bus.publish("user.tool_flagged", tool=name)
    return {"ok": True}


@router.post("/tools/{name}/unflag")
def unflag_tool(name: str) -> dict[str, Any]:
    s = get_state()
    t = s.tool_store.get(name)
    if t is None:
        raise HTTPException(status_code=404, detail=f"no tool {name!r}")
    s.tool_store.flag(name, flagged=False)
    s.event_bus.publish("user.tool_unflagged", tool=name)
    return {"ok": True}


@router.post("/installs/{install_id}")
def approve_install(install_id: str, req: InstallApprovalRequest) -> dict[str, Any]:
    s = get_state()
    ok = s.controller.resolve_install(install_id, approve=req.approve)
    if not ok:
        raise HTTPException(status_code=404, detail="no pending install with that id")
    return {"ok": True}


# ---- Internals ------------------------------------------------------------


def _next_tick(s: Any) -> int:
    """Bump the scheduler's tick counter so user-authored findings stay in
    sync with the orchestrator's ordering."""
    sched = s.controller._scheduler  # noqa: SLF001
    if sched is None:
        # Pre-scheduler-start path: synthesize ticks from now().
        return int(datetime.now(UTC).timestamp())
    sched.tick += 1
    return int(sched.tick)
