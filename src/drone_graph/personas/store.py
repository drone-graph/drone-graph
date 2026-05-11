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
        return _persona_from_node(rows[0]["p"])

    def list(self) -> list[Persona]:
        rows = self.substrate.execute_read(
            "MATCH (p:Persona) RETURN p ORDER BY p.created_at ASC",
        )
        return [_persona_from_node(r["p"]) for r in rows]

    # ---- Writes ----------------------------------------------------------

    def register(self, persona: Persona) -> Persona:
        """Create or upsert a persona. Generates the on-disk SSH keypair
        + ``.gitconfig`` if they don't exist yet, then writes the graph
        node. Idempotent — calling twice with the same name updates the
        existing record without regenerating the SSH key.

        Capabilities are stored as a JSON blob since the schema is
        open-ended (drones can add new capability keys at runtime)."""
        dir_ = persona_dir(persona.name)
        keypath = dir_ / _SSH_DIR / _KEY_NAME
        keypath.parent.mkdir(parents=True, exist_ok=True)
        # Use the persona's name as the ssh comment — we don't have a
        # real email yet (email is a capability, not a guaranteed field).
        ssh_comment = f"drone-graph:{persona.name}"
        if not keypath.exists():
            persona.ssh_fingerprint = _generate_ed25519(keypath, ssh_comment)
        elif persona.ssh_fingerprint is None:
            persona.ssh_fingerprint = _fingerprint(keypath.with_suffix(".pub"))
        # Per-persona ~/.gitconfig that any drone using this persona drops
        # into its $HOME. Email is whatever the persona's email capability
        # has actually acquired (actual_value), else a placeholder.
        email_cap = persona.capability("email")
        commit_email = (
            (email_cap.actual_value if email_cap and email_cap.actual_value
             else email_cap.desired_value if email_cap
             else f"{persona.name}@swarm.local")
            if email_cap is not None
            else f"{persona.name}@swarm.local"
        )
        gitconfig = dir_ / "home" / ".gitconfig"
        gitconfig.parent.mkdir(parents=True, exist_ok=True)
        # Re-write every time so a status change (email capability moving
        # from pending to verified) lands in the gitconfig promptly.
        gitconfig.write_text(textwrap.dedent(f"""\
            [user]
                name = {persona.display_name}
                email = {commit_email}
            [init]
                defaultBranch = main
        """))
        # Manifest.
        manifest = dir_ / _MANIFEST
        manifest.write_text(persona.model_dump_json(indent=2), encoding="utf-8")
        # Mirror to graph. Capabilities are JSON-encoded — easier than
        # modelling them as their own node type for now.
        self.substrate.execute_write(
            """
            MERGE (p:Persona {name: $name})
            SET p.display_name = $display_name,
                p.backed_by_real_human = $backed_by_real_human,
                p.bio = $bio,
                p.browser_profiles = $browser_profiles,
                p.ssh_fingerprint = $ssh_fingerprint,
                p.capabilities_json = $capabilities_json,
                p.created_at = COALESCE(p.created_at, datetime($created_at)),
                p.created_by_drone_id = COALESCE(p.created_by_drone_id, $created_by_drone_id),
                p.notes = $notes
            """,
            name=persona.name,
            display_name=persona.display_name,
            backed_by_real_human=bool(persona.backed_by_real_human),
            bio=persona.bio,
            browser_profiles=list(persona.browser_profiles),
            ssh_fingerprint=persona.ssh_fingerprint,
            capabilities_json=json.dumps(
                [c.model_dump(mode="json") for c in persona.capabilities]
            ),
            created_at=persona.created_at.isoformat(),
            created_by_drone_id=persona.created_by_drone_id,
            notes=persona.notes,
        )
        return persona

    # ---- Capability mutators -------------------------------------------

    def upsert_capability(self, persona_name: str, capability: Any) -> Persona:
        """Upsert a single capability on a persona, persisting both
        sides (graph + on-disk manifest). Drones call this when they
        register, verify, or refine a capability."""
        persona = self.get(persona_name)
        if persona is None:
            raise ValueError(f"no persona named {persona_name!r}")
        persona.set_capability(capability)
        return self.register(persona)

    # ---- Helpers ---------------------------------------------------------

    def bootstrap_baseline(self) -> Persona:
        """Mint the ``swarm-zero`` persona if absent; return it. Called
        at substrate init. ``swarm-zero`` ships with one capability —
        a baseline ssh key (registered, since we generate the keypair
        immediately) — and zero other capabilities. Drones add (email,
        github, card, etc.) as the swarm actually acquires them in the
        world."""
        from drone_graph.personas.records import Capability, CapabilityStatus

        existing = self.get("swarm-zero")
        if existing is not None:
            return existing
        return self.register(
            Persona(
                name="swarm-zero",
                display_name="Drone Graph Swarm",
                bio=(
                    "Baseline identity for routine work — git commits, "
                    "throwaway scratch accounts. Mint additional personas "
                    "via cm_create_persona when you need a specific external "
                    "account. This persona ships with zero verified external "
                    "capabilities; treat any 'send email' / 'push to github' "
                    "instinct as needing capability acquisition first."
                ),
                capabilities=[
                    Capability(
                        key="ssh_key",
                        desired_value="ed25519 keypair",
                        status=CapabilityStatus.registered,
                        notes=(
                            "Generated locally at bootstrap. Bound to no "
                            "external service yet — registering with GitHub "
                            "/ a server is itself a capability to acquire."
                        ),
                    ),
                ],
            )
        )


# ---- Module helpers ------------------------------------------------------


def _persona_from_node(node: Any) -> Persona:
    """Deserialize a Neo4j ``:Persona`` node, including the JSON-encoded
    capabilities blob and any legacy email/github/linkedin fields from
    pre-reframe records (folded into capabilities at read time)."""
    raw = _to_dict(node)
    caps_json = raw.pop("capabilities_json", None)
    caps: list[dict[str, Any]] = []
    if isinstance(caps_json, str) and caps_json:
        try:
            caps = json.loads(caps_json)
            if not isinstance(caps, list):
                caps = []
        except json.JSONDecodeError:
            caps = []
    # Legacy fold-in: nodes minted before the reframe carried email /
    # github_handle / linkedin_handle as direct columns. Treat those as
    # ``pending`` capabilities — they were aspirational then too.
    for legacy_key, cap_key in (
        ("email", "email"),
        ("github_handle", "github"),
        ("linkedin_handle", "linkedin"),
    ):
        legacy_val = raw.pop(legacy_key, None)
        if legacy_val and not any(c.get("key") == cap_key for c in caps):
            caps.append(
                {
                    "key": cap_key,
                    "desired_value": legacy_val,
                    "status": "pending",
                    "notes": "imported from legacy persona record",
                }
            )
    raw["capabilities"] = caps
    return Persona.model_validate(raw)


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
