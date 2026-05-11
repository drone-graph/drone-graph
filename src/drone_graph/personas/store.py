"""Persona registry: Neo4j + on-disk dual storage.

The graph node is the source of truth for *discoverability* (every drone
calls ``cm_list_personas`` against the substrate). The on-disk dir is the
source of truth for *secrets* (SSH keys + per-persona ``.gitconfig``)
because those don't belong in a graph DB.

Both writes happen in one method (``register``) so they can't drift.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path
from typing import Any

from drone_graph.personas.records import Persona, PersonaError, persona_dir
from drone_graph.substrate import Substrate

_MANIFEST = "manifest.json"
_SSH_DIR = "ssh"
_KEY_NAME = "id_ed25519"


class PersonaStore:
    """CRUD for personas. Open one per server process."""

    def __init__(self, substrate: Substrate) -> None:
        self.substrate = substrate

    # ---- Reads -----------------------------------------------------------

    def get(self, name: str) -> Persona | None:
        rows = self.substrate.execute_read(
            "MATCH (p:Persona {name: $name}) RETURN p",
            name=name,
        )
        if not rows:
            return None
        return Persona.model_validate(_to_dict(rows[0]["p"]))

    def list(self) -> list[Persona]:
        rows = self.substrate.execute_read(
            "MATCH (p:Persona) RETURN p ORDER BY p.created_at ASC",
        )
        return [Persona.model_validate(_to_dict(r["p"])) for r in rows]

    # ---- Writes ----------------------------------------------------------

    def register(self, persona: Persona) -> Persona:
        """Create or upsert a persona. Generates the on-disk SSH keypair
        + ``.gitconfig`` if they don't exist yet, then writes the graph
        node. Idempotent — calling twice with the same name updates the
        existing record without regenerating the SSH key."""
        dir_ = persona_dir(persona.name)
        keypath = dir_ / _SSH_DIR / _KEY_NAME
        keypath.parent.mkdir(parents=True, exist_ok=True)
        if not keypath.exists():
            persona.ssh_fingerprint = _generate_ed25519(keypath, persona.email)
        elif persona.ssh_fingerprint is None:
            persona.ssh_fingerprint = _fingerprint(keypath.with_suffix(".pub"))
        # Per-persona ~/.gitconfig that any drone using this persona drops
        # into its $HOME.
        gitconfig = dir_ / "home" / ".gitconfig"
        gitconfig.parent.mkdir(parents=True, exist_ok=True)
        if not gitconfig.exists():
            gitconfig.write_text(textwrap.dedent(f"""\
                [user]
                    name = {persona.display_name}
                    email = {persona.email}
                [init]
                    defaultBranch = main
            """))
        # Manifest.
        manifest = dir_ / _MANIFEST
        manifest.write_text(persona.model_dump_json(indent=2), encoding="utf-8")
        # Mirror to graph.
        self.substrate.execute_write(
            """
            MERGE (p:Persona {name: $name})
            SET p.display_name = $display_name,
                p.email = $email,
                p.github_handle = $github_handle,
                p.linkedin_handle = $linkedin_handle,
                p.bio = $bio,
                p.browser_profiles = $browser_profiles,
                p.ssh_fingerprint = $ssh_fingerprint,
                p.created_at = COALESCE(p.created_at, datetime($created_at)),
                p.created_by_drone_id = COALESCE(p.created_by_drone_id, $created_by_drone_id),
                p.notes = $notes
            """,
            name=persona.name,
            display_name=persona.display_name,
            email=persona.email,
            github_handle=persona.github_handle,
            linkedin_handle=persona.linkedin_handle,
            bio=persona.bio,
            browser_profiles=list(persona.browser_profiles),
            ssh_fingerprint=persona.ssh_fingerprint,
            created_at=persona.created_at.isoformat(),
            created_by_drone_id=persona.created_by_drone_id,
            notes=persona.notes,
        )
        return persona

    # ---- Helpers ---------------------------------------------------------

    def bootstrap_baseline(self) -> Persona:
        """Mint the ``swarm-zero`` persona if absent; return it. Called
        at substrate init."""
        existing = self.get("swarm-zero")
        if existing is not None:
            return existing
        return self.register(
            Persona(
                name="swarm-zero",
                display_name="Drone Graph Swarm",
                email="swarm-zero@swarm.local",
                bio=(
                    "Baseline identity for routine work — git commits, "
                    "throwaway scratch accounts. Drones mint additional "
                    "personas via cm_create_persona when they need a "
                    "specific external account."
                ),
            )
        )


# ---- Module helpers ------------------------------------------------------


def _to_dict(node: Any) -> dict[str, Any]:
    return {k: _native(v) for k, v in dict(node).items()}


def _native(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "to_native"):
        return v.to_native()
    return v


def _generate_ed25519(keypath: Path, comment: str) -> str | None:
    """``ssh-keygen -t ed25519 -f keypath -N "" -C comment``. Returns
    the SHA256 fingerprint, or ``None`` if ssh-keygen is unavailable."""
    try:
        subprocess.run(
            [
                "ssh-keygen",
                "-t", "ed25519",
                "-f", str(keypath),
                "-N", "",
                "-C", comment,
                "-q",
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return _fingerprint(keypath.with_suffix(".pub"))


def _fingerprint(pubpath: Path) -> str | None:
    if not pubpath.exists():
        return None
    try:
        r = subprocess.run(
            ["ssh-keygen", "-lf", str(pubpath)],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        # Output: "256 SHA256:abc... comment (ED25519)"
        parts = r.stdout.split()
        if len(parts) >= 2:
            return parts[1]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None
