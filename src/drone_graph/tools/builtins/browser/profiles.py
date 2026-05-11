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
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

_PROFILE_NAME_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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


def registered_tool_name(profile_name: str) -> str:
    """Stable tool-registry name for a registered browser session.

    Drones use this when calling ``cm_register_tool`` so later drones can
    discover the capability with a predictable query.
    """
    return f"browser_session_{profile_name}"
