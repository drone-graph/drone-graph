"""Operator control endpoints: pause, resume, ceiling, paranoid mode, force tick, restart."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from drone_graph.api.models import (
    CostCeilingRequest,
    ParanoidModeRequest,
)
from drone_graph.api.state import get_state

router = APIRouter(prefix="/api/control", tags=["control"])


class _ForceTickRequest(BaseModel):
    role: Literal["gap_finding", "alignment"]


def _swarm_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            "swarm controller is not running — configure a provider key in "
            "Settings to wake it"
        ),
    )


def _controller() -> Any:
    c = get_state().controller
    if c is None:
        raise _swarm_unavailable()
    return c


@router.post("/pause")
def pause() -> dict[str, Any]:
    _controller().pause()
    return {"ok": True, "paused": True}


@router.post("/resume")
def resume() -> dict[str, Any]:
    _controller().resume()
    return {"ok": True, "paused": False}


@router.post("/ceiling")
def set_ceiling(req: CostCeilingRequest) -> dict[str, Any]:
    _controller().set_cost_ceiling(req.ceiling_usd)
    return {"ok": True, "ceiling_usd": req.ceiling_usd}


@router.post("/paranoid")
def set_paranoid(req: ParanoidModeRequest) -> dict[str, Any]:
    _controller().set_paranoid_install(req.enabled)
    return {"ok": True, "paranoid_install": req.enabled}


@router.post("/force-tick")
def force_tick(req: _ForceTickRequest) -> dict[str, Any]:
    _controller().request_force_tick(req.role)
    return {"ok": True, "role": req.role}


@router.post("/restart")
def restart_controller() -> dict[str, Any]:
    """Shut down the running ``SwarmController`` and rebuild it from current
    settings. Used after the operator changes provider / model in the
    Settings panel."""
    from drone_graph.api.app import maybe_start_controller
    from drone_graph.api.settings import load_settings
    from drone_graph.api.state import get_state

    state = get_state()
    if state.controller is not None:
        try:
            state.controller.shutdown()
        finally:
            state.controller = None
            state.provider_name = None
            state.model = None
    started = maybe_start_controller(load_settings())
    state.needs_restart = False
    state.needs_restart_reason = None
    state.event_bus.publish("controller.restart_requested", started=started)
    return {"ok": True, "started": started}
