from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class GapStatus(StrEnum):
    unfilled = "unfilled"
    filled = "filled"
    retired = "retired"


class ModelTier(StrEnum):
    cheap = "cheap"
    standard = "standard"
    frontier = "frontier"


class FindingAuthor(StrEnum):
    gap_finding = "gap_finding"
    alignment = "alignment"
    worker = "worker"
    user = "user"
    # Deterministic system bookkeeping. Today: auto-rollup fills when all of a
    # parent's non-retired children are filled. Alignment can still contest and
    # Gap Finding can reopen, so "system" is never authoritative on its own.
    system = "system"


class FindingKind(StrEnum):
    # Structural edits authored by Gap Finding.
    decompose = "decompose"
    create = "create"
    retire = "retire"
    reopen = "reopen"
    noop = "noop"
    rewrite_intent = "rewrite_intent"
    # Worker outcomes.
    fill = "fill"
    fail = "fail"
    # Worker cancelled mid-flight (gap retired or budget exceeded). Substrate
    # never deletes the work the drone did before the cancel — those findings
    # remain attached to the (now retired) gap as audit.
    cancelled = "cancelled"
    budget_exceeded = "budget_exceeded"
    # Alignment observations.
    alignment_invalidated_premise = "alignment_invalidated_premise"
    alignment_unmet_intent = "alignment_unmet_intent"
    alignment_missing_subtree = "alignment_missing_subtree"
    alignment_no_issue = "alignment_no_issue"
    # External signals.
    user_input = "user_input"
    # Freeform drone note that does not close or abandon a gap.
    note = "note"


def _now() -> datetime:
    return datetime.now(UTC)


class Gap(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4()))
    intent: str
    criteria: str
    status: GapStatus = GapStatus.unfilled
    reopen_count: int = 0
    retire_reason: str | None = None
    model_tier: ModelTier = ModelTier.standard
    created_at: datetime = Field(default_factory=_now)
    # Tool policy (set by Gap Finding at creation, or by substrate init for
    # preset gaps). All three lists are tool *names* — the registry resolves
    # them to schemas + dispatchers.
    tool_loadout: list[str] = Field(default_factory=list)
    """Always-available tools for the drone working this gap. Empty list means
    "use the default emergent loadout" (terminal_run, cm_read_gap,
    cm_write_finding, plus the universal cm_* query tools)."""
    tool_suggestions: list[str] = Field(default_factory=list)
    """Tools recommended by Gap Finding but not preloaded; the drone can ask
    for them via cm_request_tool(name) during the run."""
    context_preload: list[str] = Field(default_factory=list)
    """Names of preload queries to run at dispatch and inject into the drone's
    initial context (e.g. ``recent_findings``, ``leaves``, ``tree_shape``).
    Used by preset gaps to avoid a wasted "obvious query" turn at the start."""
    # If this gap is a preset (Gap Finding, Alignment, etc.), this is its
    # stable preset id; ``None`` for emergent gaps. Preset gaps are minted at
    # substrate init and are never closed or retired by the loop.
    preset_kind: str | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4()))
    tick: int
    author: FindingAuthor
    kind: FindingKind
    summary: str
    affected_gap_ids: list[str] = Field(default_factory=list)
    # Paths to files on disk produced or referenced by this finding. The finding
    # stays short; substantive output lives on disk and is pointed at here so
    # other drones and tooling can retrieve it directly.
    artefact_paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
