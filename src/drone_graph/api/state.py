"""App-wide singletons for the API server.

Held in module-level globals so request handlers can grab them without
threading them through every endpoint. Built once at startup, torn down on
shutdown.

The state is **two-phase**:

  * Phase 1 (always available): substrate connection, gap store, tool store,
    signal store, event bus, settings. Enough to serve ``/api/settings`` and
    the SPA when keys haven't been configured yet.
  * Phase 2 (provider configured): a ``SwarmController`` is constructed with
    a resolved provider + model. Until then ``state.controller`` is ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from drone_graph.api.control import SwarmController
from drone_graph.api.events import EventBus
from drone_graph.gaps import GapStore
from drone_graph.signals import SQLiteSignalStore
from drone_graph.substrate import Substrate
from drone_graph.tools import ToolStore


@dataclass
class AppState:
    substrate: Substrate
    store: GapStore
    tool_store: ToolStore
    signals: SQLiteSignalStore
    event_bus: EventBus
    controller: SwarmController | None = None
    provider_name: str | None = None
    model: str | None = None
    # Set when Settings changes a knob the running controller can't honor
    # without a restart (provider, model). Cleared by ``POST /api/control/restart``.
    needs_restart: bool = False
    needs_restart_reason: str | None = None


_STATE: AppState | None = None


def set_state(state: AppState) -> None:
    global _STATE
    _STATE = state


def get_state() -> AppState:
    if _STATE is None:
        raise RuntimeError("AppState not initialized; FastAPI startup did not run")
    return _STATE


def require_controller() -> SwarmController:
    s = get_state()
    if s.controller is None:
        raise RuntimeError(
            "swarm controller is not configured — set provider keys in /api/settings"
        )
    return s.controller


def clear_state() -> None:
    global _STATE
    _STATE = None


def to_dict(state: AppState) -> dict[str, Any]:
    return {
        "provider": state.provider_name,
        "model": state.model,
        "run_id": state.controller.run_id if state.controller else None,
    }
