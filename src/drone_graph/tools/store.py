"""Graph-side CRUD for Tool nodes.

Tools live as ``:Tool`` nodes alongside Gaps and Findings. The store wraps the
Cypher writes/reads so drones (via ``cm_*`` tools) can list, fetch, register,
and flag tools through a typed Python API.

Edges (added as the graph fills out):
    (:Tool)-[:USED_BY]->(:Gap)         # gap that referenced/invoked the tool
    (:Tool)-[:DEPENDS_ON]->(:Tool)     # required to be installed first
"""

from __future__ import annotations

import json
from typing import Any

from drone_graph.substrate import Substrate
from drone_graph.tools.records import Tool, ToolKind


def _tool_from_node(node: Any) -> Tool:
    data: dict[str, Any] = dict(node)
    # Coerce Neo4j datetime to native python datetime.
    if hasattr(data.get("created_at"), "to_native"):
        data["created_at"] = data["created_at"].to_native()
    return Tool.model_validate(data)


class ToolStore:
    """Neo4j-backed CRUD for ``:Tool`` nodes."""

    def __init__(self, substrate: Substrate) -> None:
        self.substrate = substrate

    # ---- Reads ----

    def get(self, name: str) -> Tool | None:
        rows = self.substrate.execute_read(
            "MATCH (t:Tool {name: $name}) RETURN t",
            name=name,
        )
        return _tool_from_node(rows[0]["t"]) if rows else None

    def all_tools(self) -> list[Tool]:
        rows = self.substrate.execute_read(
            "MATCH (t:Tool) RETURN t ORDER BY t.kind ASC, t.name ASC",
        )
        return [_tool_from_node(r["t"]) for r in rows]

    def search(self, query: str) -> list[Tool]:
        """Case-insensitive substring search over name + description."""
        q = (query or "").strip().lower()
        if not q:
            return self.all_tools()
        return [
            t
            for t in self.all_tools()
            if q in t.name.lower() or q in t.description.lower()
        ]

    def depends_on(self, name: str) -> list[Tool]:
        rows = self.substrate.execute_read(
            "MATCH (:Tool {name: $name})-[:DEPENDS_ON]->(t:Tool) RETURN t",
            name=name,
        )
        return [_tool_from_node(r["t"]) for r in rows]

    # ---- Writes ----

    def upsert_builtin(self, tool: Tool) -> None:
        """Idempotent insert/update for builtin tool metadata.

        Called at substrate init for every tool registered in the Python
        registry, so ``cm_list_tools`` always reflects the full surface.
        """
        if tool.kind is not ToolKind.builtin:
            raise ValueError(
                f"upsert_builtin received non-builtin {tool.name} ({tool.kind})"
            )
        self.substrate.execute_write(
            "MERGE (t:Tool {name: $name}) "
            "ON CREATE SET t.created_at = datetime($created_at) "
            "SET t.description = $description, "
            "    t.input_schema_json = $schema, "
            "    t.kind = $kind, "
            "    t.usage = $usage, "
            "    t.install_commands = $install_commands, "
            "    t.depends_on = $depends_on, "
            "    t.installed_by_drone_id = null, "
            "    t.flagged_by_alignment = coalesce(t.flagged_by_alignment, false)",
            name=tool.name,
            description=tool.description,
            schema=tool.input_schema_json,
            kind=tool.kind.value,
            usage=tool.usage,
            install_commands=list(tool.install_commands),
            depends_on=list(tool.depends_on),
            created_at=tool.created_at.isoformat(),
        )

    def register_installed(self, tool: Tool) -> Tool:
        """Insert a drone-installed tool. Errors on duplicate name."""
        if tool.kind is not ToolKind.installed:
            raise ValueError(
                f"register_installed expects kind=installed, got {tool.kind}"
            )
        # Validate the schema parses; drones occasionally pass garbage.
        try:
            json.loads(tool.input_schema_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"input_schema_json is not valid JSON: {e}") from e
        if self.get(tool.name) is not None:
            raise ValueError(f"tool {tool.name!r} already registered")
        self.substrate.execute_write(
            "CREATE (t:Tool { "
            "  name: $name, description: $description, "
            "  input_schema_json: $schema, kind: $kind, "
            "  usage: $usage, install_commands: $install_commands, "
            "  depends_on: $depends_on, "
            "  installed_by_drone_id: $by, flagged_by_alignment: false, "
            "  created_at: datetime($created_at) "
            "})",
            name=tool.name,
            description=tool.description,
            schema=tool.input_schema_json,
            kind=tool.kind.value,
            usage=tool.usage,
            install_commands=list(tool.install_commands),
            depends_on=list(tool.depends_on),
            by=tool.installed_by_drone_id,
            created_at=tool.created_at.isoformat(),
        )
        # Wire DEPENDS_ON edges to any tools we listed that already exist.
        for dep in tool.depends_on:
            self.substrate.execute_write(
                "MATCH (t:Tool {name: $name}), (d:Tool {name: $dep}) "
                "MERGE (t)-[:DEPENDS_ON]->(d)",
                name=tool.name,
                dep=dep,
            )
        registered = self.get(tool.name)
        assert registered is not None
        return registered

    def record_usage(self, tool_name: str, gap_id: str) -> None:
        """Add a ``USED_BY`` edge from the tool to the gap (idempotent)."""
        self.substrate.execute_write(
            "MATCH (t:Tool {name: $name}), (g:Gap {id: $gap_id}) "
            "MERGE (t)-[:USED_BY]->(g)",
            name=tool_name,
            gap_id=gap_id,
        )

    def flag(self, tool_name: str, *, flagged: bool = True) -> None:
        """Alignment marks a registration as suspicious (or clears the flag)."""
        self.substrate.execute_write(
            "MATCH (t:Tool {name: $name}) "
            "SET t.flagged_by_alignment = $flagged",
            name=tool_name,
            flagged=flagged,
        )
