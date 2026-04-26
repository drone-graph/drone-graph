"""Tool records — the metadata side of the tool registry.

Tools live in the graph as ``:Tool`` nodes so they're discoverable from any
drone via ``cm_list_tools`` / ``cm_get_tool``. Implementation lives in code
(builtins) or as documentation a drone executes via ``terminal_run`` (installed
tools). The Tool node carries the schema, the description, and provenance —
not the implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolKind(StrEnum):
    builtin = "builtin"
    """Implementation lives in Python code, dispatched via the registry."""
    installed = "installed"
    """Installed by a drone at runtime; future drones invoke it through the
    terminal using the ``usage`` string (and ``install_commands`` if not yet
    installed in their shell)."""


def _now() -> datetime:
    return datetime.now(UTC)


class Tool(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    """Globally unique identifier. Matches the tool name a drone uses in a
    tool call. Cannot be changed after registration."""
    description: str
    """What the tool does, in one or two sentences. Drones read this when
    selecting tools; keep it terse and accurate."""
    input_schema_json: str
    """JSON schema for the tool's inputs, serialized as a string. Stored as
    a string so it can sit on a Neo4j node without graph-side parsing."""
    kind: ToolKind = ToolKind.builtin
    usage: str = ""
    """For installed tools: a runnable example (e.g.
    ``python -m playwright screenshot URL OUT.png``). Empty for builtins."""
    install_commands: list[str] = Field(default_factory=list)
    """For installed tools: the bash commands a drone ran to make the tool
    available, recorded for posterity and reproducibility."""
    depends_on: list[str] = Field(default_factory=list)
    """Names of other tools this tool needs available."""
    created_at: datetime = Field(default_factory=_now)
    installed_by_drone_id: str | None = None
    """Drone id that registered this tool, if any. ``None`` for builtins."""
    flagged_by_alignment: bool = False
    """Set by alignment when the registration looks suspicious. Drones can
    still see the tool but should treat it with extra scrutiny."""


def empty_input_schema() -> dict[str, Any]:
    """Convenience: a tool with no inputs."""
    return {"type": "object", "properties": {}, "required": []}
