"""Browser profile management — capability-shaped, persisted on disk.

A "profile" is a Chromium user-data directory: cookies, local storage,
saved passwords, the works. Keyed by an operator/drone-chosen name, not
by drone id. That way a profile holding a LinkedIn session created by
drone A on Monday is still usable by drone B on Tuesday — the capability
lives in the registry, not in the ephemeral drone.

The mapping ``profile_name`` → ``profile_dir`` is the contract. The
profile is registered as a ``Tool`` (kind=installed) in the graph so
``cm_list_tools`` shows the capability and operators can revoke it via
the marketplace.

Service tags
------------
Each profile directory can hold an optional ``metadata.json`` file that
declares which **online services** (Google, Reddit, X/Twitter, GitHub,
LinkedIn, etc.) the profile has active sessions for.  Drones use this
info to pick the right profile for a task, and the frontend Accounts UI
lets operators add/remove tags interactively.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

_PROFILE_NAME_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_METADATA_FILE = "metadata.json"


def profiles_root() -> Path:
    """``~/.config/drone-graph/browser-profiles/`` — created if missing."""
    p = Path.home() / ".config" / "drone-graph" / "browser-profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def profile_dir(profile_name: str) -> Path:
    """Return the disk path for ``profile_name``, creating it as needed.

    Profile names are simple slugs — no spaces, no slashes — to keep the
    on-disk layout predictable. Drones that pass invalid names get a
    ``ValueError``.
    """
    if not _PROFILE_NAME_OK.match(profile_name):
        raise ValueError(
            f"invalid profile name {profile_name!r} — use 1-64 chars of "
            "[A-Za-z0-9_-] only"
        )
    p = profiles_root() / profile_name
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_profiles() -> list[str]:
    """Names of all profiles currently on disk."""
    root = profiles_root()
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and _PROFILE_NAME_OK.match(d.name)
    )


def delete_profile(profile_name: str) -> bool:
    """Remove a profile's on-disk directory. Returns True if it existed.

    Operator-driven revocation goes through here. Drones do NOT call this
    — they'd lose their own session mid-turn. The marketplace UI maps to
    this via an endpoint we'll add separately.
    """
    if not _PROFILE_NAME_OK.match(profile_name):
        return False
    p = profiles_root() / profile_name
    if not p.exists():
        return False
    shutil.rmtree(p)
    return True


# ---------------------------------------------------------------------------
# Service-tag metadata  (the "Accounts" feature)
# ---------------------------------------------------------------------------


def _metadata_path(profile_name: str) -> Path:
    """Full path to the ``metadata.json`` for *profile_name*."""
    if not _PROFILE_NAME_OK.match(profile_name):
        raise ValueError(f"invalid profile name {profile_name!r}")
    return profile_dir(profile_name) / _METADATA_FILE


def load_profile_metadata(profile_name: str) -> dict[str, Any]:
    """Read the metadata.json for *profile_name*, returning an empty dict if
    the file doesn't exist yet."""
    p = _metadata_path(profile_name)
    if not p.exists():
        return {}
    try:
        raw = p.read_text(encoding="utf-8")
        return dict(json.loads(raw))
    except (json.JSONDecodeError, OSError):
        return {}


def save_profile_metadata(profile_name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Atomically write *data* to the profile's metadata.json."""
    p = _metadata_path(profile_name)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return data


def get_profile_services(profile_name: str) -> list[str]:
    """Return the list of service tags for *profile_name*."""
    meta = load_profile_metadata(profile_name)
    raw = meta.get("services", [])
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if s]
    return []


def set_profile_services(profile_name: str, services: list[str]) -> list[str]:
    """Replace the service tags for *profile_name* and persist to disk."""
    clean = sorted({s.strip().lower() for s in services if s.strip()})
    meta = load_profile_metadata(profile_name)
    meta["services"] = clean
    save_profile_metadata(profile_name, meta)
    return clean


def registered_tool_name(profile_name: str) -> str:
    """Stable tool-registry name for a registered browser session.

    Drones use this when calling ``cm_register_tool`` so later drones can
    discover the capability with a predictable query.
    """
    return f"browser_session_{profile_name}"
