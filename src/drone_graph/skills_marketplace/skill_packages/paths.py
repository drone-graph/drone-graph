"""Filesystem resolution for skill package directories (shared with preload + tools)."""

from __future__ import annotations

import os
from pathlib import Path

SKILL_ROOT_ENV = "DRONE_GRAPH_SKILL_ROOT"


def resolve_skill_package_path(path_str: str) -> Path:
    """Resolve a directory path the same way as ``skill_package:<path>`` preloads.

    Absolute paths are used as-is (caller may ``.resolve()``). Relative paths
    resolve under ``DRONE_GRAPH_SKILL_ROOT`` when set; otherwise under
    :func:`Path.cwd`.
    """
    p = Path(path_str.strip())
    if p.is_absolute():
        return p
    root = os.environ.get(SKILL_ROOT_ENV)
    if root:
        return Path(root) / p
    return Path.cwd() / p
