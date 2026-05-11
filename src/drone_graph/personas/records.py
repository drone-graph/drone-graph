"""Persona pydantic record + on-disk layout helpers.

A persona's *fields are goals, not claims*. The persona registry tracks
**capabilities** with an explicit lifecycle (pending → registered →
verified). A drone calling ``cm_use_persona("alice-renewable")`` gets
back the status of each capability, not a misleading metadata blob —
it can see "email pending, github pending" and know it cannot yet send
mail or push under this persona.

A capability has a stable key (``email``, ``github``, ``card``, …),
a desired value (the email address we want, the handle we want), a
status, and an optional credential-storage pointer that tells drones
where to find the secret (a Settings key, an env var, an opaque ref).

Capability columns are extensible: a drone working a persona can
introduce a new key (``stripe_express_account``) with its own desired
value + status. The persona's "shape" isn't frozen at creation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_NAME_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PersonaError(ValueError):
    """Raised on invalid persona names / duplicate creation / missing keys."""


class CapabilityStatus(StrEnum):
    """Lifecycle of a persona capability.

    * ``pending`` — the persona wants this (someone wrote down a
      desired value) but no real-world entity has been acquired yet.
      Drones must NOT assume the capability is usable.
    * ``registered`` — a real-world entity has been created (account
      signed up, key generated, card provisioned) but it hasn't yet
      been proven to work end-to-end.
    * ``verified`` — a drone has demonstrated the capability works
      (sent + received an email, pushed to GitHub, charged the card)
      and recorded the timestamp.
    """

    pending = "pending"
    registered = "registered"
    verified = "verified"


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


class Capability(BaseModel):
    """A single tracked capability on a persona.

    ``key`` is a stable identifier (``email``, ``github``, ``card``,
    ``phone``, ``stripe_express_account``, …). ``desired_value`` is
    what the persona wants to be (the email it would like to register,
    the handle it would like to claim) — at creation, this is a wish.
    The status moves desired → registered → verified as drones make it
    real. ``actual_value`` is set once the persona has actually
    acquired the thing (which may differ from the desired value if the
    handle was taken, etc.).
    """

    model_config = ConfigDict(extra="ignore")

    key: str
    """Stable identifier — ``email``, ``github``, ``card``, etc."""
    desired_value: str | None = None
    """Goal value (the email we want, the handle we want). Wishlist."""
    actual_value: str | None = None
    """The value we actually got, once acquired. May differ from
    desired (if the handle was taken, etc.)."""
    status: CapabilityStatus = CapabilityStatus.pending
    """Lifecycle position — pending → registered → verified."""
    credential_ref: str | None = None
    """Pointer to where the secret/credential lives, e.g.
    ``settings.secrets.alice_smtp_api_key``. Never the secret value
    itself — drones look it up by reference."""
    notes: str | None = None
    """Free-form context — provider chosen, why it was registered, etc."""
    updated_at: datetime = Field(default_factory=_now)
    verified_at: datetime | None = None


class Persona(BaseModel):
    """A swarm-managed identity. Persists in two places: the on-disk
    dir under ``persona_dir(name)`` and a ``:Persona`` node in Neo4j.

    ``swarm-zero`` is the baseline persona minted at bootstrap. Drones
    mint new ones via ``cm_create_persona`` when they need a fresh
    identity for an external account.

    The persona's *real* state is in ``capabilities`` — a list of
    explicit (key, desired_value, actual_value, status, …) entries
    that drones can interrogate before assuming any external service
    actually exists.
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    display_name: str
    backed_by_real_human: bool = False
    """True if a real human is the entity behind this persona. The
    operator's own identity is one such persona; a drone-minted alias
    is not. Drones that need to know whether a real human stands behind
    a counterparty interaction read this flag. Drones cannot set this
    to true themselves — only the operator (via UI / Settings)."""
    bio: str | None = None
    """Optional one-line backstory the drone can paste into 'about' fields."""
    ssh_fingerprint: str | None = None
    """SHA256 fingerprint of the persona's id_ed25519.pub. Generated
    locally at persona creation so personas have a key on file even
    before any external service has been bound to it."""
    capabilities: list[Capability] = Field(default_factory=list)
    """Extensible capability list. See ``Capability`` docstring."""
    browser_profiles: list[str] = Field(default_factory=list)
    """Names of ``cm_browser`` profiles bundled with this persona —
    these hold any cookies / saved logins for services this persona
    has authed into. Each profile typically corresponds to a
    capability binding (e.g. ``email`` capability registered after
    a Mailgun signup whose session lives in a browser profile)."""
    created_at: datetime = Field(default_factory=_now)
    created_by_drone_id: str | None = None
    notes: str | None = None
    """Operator can hand-edit free text in the manifest; the registry
    reads it back so personas can carry intent / scope across drones."""

    # ---- Capability helpers --------------------------------------------

    def capability(self, key: str) -> Capability | None:
        for c in self.capabilities:
            if c.key == key:
                return c
        return None

    def set_capability(self, cap: Capability) -> None:
        """Upsert a capability by key."""
        for i, existing in enumerate(self.capabilities):
            if existing.key == cap.key:
                self.capabilities[i] = cap
                return
        self.capabilities.append(cap)
