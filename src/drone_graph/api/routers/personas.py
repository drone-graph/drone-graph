"""Persona registry endpoints.

Personas track swarm-managed identities and the lifecycle of each
capability (email / github / card / …). The UI panel reads from here
to render the capability matrix — what each persona can actually do
in the world vs. what it merely aspires to.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from drone_graph.api.state import get_state
from drone_graph.personas import (
    Capability,
    CapabilityStatus,
    Persona,
    PersonaError,
    PersonaStore,
)

router = APIRouter(prefix="/api/personas", tags=["personas"])


def _serialize(p: Persona) -> dict[str, Any]:
    return {
        "name": p.name,
        "display_name": p.display_name,
        "backed_by_real_human": p.backed_by_real_human,
        "bio": p.bio,
        "notes": p.notes,
        "ssh_fingerprint": p.ssh_fingerprint,
        "browser_profiles": list(p.browser_profiles),
        "capabilities": [
            {
                "key": c.key,
                "desired_value": c.desired_value,
                "actual_value": c.actual_value,
                "status": c.status.value,
                "credential_ref": c.credential_ref,
                "notes": c.notes,
                "updated_at": c.updated_at.isoformat(),
                "verified_at": c.verified_at.isoformat() if c.verified_at else None,
            }
            for c in p.capabilities
        ],
        "created_at": p.created_at.isoformat(),
        "created_by_drone_id": p.created_by_drone_id,
    }


def _store() -> PersonaStore:
    state = get_state()
    return PersonaStore(state.substrate)


@router.get("")
def list_personas() -> list[dict[str, Any]]:
    return [_serialize(p) for p in _store().list()]


@router.get("/{name}")
def get_persona(name: str) -> dict[str, Any]:
    p = _store().get(name)
    if p is None:
        raise HTTPException(status_code=404, detail=f"no persona {name!r}")
    return _serialize(p)


class _CapabilityPatch(BaseModel):
    desired_value: str | None = None
    actual_value: str | None = None
    status: str
    credential_ref: str | None = None
    notes: str | None = None


@router.post("/{name}/capabilities/{key}")
def upsert_capability(
    name: str, key: str, patch: _CapabilityPatch
) -> dict[str, Any]:
    """Operator-side capability edit. Drones use the cm_set_persona_capability
    builtin; this endpoint lets the operator do the same from the UI."""
    try:
        status = CapabilityStatus(patch.status.strip().lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="status must be pending | registered | verified",
        )
    cap = Capability(
        key=key,
        desired_value=(patch.desired_value.strip() if patch.desired_value else None),
        actual_value=(patch.actual_value.strip() if patch.actual_value else None),
        status=status,
        credential_ref=(patch.credential_ref.strip() if patch.credential_ref else None),
        notes=(patch.notes.strip() if patch.notes else None),
    )
    try:
        updated = _store().upsert_capability(name, cap)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _serialize(updated)


class _RealHumanPatch(BaseModel):
    backed_by_real_human: bool


@router.post("/{name}/backed-by-real-human")
def set_backed_by_real_human(
    name: str, patch: _RealHumanPatch
) -> dict[str, Any]:
    """Operator marks (or unmarks) a persona as backed by a real human.
    Drones cannot set this themselves — it's the operator's source of
    truth that a counterparty interaction with this persona will
    eventually reach a real person."""
    store = _store()
    p = store.get(name)
    if p is None:
        raise HTTPException(status_code=404, detail=f"no persona {name!r}")
    p.backed_by_real_human = bool(patch.backed_by_real_human)
    try:
        updated = store.register(p)
    except (PersonaError, Exception) as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    return _serialize(updated)
