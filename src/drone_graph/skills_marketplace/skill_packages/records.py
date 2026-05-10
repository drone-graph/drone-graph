"""Pydantic records for on-disk skill packages (SKILL.md + optional metadata.json)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SkillPackageError(ValueError):
    """Invalid skill package directory, missing SKILL.md, or bad metadata.json."""


class SkillPackageMetadata(BaseModel):
    """Optional metadata.json beside SKILL.md."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    triggers: list[str] = Field(default_factory=list)
    version: str | None = None
    description: str | None = None


class ParsedSkillPackage(BaseModel):
    """Validated skill package loaded from disk."""

    skill_id: str
    title: str
    body: str
    triggers: list[str] = Field(default_factory=list)
    version: str | None = None
    description: str | None = None
