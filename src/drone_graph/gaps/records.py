from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class GapStatus(StrEnum):
    open = "open"
    in_progress = "in_progress"
    closed = "closed"
    failed = "failed"


class ModelTier(StrEnum):
    cheap = "cheap"
    standard = "standard"
    frontier = "frontier"


def _now() -> datetime:
    return datetime.now(UTC)


class Gap(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    description: str
    status: GapStatus = GapStatus.open
    nl_criteria: str | None = None
    structured_check: str | None = None
    cost_max_usd: float | None = None
    token_max: int | None = None
    model_tier: ModelTier = ModelTier.standard
    created_at: datetime = Field(default_factory=_now)
    closed_at: datetime | None = None


class Finding(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    summary: str
    payload_ref: str | None = None
    created_at: datetime = Field(default_factory=_now)
