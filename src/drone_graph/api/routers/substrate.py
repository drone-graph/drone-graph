"""Read endpoints: snapshot, gaps, findings, tools, claims."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from drone_graph.api.models import (
    ActiveDroneDTO,
    ClaimDTO,
    FindingDTO,
    GapDTO,
    InstallDTO,
    PendingInstallDTO,
    SnapshotDTO,
    SwarmStatusDTO,
    ToolDTO,
)
from drone_graph.api.state import get_state

router = APIRouter(prefix="/api", tags=["substrate"])


# ---- Status / snapshot -----------------------------------------------------


@router.get("/status", response_model=SwarmStatusDTO)
def get_status() -> SwarmStatusDTO:
    s = get_state()
    if s.controller is None:
        # No keys / no provider yet — Mission Control renders Settings in this
        # state. We still return a well-formed status so the frontend can show
        # "needs setup" without special-casing 404s.
        from datetime import UTC, datetime

        return SwarmStatusDTO(
            state="idle",  # type: ignore[arg-type]
            run_id="(not started)",
            provider=s.provider_name or "unconfigured",
            model=s.model or "unconfigured",
            started_at=datetime.now(UTC),
            paused=True,
            paranoid_install=False,
            cost_ceiling_usd=None,
            cost_spent_usd=0.0,
            gf_count=0,
            align_count=0,
            worker_count=0,
            consecutive_noops=0,
            active_drones=0,
            last_event_at=None,
            tick_seconds=1.0,
            needs_restart=s.needs_restart,
            needs_restart_reason=s.needs_restart_reason,
        )
    counters = s.controller.counters_snapshot()
    ctrl = s.controller.control
    return SwarmStatusDTO(
        state=s.controller.swarm_state(),  # type: ignore[arg-type]
        run_id=s.controller.run_id,
        provider=s.provider_name or "unknown",
        model=s.model or "unknown",
        started_at=s.controller.started_at,
        paused=ctrl.is_paused,
        paranoid_install=ctrl.paranoid_install,
        cost_ceiling_usd=ctrl.cost_ceiling_usd,
        cost_spent_usd=s.controller.cost_spent_usd(),
        gf_count=counters["gf_count"],
        align_count=counters["align_count"],
        worker_count=counters["worker_count"],
        consecutive_noops=counters["consecutive_noops"],
        active_drones=counters["active_drones"],
        last_event_at=None,
        tick_seconds=ctrl.tick_s,
        needs_restart=s.needs_restart,
        needs_restart_reason=s.needs_restart_reason,
    )


@router.get("/snapshot", response_model=SnapshotDTO)
def get_snapshot(
    findings_limit: int = Query(120, ge=1, le=2000),
) -> SnapshotDTO:
    """One-shot read of the whole substrate. Frontend calls this on first
    paint and on SSE reconnect to deterministically catch up."""
    s = get_state()
    gaps = [_gap_to_dto(g) for g in s.store.all_gaps()]
    edges = list(s.store.parent_edges())
    findings = [_finding_to_dto(f) for f in s.store.recent_findings(findings_limit)]
    tools = [_tool_to_dto(t) for t in s.tool_store.all_tools()]
    active = (
        [ActiveDroneDTO(**d) for d in s.controller.active_drones()]
        if s.controller is not None
        else []
    )
    claims = _claims_list(s)
    installs = _installs_list(s)
    status = get_status()
    return SnapshotDTO(
        status=status,
        gaps=gaps,
        parent_edges=edges,
        recent_findings=findings,
        active_drones=active,
        tools=tools,
        claims=claims,
        installs=installs,
    )


# ---- Gaps ------------------------------------------------------------------


@router.get("/gaps", response_model=list[GapDTO])
def list_gaps(status: str | None = None) -> list[GapDTO]:
    s = get_state()
    gaps = s.store.all_gaps()
    if status:
        gaps = [g for g in gaps if g.status.value == status]
    return [_gap_to_dto(g) for g in gaps]


@router.get("/gaps/{gap_id}", response_model=GapDTO)
def get_gap(gap_id: str) -> GapDTO:
    g = get_state().store.get(gap_id)
    if g is None:
        raise HTTPException(status_code=404, detail=f"no gap with id {gap_id}")
    return _gap_to_dto(g)


@router.get("/gaps/{gap_id}/findings", response_model=list[FindingDTO])
def findings_for_gap(gap_id: str, limit: int = Query(50, ge=1, le=2000)) -> list[FindingDTO]:
    s = get_state()
    g = s.store.get(gap_id)
    if g is None:
        raise HTTPException(status_code=404, detail=f"no gap with id {gap_id}")
    rows = [
        f
        for f in s.store.all_findings()
        if g.id in f.affected_gap_ids
    ]
    rows = rows[-limit:]
    return [_finding_to_dto(f) for f in rows]


# ---- Findings --------------------------------------------------------------


@router.get("/findings", response_model=list[FindingDTO])
def list_findings(
    limit: int = Query(200, ge=1, le=5000),
    author: str | None = None,
    kind: str | None = None,
) -> list[FindingDTO]:
    s = get_state()
    rows = s.store.all_findings()
    if author:
        rows = [f for f in rows if f.author.value == author]
    if kind:
        rows = [f for f in rows if f.kind.value == kind]
    rows = rows[-limit:]
    return [_finding_to_dto(f) for f in rows]


@router.get("/findings/{finding_id}", response_model=FindingDTO)
def get_finding(finding_id: str) -> FindingDTO:
    s = get_state()
    matches = [f for f in s.store.all_findings() if f.id.startswith(finding_id)]
    if not matches:
        raise HTTPException(status_code=404, detail=f"no finding starting {finding_id!r}")
    if len(matches) > 1:
        raise HTTPException(
            status_code=400, detail=f"ambiguous id {finding_id!r}: {len(matches)} matches"
        )
    return _finding_to_dto(matches[0])


# ---- Tools / marketplace ---------------------------------------------------


@router.get("/tools", response_model=list[ToolDTO])
def list_tools() -> list[ToolDTO]:
    return [_tool_to_dto(t) for t in get_state().tool_store.all_tools()]


@router.get("/tools/{name}", response_model=ToolDTO)
def get_tool(name: str) -> ToolDTO:
    t = get_state().tool_store.get(name)
    if t is None:
        raise HTTPException(status_code=404, detail=f"no tool {name!r}")
    return _tool_to_dto(t)


# ---- Active drones ---------------------------------------------------------


@router.get("/drones/active", response_model=list[ActiveDroneDTO])
def active_drones() -> list[ActiveDroneDTO]:
    s = get_state()
    if s.controller is None:
        return []
    return [ActiveDroneDTO(**d) for d in s.controller.active_drones()]


# ---- Signals / internals ---------------------------------------------------


@router.get("/signals/claims", response_model=list[ClaimDTO])
def list_claims() -> list[ClaimDTO]:
    return _claims_list(get_state())


@router.get("/signals/installs", response_model=list[InstallDTO])
def list_installs() -> list[InstallDTO]:
    return _installs_list(get_state())


@router.get("/installs/pending", response_model=list[PendingInstallDTO])
def list_pending_installs() -> list[PendingInstallDTO]:
    s = get_state()
    if s.controller is None:
        return []
    return [
        PendingInstallDTO(**_pending_install_to_dict(p))
        for p in s.controller.list_pending_installs()
    ]


# ---- DTO mappers -----------------------------------------------------------


def _gap_to_dto(g: Any) -> GapDTO:
    return GapDTO(
        id=g.id,
        intent=g.intent,
        criteria=g.criteria,
        status=g.status.value,
        reopen_count=g.reopen_count,
        retire_reason=g.retire_reason,
        model_tier=g.model_tier.value,
        created_at=g.created_at,
        tool_loadout=list(g.tool_loadout),
        tool_suggestions=list(g.tool_suggestions),
        context_preload=list(g.context_preload),
        preset_kind=g.preset_kind,
    )


def _finding_to_dto(f: Any) -> FindingDTO:
    return FindingDTO(
        id=f.id,
        tick=f.tick,
        author=f.author.value,
        kind=f.kind.value,
        summary=f.summary,
        affected_gap_ids=list(f.affected_gap_ids),
        artefact_paths=list(f.artefact_paths),
        created_at=f.created_at,
        invocation_tool_name=f.invocation_tool_name,
        invocation_outcome=f.invocation_outcome,
        invocation_provider=f.invocation_provider,
        invocation_model=f.invocation_model,
        invocation_cost_usd=f.invocation_cost_usd,
    )


def _tool_to_dto(t: Any) -> ToolDTO:
    return ToolDTO(
        name=t.name,
        description=t.description,
        kind=t.kind.value,
        trust_tier=t.trust_tier.value,
        usage=t.usage,
        install_commands=list(t.install_commands),
        depends_on=list(t.depends_on),
        flagged_by_alignment=t.flagged_by_alignment,
        needs_venv=t.needs_venv,
        last_used_at=t.last_used_at,
        created_at=t.created_at,
        deprecated_at=t.deprecated_at,
        deprecated_reason=t.deprecated_reason,
        installed_by_drone_id=t.installed_by_drone_id,
        skill_package_id=t.skill_package_id,
    )


def _claims_list(s: Any) -> list[ClaimDTO]:
    """Read every active claim directly from the signals DB."""
    conn = s.signals._conn  # noqa: SLF001 — internal access is the cleanest route here
    with s.signals._lock:  # noqa: SLF001
        rows = conn.execute(
            "SELECT kind, key, drone_id, acquired_at, expires_at, "
            "       cancelled, metadata FROM claims"
        ).fetchall()
    import json as _json
    out: list[ClaimDTO] = []
    for r in rows:
        meta = r[6]
        out.append(
            ClaimDTO(
                kind=r[0],
                key=r[1],
                drone_id=r[2],
                acquired_at=float(r[3]),
                expires_at=float(r[4]),
                cancelled=bool(r[5]),
                metadata=_json.loads(meta) if meta else None,
            )
        )
    return out


def _installs_list(s: Any) -> list[InstallDTO]:
    conn = s.signals._conn  # noqa: SLF001
    with s.signals._lock:  # noqa: SLF001
        rows = conn.execute(
            "SELECT key, installed_by, installed_at, install_commands, usage FROM installs"
        ).fetchall()
    import json as _json
    return [
        InstallDTO(
            key=r[0],
            installed_by=r[1],
            installed_at=float(r[2]),
            install_commands=_json.loads(r[3]) if r[3] else [],
            usage=r[4],
        )
        for r in rows
    ]


def _pending_install_to_dict(p: Any) -> dict[str, Any]:
    return {
        "id": p.id,
        "tool_name": p.tool_name,
        "description": p.description,
        "install_commands": list(p.install_commands),
        "usage": p.usage,
        "requested_by_drone_id": p.requested_by_drone_id,
        "requested_at": p.requested_at if isinstance(p.requested_at, datetime) else datetime.fromisoformat(str(p.requested_at)),
    }
