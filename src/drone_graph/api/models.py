"""API DTOs.

Pydantic models that flatten the substrate's records for the frontend. We
deliberately don't re-export the in-process records: the wire format is a
stable contract; the in-process records can grow fields without breaking
clients.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---- Gaps ------------------------------------------------------------------


class GapDTO(BaseModel):
    """Wire shape for a Gap node."""

    model_config = ConfigDict(extra="ignore")

    id: str
    intent: str
    criteria: str
    status: Literal["unfilled", "filled", "retired"]
    reopen_count: int
    retire_reason: str | None = None
    model_tier: Literal[
        "nano", "mini", "standard", "advanced", "frontier",
        # legacy values from earlier tier scheme — kept so existing nodes
        # round-trip without validation failures.
        "cheap",
    ]
    created_at: datetime
    tool_loadout: list[str] = Field(default_factory=list)
    tool_suggestions: list[str] = Field(default_factory=list)
    context_preload: list[str] = Field(default_factory=list)
    preset_kind: str | None = None
    uses_operator_identity: bool = False
    identity_approved: bool = False
    identity_denied_reason: str | None = None
    max_output_tokens: int | None = None
    paused: bool = False


# ---- Findings --------------------------------------------------------------


class FindingDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    tick: int
    author: Literal["gap_finding", "alignment", "worker", "user", "system"]
    kind: str
    summary: str
    affected_gap_ids: list[str] = Field(default_factory=list)
    artefact_paths: list[str] = Field(default_factory=list)
    created_at: datetime
    invocation_tool_name: str | None = None
    invocation_outcome: str | None = None
    invocation_provider: str | None = None
    invocation_model: str | None = None
    invocation_cost_usd: float | None = None


# ---- Tools -----------------------------------------------------------------


class ToolDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str
    kind: Literal["builtin", "installed"]
    trust_tier: Literal["high", "standard", "low", "blocked"]
    usage: str = ""
    install_commands: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    flagged_by_alignment: bool = False
    needs_venv: bool = False
    last_used_at: datetime
    created_at: datetime
    deprecated_at: datetime | None = None
    deprecated_reason: str | None = None
    installed_by_drone_id: str | None = None
    skill_package_id: str | None = None


# ---- Drones (live, from controller) ----------------------------------------


class ActiveDroneDTO(BaseModel):
    """A drone currently in-flight, as the controller sees it."""

    drone_id: str
    role: Literal[
        "preset:gap_finding", "preset:alignment", "worker"
    ]
    gap_id: str
    tick: int
    spawned_at: datetime
    cancel_signaled: bool = False
    tape_path: str | None = None
    # Vital signs, populated by tailing the per-drone tape file.
    turn: int | None = None
    max_turns: int | None = None
    last_command: str | None = None
    tail_lines: list[str] = Field(default_factory=list)
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    # Routing — the model the scheduler ACTUALLY spawned this drone on,
    # not the operator's default. Workers vary by gap tier; presets pin to
    # advanced tier.
    provider: str | None = None
    model: str | None = None
    model_tier: str | None = None


# ---- Signals / claims ------------------------------------------------------


class ClaimDTO(BaseModel):
    kind: str
    key: str
    drone_id: str
    acquired_at: float
    expires_at: float
    cancelled: bool = False
    metadata: dict[str, Any] | None = None


class InstallDTO(BaseModel):
    key: str
    installed_by: str
    installed_at: float
    install_commands: list[str] = Field(default_factory=list)
    usage: str | None = None


# ---- Controller / swarm status --------------------------------------------


SwarmState = Literal["idle", "active", "paused", "cost_locked", "resting"]


class SwarmStatusDTO(BaseModel):
    state: SwarmState
    run_id: str
    provider: str
    model: str
    started_at: datetime
    paused: bool
    paranoid_install: bool
    cost_ceiling_usd: float | None
    cost_spent_usd: float
    gf_count: int
    align_count: int
    worker_count: int
    consecutive_noops: int
    active_drones: int
    last_event_at: datetime | None
    # Slow tick when at rest, fast tick when active.
    tick_seconds: float
    # True when Settings changes (provider/model) have been saved that the
    # running scheduler doesn't yet honor. UI surfaces a "restart swarm"
    # banner; ``POST /api/control/restart`` clears it.
    needs_restart: bool = False
    needs_restart_reason: str | None = None


# ---- Snapshot --------------------------------------------------------------


class SnapshotDTO(BaseModel):
    """One-shot read of everything the UI cares about. Used on first paint
    and on SSE reconnect to deterministically catch up."""

    status: SwarmStatusDTO
    gaps: list[GapDTO]
    parent_edges: list[tuple[str, str]]
    recent_findings: list[FindingDTO]
    active_drones: list[ActiveDroneDTO]
    tools: list[ToolDTO]
    claims: list[ClaimDTO]
    installs: list[InstallDTO]


# ---- Edit requests --------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    target_gap_id: str | None = None
    """When set, the user_input finding is attached to this gap (the inline-
    injection surface). When ``None``, the finding lands on
    ``preset:gap_finding`` and the swarm decides how to frame it."""


class RewriteIntentRequest(BaseModel):
    new_intent: str
    new_criteria: str
    rationale: str = "Rewritten by user via mission control"


class RetireRequest(BaseModel):
    reason: str


class CancelDroneRequest(BaseModel):
    reason: str = "user_cancelled"


class CostCeilingRequest(BaseModel):
    ceiling_usd: float | None
    """``None`` clears the ceiling (unlimited)."""


class TrustTierRequest(BaseModel):
    tier: Literal["high", "standard", "low", "blocked"]


class ParanoidModeRequest(BaseModel):
    enabled: bool


# ---- Marketplace install approval -----------------------------------------


class PendingInstallDTO(BaseModel):
    """A pending install request awaiting user approval in paranoid mode."""

    id: str
    tool_name: str
    description: str
    install_commands: list[str]
    usage: str
    requested_by_drone_id: str
    requested_at: datetime


class InstallApprovalRequest(BaseModel):
    approve: bool


# ---- Streamed events -------------------------------------------------------


class StreamEvent(BaseModel):
    """Wire shape for SSE events. ``event`` field corresponds to the SSE
    ``event:`` line; ``payload`` is the JSON body."""

    event: str
    payload: dict[str, Any]
