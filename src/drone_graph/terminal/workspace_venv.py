"""Resolve the workspace virtualenv activate script for bash ``terminal_run``.

Policy: ``DRONE_GRAPH_WORKSPACE`` points at a directory; the venv lives at
``{workspace}/.venv``. Prefer Unix ``bin/activate``, else Windows ``Scripts/activate``.
No venv creation or pruning — operator-managed on-disk layout only.
"""

from __future__ import annotations

import os
from pathlib import Path

WORKSPACE_ENV = "DRONE_GRAPH_WORKSPACE"


def resolve_venv_activate_script() -> tuple[Path | None, str | None]:
    """Return ``(activate_script_path, None)`` or ``(None, error_message)``.

    When ``WORKSPACE_ENV`` is unset or empty, returns an error — callers that
    require a venv should surface it to the operator.
    """
    raw = os.environ.get(WORKSPACE_ENV, "").strip()
    if not raw:
        return (
            None,
            f"{WORKSPACE_ENV} is not set; cannot activate workspace .venv "
            "for tools with needs_venv.",
        )
    workspace = Path(raw).resolve()
    venv_root = workspace / ".venv"
    unix_activate = venv_root / "bin" / "activate"
    win_activate = venv_root / "Scripts" / "activate"
    if unix_activate.is_file():
        return unix_activate, None
    if win_activate.is_file():
        return win_activate, None
    return (
        None,
        f"no venv activate script under {venv_root} "
        f"(expected {unix_activate} or {win_activate}). "
        "Create the workspace venv or unset "
        f"{WORKSPACE_ENV}.",
    )
