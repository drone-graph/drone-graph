"""Persona builtins — list, create, and bind a swarm identity to a drone.

The identity firewall (``orchestrator/isolation.py``) gives every isolated
drone a throwaway ``$HOME`` with a unique ``drone-<short>`` git identity.
That's fine for scratch work. When a drone needs *continuity* across
runs — a stable GitHub account, an email, a profile that survived from
last week — it picks a persona instead.

``cm_use_persona`` swaps the drone's on-disk identity files in place:
copies the persona's ``.gitconfig`` and ``ssh/id_ed25519*`` into the
drone's existing ``$HOME``. The next ``git commit`` / ``ssh ...`` picks
up the new identity without restarting the drone.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from drone_graph.personas.records import (
    Persona,
    PersonaError,
    persona_dir,
)
from drone_graph.personas.store import PersonaStore
from drone_graph.tools.registry import DroneContext, ToolResult, register_tool


def _persona_store(ctx: DroneContext) -> PersonaStore:
    # tool_store / store share the same substrate handle.
    return PersonaStore(ctx.store.substrate)


def _serialize(p: Persona) -> dict[str, Any]:
    return {
        "name": p.name,
        "display_name": p.display_name,
        "email": p.email,
        "github_handle": p.github_handle,
        "linkedin_handle": p.linkedin_handle,
        "bio": p.bio,
        "browser_profiles": list(p.browser_profiles),
        "ssh_fingerprint": p.ssh_fingerprint,
        "created_at": p.created_at.isoformat(),
        "notes": p.notes,
    }


@register_tool(
    "cm_list_personas",
    (
        "List swarm-managed identities (personas). Each persona bundles a "
        "display name + email + optional handles + an ssh keypair, "
        "persisted across drones. Use cm_use_persona to bind one to this "
        "drone so its git commits and ssh actions carry that identity. "
        "When you need a brand-new identity for a service that has no "
        "existing persona, call cm_create_persona."
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
        "Mint a new swarm-managed identity. Use ONLY when no existing "
        "persona fits — check cm_list_personas first. Generates an "
        "ssh ed25519 keypair on disk under the persona's home and "
        "registers a :Persona node so future drones can discover it. "
        "Idempotent on ``name`` — calling again with the same name "
        "updates the existing record without regenerating the key. "
        "Do NOT fabricate links to real people; pick a clearly synthetic "
        "name unless this persona is going to back a service signup the "
        "drone is about to do via cm_browser."
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
                "description": "Human-readable name used in git commits, signup forms.",
            },
            "email": {
                "type": "string",
                "description": (
                    "Email address for this persona. Use a real address only "
                    "if you've actually created one (e.g. via cm_browser at "
                    "a mail provider). Otherwise pick a clearly synthetic "
                    "@swarm.local placeholder."
                ),
            },
            "github_handle": {"type": "string"},
            "linkedin_handle": {"type": "string"},
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
        },
        "required": ["name", "display_name", "email"],
    },
)
def cm_create_persona(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    name = str(args.get("name", "")).strip()
    display_name = str(args.get("display_name", "")).strip()
    email = str(args.get("email", "")).strip()
    if not name or not display_name or not email:
        return ToolResult(content="ERROR: name, display_name, email all required")
    try:
        persona = Persona(
            name=name,
            display_name=display_name,
            email=email,
            github_handle=(str(args["github_handle"]).strip() if args.get("github_handle") else None),
            linkedin_handle=(str(args["linkedin_handle"]).strip() if args.get("linkedin_handle") else None),
            bio=(str(args["bio"]).strip() if args.get("bio") else None),
            notes=(str(args["notes"]).strip() if args.get("notes") else None),
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
        )
    return ToolResult(content=json.dumps(_serialize(registered)))


@register_tool(
    "cm_use_persona",
    (
        "Bind a persona to this drone for the rest of its run. Copies the "
        "persona's .gitconfig and ssh keypair into the drone's $HOME so "
        "subsequent git commits and ssh actions carry that identity. The "
        "binding lasts until the drone exits; future drones must call "
        "cm_use_persona themselves. Returns the persona's public-key "
        "fingerprint so you can register it with a remote (GitHub etc.) "
        "if needed."
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
    # Copy .gitconfig
    src_gc = src_dir / "home" / ".gitconfig"
    if src_gc.exists():
        (home / ".gitconfig").write_text(src_gc.read_text(encoding="utf-8"))
    # Copy ssh keypair
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
    return ToolResult(
        content=json.dumps(
            {
                "bound": persona.name,
                "display_name": persona.display_name,
                "email": persona.email,
                "ssh_fingerprint": persona.ssh_fingerprint,
                "github_handle": persona.github_handle,
            }
        )
    )
