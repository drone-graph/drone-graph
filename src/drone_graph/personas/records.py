"""Persona pydantic record + on-disk layout helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_NAME_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PersonaError(ValueError):
    """Raised on invalid persona names / duplicate creation / missing keys."""


def personas_root() -> Path:
    """``~/.config/drone-graph/personas/`` — created on first call."""
    p = Path.home() / ".config" / "drone-graph" / "personas"
    p.mkdir(parents=True, exist_ok=True)
    return p


def persona_dir(name: str) -> Path:
    """On-disk dir for a persona. Raises ``PersonaError`` on invalid name."""
    if not _NAME_OK.match(name):
        raise PersonaError(
            f"invalid persona name {name!r} — use 1-64 chars of [A-Za-z0-9_-] only"
        )
    p = personas_root() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> datetime:
    return datetime.now(UTC)


class Persona(BaseModel):
    """A swarm-managed identity. Persists in two places: the on-disk
    dir under ``persona_dir(name)`` and a ``:Persona`` node in Neo4j.

    ``swarm-zero`` is the baseline persona minted at bootstrap. Drones
    mint new ones via ``cm_create_persona`` when they need a fresh
    identity for an external account.
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    display_name: str
    email: str
    github_handle: str | None = None
    linkedin_handle: str | None = None
    bio: str | None = None
    """Optional one-line backstory the drone can paste into 'about' fields."""
    browser_profiles: list[str] = Field(default_factory=list)
    """Names of ``cm_browser`` profiles bundled with this persona —
    these hold any cookies / saved logins for services this persona
    has authed into."""
    ssh_fingerprint: str | None = None
    """SHA256 fingerprint of the persona's id_ed25519.pub, recorded
    so future drones can spot ``Permission denied (publickey)`` and
    know which key to register."""
    created_at: datetime = Field(default_factory=_now)
    created_by_drone_id: str | None = None
    notes: str | None = None
    """Operator can hand-edit free text in the manifest; the registry
    reads it back so personas can carry intent / scope across drones."""
