"""Persona builtins — discover, mint, bind, and track real-world capabilities.

The identity firewall (``orchestrator/isolation.py``) gives every isolated
drone a throwaway ``$HOME`` with a unique ``drone-<short>`` git identity.
That's fine for scratch work. When a drone needs *continuity* across
runs — a stable GitHub account, an email, a profile that survived from
last week — it picks or mints a persona.

Persona fields are GOALS, not claims. A persona is a list of explicit
capabilities (email, github, card, …) each with a desired value and a
lifecycle status (pending → registered → verified). Drones read the
status before assuming any external service is actually usable.

Tools:

* ``cm_list_personas`` — discover what the swarm has, with capability
  statuses so you can pick the persona whose verified set covers what
  you need.
* ``cm_create_persona`` — mint a new persona by name (slug). Seeds
  optional ``capabilities`` (each starts as ``pending`` unless you
  pass a different status).
* ``cm_use_persona`` — bind a persona to this drone's ``$HOME`` for
  the rest of the run. Returns the persona's full capability matrix
  so the drone knows what it can and cannot actually do.
* ``cm_set_persona_capability`` — register (or verify) a capability
  on a persona after acquiring (or successfully exercising) it in the
  real world. This is how personas grow real bodies — drones call it
  when a signup completes or when an action proves the capability
  works end-to-end.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from drone_graph.personas.records import (
    Capability,
    CapabilityStatus,
    Persona,
    PersonaError,
    persona_dir,
)
from drone_graph.personas.store import PersonaStore
from drone_graph.tools.registry import DroneContext, ToolResult, register_tool


def _persona_store(ctx: DroneContext) -> PersonaStore:
    return PersonaStore(ctx.store.substrate)


def _serialize(p: Persona) -> dict[str, Any]:
    return {
        "name": p.name,
        "display_name": p.display_name,
        "backed_by_real_human": p.backed_by_real_human,
        "bio": p.bio,
        "browser_profiles": list(p.browser_profiles),
        "ssh_fingerprint": p.ssh_fingerprint,
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
        "notes": p.notes,
    }


def _parse_capability_seed(entry: Any) -> Capability | None:
    """Parse a capability seed dict from tool args, or ``None`` if
    invalid."""
    if not isinstance(entry, dict):
        return None
    key = str(entry.get("key", "")).strip()
    if not key:
        return None
    try:
        status = CapabilityStatus(str(entry.get("status", "pending")).strip().lower())
    except ValueError:
        status = CapabilityStatus.pending
    return Capability(
        key=key,
        desired_value=(str(entry["desired_value"]).strip() if entry.get("desired_value") else None),
        actual_value=(str(entry["actual_value"]).strip() if entry.get("actual_value") else None),
        status=status,
        credential_ref=(str(entry["credential_ref"]).strip() if entry.get("credential_ref") else None),
        notes=(str(entry["notes"]).strip() if entry.get("notes") else None),
    )


@register_tool(
    "cm_list_personas",
    (
        "List swarm-managed identities (personas). Each persona has a "
        "name, an ssh keypair, and a list of capabilities — keys like "
        "``email`` / ``github`` / ``card`` with lifecycle statuses "
        "(pending / registered / verified). READ THE STATUSES before "
        "assuming a persona can do anything in the world: a persona "
        "with email=pending cannot send mail; a persona with "
        "github=registered but not verified hasn't proven the GitHub "
        "binding works. Pick the persona whose verified capabilities "
        "cover what your gap actually needs; if none do, see "
        "cm_create_persona and cm_set_persona_capability."
    ),
    {"type": "object", "properties": {}},
)
def cm_list_personas(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    store = _persona_store(ctx)
    personas = store.list()
    return ToolResult(content=json.dumps([_serialize(p) for p in personas]))


@register_tool(
    "cm_create_persona",
    (
        "Mint a new swarm-managed identity. Only when no existing "
        "persona fits — call cm_list_personas first. The persona starts "
        "with a freshly-generated ssh ed25519 keypair and zero verified "
        "capabilities. Capabilities are GOALS until a drone has actually "
        "acquired them in the world; seed any aspirational fields as "
        "``capabilities=[{key:'email', desired_value:'…', status:'pending'}]`` "
        "and use cm_set_persona_capability to move them to registered "
        "/ verified later."
    ),
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Slug, [A-Za-z0-9_-]{1,64}. Stable id; future drones "
                    "look the persona up by this name."
                ),
            },
            "display_name": {
                "type": "string",
                "description": "Human-readable name for git commits, signup forms.",
            },
            "bio": {
                "type": "string",
                "description": "One-line description; useful for 'about' fields on signups.",
            },
            "notes": {
                "type": "string",
                "description": (
                    "Free-form notes for future drones (what this persona is "
                    "for, which gap minted it, anything they should know)."
                ),
            },
            "capabilities": {
                "type": "array",
                "description": (
                    "Optional initial capability list. Each entry is a "
                    "goal — typically `status='pending'` unless you have "
                    "already acquired the entity (in which case use "
                    "cm_set_persona_capability instead so the timestamps "
                    "are correct)."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": (
                                "Stable identifier — email, github, "
                                "linkedin, card, phone, etc. Or a custom "
                                "key for a service-specific capability."
                            ),
                        },
                        "desired_value": {"type": "string"},
                        "actual_value": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "registered", "verified"],
                        },
                        "credential_ref": {
                            "type": "string",
                            "description": (
                                "Pointer to where the secret lives, e.g. "
                                "settings.secrets.alice_smtp_api_key. "
                                "Never the secret value itself."
                            ),
                        },
                        "notes": {"type": "string"},
                    },
                    "required": ["key"],
                },
            },
        },
        "required": ["name", "display_name"],
    },
)
def cm_create_persona(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    name = str(args.get("name", "")).strip()
    display_name = str(args.get("display_name", "")).strip()
    if not name or not display_name:
        return ToolResult(content="ERROR: name and display_name required")
    raw_caps = args.get("capabilities") or []
    seeded_caps: list[Capability] = []
    if isinstance(raw_caps, list):
        for entry in raw_caps:
            parsed = _parse_capability_seed(entry)
            if parsed is not None:
                seeded_caps.append(parsed)
    try:
        persona = Persona(
            name=name,
            display_name=display_name,
            bio=(str(args["bio"]).strip() if args.get("bio") else None),
            notes=(str(args["notes"]).strip() if args.get("notes") else None),
            capabilities=seeded_caps,
            created_by_drone_id=ctx.drone_id,
        )
    except PersonaError as e:
        return ToolResult(content=f"ERROR: {e}")
    store = _persona_store(ctx)
    try:
        registered = store.register(persona)
    except Exception as e:  # noqa: BLE001
        return ToolResult(content=f"ERROR: {type(e).__name__}: {e}")
    if ctx.tape is not None:
        ctx.tape.emit(
            "persona.created",
            drone_id=ctx.drone_id,
            gap_id=ctx.gap_id,
            name=registered.name,
            ssh_fingerprint=registered.ssh_fingerprint,
            capability_keys=[c.key for c in registered.capabilities],
        )
    return ToolResult(content=json.dumps(_serialize(registered)))


@register_tool(
    "cm_use_persona",
    (
        "Bind a persona to this drone for the rest of its run. Copies "
        "the persona's .gitconfig and ssh keypair into the drone's "
        "$HOME so subsequent git commits and ssh actions carry that "
        "identity. Returns the persona's full capability matrix so you "
        "know what this persona can actually do — capabilities with "
        "status=verified are battle-tested, registered means an "
        "external entity exists but hasn't been proven, pending means "
        "the persona only wishes it had this capability."
    ),
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
        },
        "required": ["name"],
    },
)
def cm_use_persona(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    name = str(args.get("name", "")).strip()
    if not name:
        return ToolResult(content="ERROR: name required")
    store = _persona_store(ctx)
    persona = store.get(name)
    if persona is None:
        return ToolResult(
            content=f"ERROR: no persona named {name!r}. Use cm_list_personas to see what's available."
        )
    home = Path(os.environ.get("HOME", ""))
    if not home or not home.exists():
        return ToolResult(
            content=(
                "ERROR: this drone has no isolated $HOME (probably running "
                "in operator-identity mode). Personas only bind in "
                "isolated mode where $HOME is a swarm-owned scratch dir."
            )
        )
    try:
        src_dir = persona_dir(name)
    except PersonaError as e:
        return ToolResult(content=f"ERROR: {e}")
    src_gc = src_dir / "home" / ".gitconfig"
    if src_gc.exists():
        (home / ".gitconfig").write_text(src_gc.read_text(encoding="utf-8"))
    src_key = src_dir / "ssh" / "id_ed25519"
    src_pub = src_dir / "ssh" / "id_ed25519.pub"
    if src_key.exists():
        dst_ssh = home / ".ssh"
        dst_ssh.mkdir(exist_ok=True)
        shutil.copy2(src_key, dst_ssh / "id_ed25519")
        try:
            (dst_ssh / "id_ed25519").chmod(0o600)
        except OSError:
            pass
        if src_pub.exists():
            shutil.copy2(src_pub, dst_ssh / "id_ed25519.pub")
    if ctx.tape is not None:
        ctx.tape.emit(
            "persona.bound",
            drone_id=ctx.drone_id,
            gap_id=ctx.gap_id,
            name=persona.name,
            ssh_fingerprint=persona.ssh_fingerprint,
        )
    return ToolResult(content=json.dumps(_serialize(persona)))


@register_tool(
    "cm_set_persona_capability",
    (
        "Register or verify a capability on a persona. Use this when "
        "the swarm has actually moved a capability forward in the "
        "world — a signup completed (registered), or an action proved "
        "the capability works (verified). Idempotent on (persona, key) "
        "— calling again updates the entry in place. Drones extend the "
        "schema freely: introduce new capability keys "
        "(stripe_express_account, ad_account, dns_zone, …) whenever "
        "the work calls for it. Never set status=verified without "
        "having actually exercised the capability end-to-end; "
        "alignment will flag fabricated verification."
    ),
    {
        "type": "object",
        "properties": {
            "persona_name": {"type": "string"},
            "key": {
                "type": "string",
                "description": (
                    "Capability key. Use existing keys (email, github, "
                    "card, phone, linkedin, ssh_key) when applicable; "
                    "introduce new keys for novel capabilities."
                ),
            },
            "desired_value": {
                "type": "string",
                "description": (
                    "The value the persona wanted (the email address it "
                    "tried to register, the github handle it tried to "
                    "claim). Carry through from the original creation."
                ),
            },
            "actual_value": {
                "type": "string",
                "description": (
                    "The value actually acquired. Often equals "
                    "desired_value; may differ if the handle was taken, "
                    "the provider assigned an id, etc."
                ),
            },
            "status": {
                "type": "string",
                "enum": ["pending", "registered", "verified"],
            },
            "credential_ref": {
                "type": "string",
                "description": (
                    "Where to find the credential — settings.secrets.X, "
                    "envvar:FOO, or an opaque ref. NEVER the secret value."
                ),
            },
            "notes": {
                "type": "string",
                "description": (
                    "What you did to register / verify. Concrete: "
                    "'signed up via mailgun.com sandbox; api key in "
                    "settings.secrets.alice_mailgun'."
                ),
            },
        },
        "required": ["persona_name", "key", "status"],
    },
)
def cm_set_persona_capability(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    pname = str(args.get("persona_name", "")).strip()
    key = str(args.get("key", "")).strip()
    if not pname or not key:
        return ToolResult(content="ERROR: persona_name and key are required")
    try:
        status = CapabilityStatus(str(args.get("status", "pending")).strip().lower())
    except ValueError:
        return ToolResult(
            content="ERROR: status must be pending | registered | verified"
        )
    cap = Capability(
        key=key,
        desired_value=(
            str(args["desired_value"]).strip() if args.get("desired_value") else None
        ),
        actual_value=(
            str(args["actual_value"]).strip() if args.get("actual_value") else None
        ),
        status=status,
        credential_ref=(
            str(args["credential_ref"]).strip() if args.get("credential_ref") else None
        ),
        notes=(str(args["notes"]).strip() if args.get("notes") else None),
        verified_at=datetime.now(UTC) if status is CapabilityStatus.verified else None,
    )
    store = _persona_store(ctx)
    try:
        updated = store.upsert_capability(pname, cap)
    except ValueError as e:
        return ToolResult(content=f"ERROR: {e}")
    except Exception as e:  # noqa: BLE001
        return ToolResult(content=f"ERROR: {type(e).__name__}: {e}")
    if ctx.tape is not None:
        ctx.tape.emit(
            "persona.capability_set",
            drone_id=ctx.drone_id,
            gap_id=ctx.gap_id,
            persona=pname,
            key=key,
            status=status.value,
        )
    return ToolResult(content=json.dumps(_serialize(updated)))
