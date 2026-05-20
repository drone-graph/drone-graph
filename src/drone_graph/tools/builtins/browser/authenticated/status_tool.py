"""``cm_check_browser`` — check if a Chrome profile is configured.

Security-critical: this tool returns **only** ``true`` or ``false``. No profile
names, paths, or filesystem listings are ever exposed to the AI.
"""

from __future__ import annotations

import json
from pathlib import Path

from drone_graph.tools.registry import register_tool, ToolResult, DroneContext


@register_tool(
    "cm_check_browser",
    (
        "Check if a Chrome profile is configured and ready. "
        "Returns true/false only — no profile names or paths are revealed. "
        "If false, the operator must configure a Chrome profile directory "
        "in Settings before browser actions can proceed. "
        "Do NOT ask for profile names — the system stores this securely."
    ),
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def cm_check_browser(
    args: dict, ctx: DroneContext  # noqa: ARG001 — unused params required by decorator
) -> ToolResult:
    """Return ``{"has_profile": true}`` or ``{"has_profile": false}``.

    Implementation: load ``settings.json`` → check if the stored
    ``chrome_profile_dir`` exists as a directory on disk.

    Security properties:
    - Returns only a boolean value.
    - No profile names, paths, or enumerations are revealed.
    - No filesystem listing of profile directories.
    - Even with prompt injection, the max information leak is one boolean.
    """
    from drone_graph.api.settings import load_settings  # lazy: break circular import

    settings = load_settings()
    if not settings.chrome_profile_dir:
        return ToolResult(content=json.dumps({"has_profile": False}))

    profile_dir = Path(settings.chrome_profile_dir)
    has_profile = profile_dir.is_dir()
    return ToolResult(content=json.dumps({"has_profile": has_profile}))
