"""Drone-Graph persona registry.

A *persona* is a first-class swarm-managed identity. It bundles together
the artifacts a drone needs to *be someone* externally:

  * a ``name`` + ``email`` for git commits, mail, signup forms
  * an optional ``github_handle`` / ``linkedin_handle`` recording which
    external accounts have been tied to this persona
  * a generated SSH keypair (private key on disk under the persona's
    home, public-key fingerprint in the record)
  * the names of any ``cm_browser`` profiles bundled with this persona
    (login cookies for GitHub / LinkedIn / etc.)

The point: when a drone needs to act in the world, it picks a persona
(or mints a new one) instead of leaking the operator's identity. The
baseline ``swarm-zero`` persona is minted at substrate bootstrap and
serves as the default for routine work.

Storage:

  * On-disk dir at ``~/.config/drone-graph/personas/<name>/`` containing
    ``manifest.json``, ``ssh/`` (id_ed25519 + id_ed25519.pub), and
    optional ``home/`` with a ``.gitconfig`` keyed to the persona.
  * Neo4j ``:Persona`` node mirroring the manifest, so ``cm_list_*``
    queries can find personas the same way they find tools.
"""

from drone_graph.personas.records import (
    Persona,
    PersonaError,
    persona_dir,
    personas_root,
)
from drone_graph.personas.store import PersonaStore

__all__ = [
    "Persona",
    "PersonaError",
    "PersonaStore",
    "persona_dir",
    "personas_root",
]
