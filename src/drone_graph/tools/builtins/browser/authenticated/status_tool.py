"""``cm_check_auth_profile`` — check if an authenticated Chrome profile exists.

Security-critical: this tool returns **only** ``true`` or ``false``. No profile
names, paths, or filesystem listings are ever exposed to the AI.
"""

from __future__ import annotations

import json
from pathlib import Path

from drone_graph.api.settings import load_settings
from drone_graph.tools.registry import register_tool, ToolResult, DroneContext


@register_tool(
    "cm_check_auth_profile",
    (
        "Check if an authenticated Chrome profile is configured and ready. "
        "Returns true/false only — no profile names or paths are revealed. "
        "If false, use cm_browser to create the account via automation instead. "
        "Do NOT ask for profile names — the system stores this securely."
    ),
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def cm_check_auth_profile(
    args: dict, ctx: DroneContext  # noqa: ARG001 — unused params required by decorator
) -> ToolResult:
    """Return ``{"has_profile": true}`` or ``{"has_profile": false}``.

    Implementation: load ``settings.json`` → check if the stored
    ``authenticated_chrome_profile_path`` exists as a directory on disk.

    Security properties:
    - Returns only a boolean value.
    - No profile names, paths, or enumerations are revealed.
    - No filesystem listing of profile directories.
    - Even with prompt injection, the max information leak is one boolean.
    """
    settings = load_settings()
    if not settings.authenticated_chrome_profile_path:
        return ToolResult(content=json.dumps({"has_profile": False}))

    profile_dir = Path(settings.authenticated_chrome_profile_path)
    has_profile = profile_dir.is_dir()
    return ToolResult(content=json.dumps({"has_profile": has_profile}))
