"""Operator settings + action inbox.

  * ``GET/POST /api/settings`` — read/update keys, default provider/model,
    default ceiling, paranoid default, sound preference. Saving keys
    triggers controller (re)initialization.
  * ``GET /api/inbox`` — pending ``requires_user_action`` findings (sign-in,
    purchase approval, secret needed, MFA, etc.) that drones have emitted
    and the user hasn't responded to yet.
  * ``POST /api/inbox/{finding_id}/resolve`` — user marks a block resolved.
    Writes a ``note`` finding so Gap Finding sees the unblock signal next
    tick. Secrets / OAuth tokens never travel through this endpoint —
    drones pull them from the local secrets store (Settings) by name.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from drone_graph.api import settings as cfg
from drone_graph.api.state import get_state
from drone_graph.gaps import FindingAuthor, FindingKind

router = APIRouter(prefix="/api", tags=["settings"])


# ---- Model registry ------------------------------------------------------


class ModelDTO(BaseModel):
    """A row in the model registry, flattened for the Settings dropdown."""

    dgraph_model_id: str
    provider: str
    vendor_model_id: str
    deprecated: bool
    input_price_per_million_usd: float
    output_price_per_million_usd: float
    capabilities: list[str]


class ModelRegistryDTO(BaseModel):
    populated: bool
    # ``{provider: {tier: dgraph_model_id}}``. Outer keys are provider names,
    # inner keys are ModelTier values (nano/mini/standard/advanced/frontier).
    tier_defaults_by_provider: dict[str, dict[str, str]]
    tiers: list[str]
    models: list[ModelDTO]


@router.get("/models", response_model=ModelRegistryDTO)
def list_models() -> ModelRegistryDTO:
    """The model registry as the Settings panel renders it. Empty registry
    (bootstrap state) returns ``populated=False`` so the UI can fall back
    to a free-text input."""
    from drone_graph.gaps.records import ModelTier
    from drone_graph.model_registry.registry import ModelRegistry

    tiers = [t.value for t in ModelTier]
    try:
        reg = ModelRegistry.load_auto()
    except Exception:
        return ModelRegistryDTO(
            populated=False,
            tier_defaults_by_provider={},
            tiers=tiers,
            models=[],
        )
    if not reg.is_populated:
        return ModelRegistryDTO(
            populated=False,
            tier_defaults_by_provider={},
            tiers=tiers,
            models=[],
        )
    data = reg._data  # noqa: SLF001 — the wrapper is a thin read-side view
    return ModelRegistryDTO(
        populated=True,
        tier_defaults_by_provider={
            prov.value: {tier.value: gid for tier, gid in ladder.items()}
            for prov, ladder in data.tier_defaults_by_provider.items()
        },
        tiers=tiers,
        models=[
            ModelDTO(
                dgraph_model_id=m.dgraph_model_id,
                provider=m.provider.value,
                vendor_model_id=m.vendor_model_id,
                deprecated=m.deprecated,
                input_price_per_million_usd=m.input_price_per_million_usd,
                output_price_per_million_usd=m.output_price_per_million_usd,
                capabilities=list(m.capabilities),
            )
            for m in data.models
        ],
    )


# ---- Settings -------------------------------------------------------------


@router.get("/settings", response_model=cfg.SettingsView)
def read_settings() -> cfg.SettingsView:
    return cfg.view(cfg.load_settings())


@router.post("/settings", response_model=cfg.SettingsView)
def update_settings(patch: cfg.SettingsPatch) -> cfg.SettingsView:
    current = cfg.load_settings()
    merged = cfg.merge_patch(current, patch)
    saved = cfg.save_settings(merged)
    cfg.apply_to_env(saved)
    # If keys are now present and the controller hasn't been built yet, start
    # it. Imported lazily so settings reads stay cheap.
    from drone_graph.api.app import maybe_start_controller

    state = get_state()
    if state.controller is None:
        maybe_start_controller(saved)
    else:
        # Provider/model/tier-override changes can't be applied to a running
        # controller; we pause the swarm and surface a "restart swarm" banner
        # in the UI. Cost ceiling / paranoid default / sound are applied live
        # below.
        wants_restart_reasons: list[str] = []
        if saved.default_provider and saved.default_provider != state.provider_name:
            wants_restart_reasons.append(
                f"provider changed ({state.provider_name} → {saved.default_provider})"
            )
        if saved.default_model and saved.default_model != state.model:
            wants_restart_reasons.append(
                f"model changed ({state.model} → {saved.default_model})"
            )
        if dict(saved.tier_overrides) != dict(current.tier_overrides):
            wants_restart_reasons.append("tier overrides changed")
        if wants_restart_reasons:
            state.controller.pause()
            state.needs_restart = True
            state.needs_restart_reason = "; ".join(wants_restart_reasons)
            state.event_bus.publish(
                "controller.needs_restart",
                reason=state.needs_restart_reason,
            )
        # Apply live-tunable settings either way.
        if saved.default_cost_ceiling_usd is not None:
            state.controller.set_cost_ceiling(saved.default_cost_ceiling_usd)
        state.controller.set_paranoid_install(bool(saved.default_paranoid_install))
    return cfg.view(saved)


# ---- Action inbox --------------------------------------------------------


class InboxItem(BaseModel):
    """A pending ``requires_user_action`` block awaiting operator response."""

    finding_id: str
    tick: int
    created_at: datetime
    summary: str
    affected_gap_ids: list[str]
    action_type: Literal[
        "credential", "oauth", "sign_in", "purchase", "approval", "mfa",
        "identity", "other",
    ]
    details: dict[str, Any]
    artefact_paths: list[str]


class InboxResolveRequest(BaseModel):
    """Operator response to a block.

    For ``credential`` / ``oauth`` / ``mfa``: the operator stores the secret
    in Settings (under the named ``details.secret_name``) themselves; the
    resolve note just says *that it's been done*. For ``purchase`` /
    ``approval``: the operator records the outcome (approved / declined /
    completed externally) and optionally an external id (order id, etc.).
    """

    outcome: Literal["resolved", "declined", "skipped"] = "resolved"
    note: str | None = None
    external_id: str | None = None


@router.get("/inbox", response_model=list[InboxItem])
def list_inbox() -> list[InboxItem]:
    s = get_state()
    resolved_ids = _resolved_block_ids(s)
    decided_identity_gaps = _decided_identity_gap_ids(s)
    # Pre-fetch gap status so we can hide blocks for gaps that no longer
    # exist or have been retired since the drone emitted them. A user-input
    # finding might unstick a retired subtree, so we only treat the gap as
    # "dead for inbox purposes" once it's actually retired (not just filled).
    gap_status: dict[str, str] = {g.id: g.status.value for g in s.store.all_gaps()}
    out: list[InboxItem] = []
    for f in s.store.all_findings():
        if f.kind.value != FindingKind.requires_user_action.value:
            continue
        if f.id in resolved_ids:
            continue
        # Skip when every affected gap is retired or missing.
        if f.affected_gap_ids:
            live = [
                gid
                for gid in f.affected_gap_ids
                if gap_status.get(gid) in ("unfilled", "filled")
            ]
            if not live:
                continue
        action_type, details = _parse_block_details(f.summary, f.artefact_paths)
        # Identity blocks auto-resolve when the operator has already
        # decided (grant/deny). Skips the extra "mark resolved" click.
        if action_type == "identity":
            if any(gid in decided_identity_gaps for gid in f.affected_gap_ids):
                continue
        out.append(
            InboxItem(
                finding_id=f.id,
                tick=f.tick,
                created_at=f.created_at,
                summary=_one_line(f.summary),
                affected_gap_ids=list(f.affected_gap_ids),
                action_type=action_type,
                details=details,
                artefact_paths=list(f.artefact_paths),
            )
        )
    return out


@router.post("/inbox/{finding_id}/resolve")
def resolve_inbox(finding_id: str, req: InboxResolveRequest) -> dict[str, Any]:
    s = get_state()
    matches = [f for f in s.store.all_findings() if f.id.startswith(finding_id)]
    if not matches:
        raise HTTPException(status_code=404, detail=f"no block {finding_id!r}")
    block = matches[0]
    if block.kind.value != FindingKind.requires_user_action.value:
        raise HTTPException(
            status_code=400,
            detail=f"finding {block.id} is not a requires_user_action block",
        )
    # Compose a clear unblock note. Secret values never travel through this
    # endpoint — drones pull them from the local Settings store by name.
    summary_lines = [
        f"User responded to block {block.id}: {req.outcome}.",
    ]
    if req.note:
        summary_lines.append(req.note.strip())
    if req.external_id:
        summary_lines.append(f"external_id={req.external_id}")
    summary = "\n".join(summary_lines)

    tick = _next_tick(s)
    note = s.store.append_finding(
        tick=tick,
        author=FindingAuthor.user,
        kind=FindingKind.note,
        summary=summary,
        affected_gap_ids=list(block.affected_gap_ids),
        # Reference the original block so we can detect resolution later.
        artefact_paths=[f"inbox-resolution:{block.id}"],
    )
    s.event_bus.publish(
        "user.inbox_resolved",
        block_id=block.id,
        finding_id=note.id,
        outcome=req.outcome,
        tick=tick,
    )
    return {"resolved": True, "note_id": note.id}


# ---- Internals -----------------------------------------------------------


def _next_tick(s: Any) -> int:
    sched = s.controller._scheduler if s.controller else None  # noqa: SLF001
    if sched is None:
        # No scheduler yet — synthesize a tick from now() so findings still
        # order monotonically.
        return int(datetime.utcnow().timestamp())
    sched.tick += 1
    return int(sched.tick)


def _resolved_block_ids(s: Any) -> set[str]:
    """A block is considered resolved when there's a later ``note`` finding
    with an ``inbox-resolution:<block_id>`` artefact path."""
    out: set[str] = set()
    for f in s.store.all_findings():
        if f.kind.value != FindingKind.note.value:
            continue
        for p in f.artefact_paths:
            if isinstance(p, str) and p.startswith("inbox-resolution:"):
                out.add(p.split(":", 1)[1])
    return out


def _decided_identity_gap_ids(s: Any) -> set[str]:
    """Gap ids that already have an identity grant OR deny note. Used to
    auto-resolve identity-request inbox items the moment the operator
    clicks grant or deny, without forcing them to also click 'resolve'."""
    out: set[str] = set()
    for f in s.store.all_findings():
        if f.kind.value != FindingKind.note.value:
            continue
        for p in f.artefact_paths:
            if isinstance(p, str) and (
                p.startswith("identity-grant:") or p.startswith("identity-deny:")
            ):
                out.add(p.split(":", 1)[1])
    return out


def _parse_block_details(
    summary: str, artefact_paths: list[str]
) -> tuple[
    Literal[
        "credential", "oauth", "sign_in", "purchase", "approval", "mfa",
        "identity", "other",
    ],
    dict[str, Any],
]:
    """Drones emit blocks via ``cm_write_finding(kind=requires_user_action, …)``.
    By convention, structured detail lives in an artefact JSON file whose path
    is recorded in ``artefact_paths``. If we can read it, we get a rich
    payload; if not, we infer the type from the summary text."""
    # Sentinel-style artefact paths take precedence — the scheduler emits
    # these for policy-blocks (identity approval) that don't need their
    # own JSON file.
    for p in artefact_paths:
        if isinstance(p, str) and p.startswith("identity-request:"):
            return "identity", {
                "summary": summary,
                "gap_id": p.split(":", 1)[1],
            }
    for p in artefact_paths:
        if not isinstance(p, str) or not p.endswith(".json"):
            continue
        try:
            raw = Path(p).read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            action_type = data.get("action_type", "other")
            if action_type not in (
                "credential",
                "oauth",
                "sign_in",
                "purchase",
                "approval",
                "mfa",
                "identity",
                "other",
            ):
                action_type = "other"
            return action_type, data  # type: ignore[return-value]

    text = summary.lower()
    if "oauth" in text or "sign in" in text or "sign-in" in text or "login" in text:
        return "oauth", {"summary": summary}
    if "purchase" in text or "buy" in text or "$ " in text or "checkout" in text:
        return "purchase", {"summary": summary}
    if "mfa" in text or "otp" in text or "verification code" in text or "2fa" in text:
        return "mfa", {"summary": summary}
    if "credential" in text or "api key" in text or "token" in text:
        return "credential", {"summary": summary}
    if "approval" in text or "approve" in text:
        return "approval", {"summary": summary}
    return "other", {"summary": summary}


def _one_line(s: str) -> str:
    i = s.find("\n")
    return (s if i == -1 else s[:i]).strip()
