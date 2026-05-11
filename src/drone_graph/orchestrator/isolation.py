"""Drone identity firewall: per-drone $HOME, env allowlist, scratch cwd.

Every emergent drone subprocess runs by default in a sandboxed identity:
a throwaway ``$HOME`` under ``var/runs/<run_id>/<drone_id>/home/`` with a
synthetic ``~/.gitconfig`` (``drone-<short>@swarm.local``), no SSH keys,
no operator ``gh`` auth, and an env stripped to a small allowlist.

Two escape hatches:

  * ``uses_operator_identity=True`` + ``identity_approved=True`` on the
    gap (operator-blessed per-gap) → real ``$HOME``, real ``$PWD``, full
    env passthrough including the operator's ``gh``, ``aws``, ``ssh``,
    etc. The scheduler is the gatekeeper.
  * Preset drones (Gap Finding / Alignment) keep the full env — they need
    LLM keys to function and don't touch external services as part of
    their normal work. They still run in their own scratch cwd to keep
    project-root file leaks out of the substrate they index.

The provider key flows in via ``DRONE_GRAPH_PROVIDER_KEY`` (a copy of
``ANTHROPIC_API_KEY``/``OPENAI_API_KEY``) so the runtime can read it
without exposing the original var name to ``terminal_run`` children.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---- Allowlist + scrubbing ------------------------------------------------


# Variables that survive into an isolated drone. Everything else is dropped.
_ENV_ALLOWLIST = frozenset({
    "PATH",
    "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES",
    "TZ", "TERM",
    "PYTHONUNBUFFERED",
    "TMPDIR",
    # Substrate connection vars — every drone reads/writes the graph and
    # signals db; without these the drone can't function at all. Defaults
    # exist in runner.py but a non-default setup (custom Neo4j host /
    # password) would silently lose them.
    "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
})

# DRONE_GRAPH_* are always passed through (substrate, signals, tape, …).
_DRONE_GRAPH_PREFIX = "DRONE_GRAPH_"


def _build_isolated_env(
    base_env: dict[str, str],
    *,
    home: Path,
    cwd: Path,
    drone_id: str,
    provider_key_value: str | None,
) -> dict[str, str]:
    """Construct the env dict for an isolated drone subprocess.

    Starts from a blank dict; copies only allowlisted vars from
    ``base_env``. Sets ``HOME`` and ``PWD`` to the drone-local sandbox.
    Smuggles the provider API key under ``DRONE_GRAPH_PROVIDER_KEY`` so
    the runtime can dispatch model calls without exposing the canonical
    ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` to a downstream
    ``terminal_run``.
    """
    env: dict[str, str] = {}
    for k, v in base_env.items():
        if k in _ENV_ALLOWLIST or k.startswith(_DRONE_GRAPH_PREFIX):
            env[k] = v
    env["HOME"] = str(home)
    env["PWD"] = str(cwd)
    env["USER"] = f"drone-{drone_id[:8]}"
    env["LOGNAME"] = env["USER"]
    env.setdefault("SHELL", "/bin/bash")
    # Redirect XDG so apps that respect the spec don't fall through to
    # the operator's real config / cache / data dirs.
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["XDG_STATE_HOME"] = str(home / ".local" / "state")
    # Provider key side-channel. The runtime reads this, the terminal
    # spawn path scrubs it before exec.
    if provider_key_value:
        env["DRONE_GRAPH_PROVIDER_KEY"] = provider_key_value
    return env


def _build_passthrough_env(
    base_env: dict[str, str],
    *,
    drone_id: str,
) -> dict[str, str]:
    """Full env passthrough for an operator-identity-approved drone.

    Same env the server itself runs under. We still set
    ``DRONE_GRAPH_PROVIDER_KEY`` as a copy of whichever provider key is
    present, so the runtime has one place to look regardless of mode.
    """
    env = dict(base_env)
    env.setdefault("PYTHONUNBUFFERED", "1")
    provider_key = (
        base_env.get("ANTHROPIC_API_KEY")
        or base_env.get("OPENAI_API_KEY")
        or ""
    )
    if provider_key:
        env["DRONE_GRAPH_PROVIDER_KEY"] = provider_key
    env["DRONE_GRAPH_IDENTITY_MODE"] = "operator"
    env["DRONE_GRAPH_DRONE_ID"] = drone_id
    return env


# ---- Sandbox HOME bootstrap ----------------------------------------------


def _seed_drone_home(home: Path, *, drone_id: str) -> None:
    """Lay out a throwaway $HOME for an isolated drone.

    Creates ``$HOME``, ``$HOME/.config``, ``$HOME/.cache``, an empty
    ``~/.ssh`` (with a stub ``config`` denying agent fallback to the
    operator's keys), and a synthetic ``~/.gitconfig`` so the drone's
    git commits don't carry the operator's name/email.
    """
    home.mkdir(parents=True, exist_ok=True)
    (home / ".config").mkdir(exist_ok=True)
    (home / ".cache").mkdir(exist_ok=True)
    (home / ".local" / "share").mkdir(parents=True, exist_ok=True)
    (home / ".local" / "state").mkdir(parents=True, exist_ok=True)
    ssh = home / ".ssh"
    ssh.mkdir(exist_ok=True)
    # Deny SSH from picking up the operator agent / keyring.
    ssh_config = ssh / "config"
    if not ssh_config.exists():
        ssh_config.write_text(
            "# Drone-Graph isolated drone. SSH agent and keyring access disabled.\n"
            "Host *\n"
            "    IdentityAgent none\n"
            "    IdentitiesOnly yes\n"
            "    UseKeychain no\n"
        )
    gitconfig = home / ".gitconfig"
    if not gitconfig.exists():
        name = f"drone-{drone_id[:8]}"
        gitconfig.write_text(
            "[user]\n"
            f"    name = {name}\n"
            f"    email = {name}@swarm.local\n"
            "[init]\n"
            "    defaultBranch = main\n"
            "[advice]\n"
            "    detachedHead = false\n"
        )


def _scratch_cwd(scratch_root: Path) -> Path:
    """Per-drone scratch working dir; created on demand."""
    scratch_root.mkdir(parents=True, exist_ok=True)
    return scratch_root


# ---- Public surface ------------------------------------------------------


@dataclass
class DroneEnvPlan:
    """The result of identity routing. Hand this to subprocess.Popen."""

    env: dict[str, str]
    cwd: Path
    identity_mode: str  # "isolated" | "operator" | "preset"
    home: Path | None    # None for operator-mode drones


def plan_drone_environment(
    *,
    drone_id: str,
    role: str,
    gap: Any,                          # Gap (untyped to dodge import cycles)
    operator_identity_approved: bool,
    allow_operator_identity: bool,
    base_env: dict[str, str] | None = None,
    run_dir: Path,
    operator_cwd: Path | None = None,
) -> DroneEnvPlan:
    """Decide how the next drone subprocess should run, and prepare its
    sandbox.

    Routing rules:
      * Preset drones (Gap Finding, Alignment): full env, but cwd is the
        run scratch dir so they don't index project files.
      * Worker drone, ``gap.uses_operator_identity == True`` AND master
        ``allow_operator_identity == True`` AND ``identity_approved ==
        True``: operator passthrough (real env + real cwd).
      * Everything else: isolated sandbox (per-drone $HOME, scrubbed env,
        scratch cwd).
    """
    base_env = dict(base_env if base_env is not None else os.environ)
    run_dir = run_dir.resolve()
    drone_dir = run_dir / "drones" / drone_id
    drone_dir.mkdir(parents=True, exist_ok=True)

    if role.startswith("preset:"):
        env = _build_passthrough_env(base_env, drone_id=drone_id)
        cwd = _scratch_cwd(drone_dir / "workspace")
        env["PWD"] = str(cwd)
        return DroneEnvPlan(env=env, cwd=cwd, identity_mode="preset", home=None)

    wants_operator = bool(getattr(gap, "uses_operator_identity", False))
    approved = bool(getattr(gap, "identity_approved", False))
    grant = wants_operator and approved and allow_operator_identity
    if grant:
        env = _build_passthrough_env(base_env, drone_id=drone_id)
        cwd = (operator_cwd or Path.cwd()).resolve()
        env["PWD"] = str(cwd)
        return DroneEnvPlan(env=env, cwd=cwd, identity_mode="operator", home=None)

    # Default path: isolated sandbox.
    home = drone_dir / "home"
    _seed_drone_home(home, drone_id=drone_id)
    cwd = _scratch_cwd(drone_dir / "workspace")
    provider_key = (
        base_env.get("ANTHROPIC_API_KEY")
        or base_env.get("OPENAI_API_KEY")
        or ""
    )
    env = _build_isolated_env(
        base_env, home=home, cwd=cwd, drone_id=drone_id,
        provider_key_value=provider_key or None,
    )
    env["DRONE_GRAPH_IDENTITY_MODE"] = "isolated"
    env["DRONE_GRAPH_DRONE_ID"] = drone_id
    return DroneEnvPlan(env=env, cwd=cwd, identity_mode="isolated", home=home)
