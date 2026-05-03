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
from datetime import UTC, datetime, timedelta
from typing import Any

from drone_graph.embeddings.backfill import maybe_index_tool_description
from drone_graph.embeddings.search import rank_tools_by_query
from drone_graph.embeddings.sqlite_store import SQLiteEmbeddingStore
from drone_graph.embeddings.types import SCOPE_DESCRIPTION, Embedder
from drone_graph.substrate import Substrate
from drone_graph.tools.records import Tool, ToolKind


def _coerce_neo4j_dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if hasattr(val, "to_native"):
        return val.to_native()
    if isinstance(val, datetime):
        return val
    return None


def _tool_from_node(node: Any) -> Tool:
    data: dict[str, Any] = dict(node)
    # Coerce Neo4j datetime to native python datetime.
    if hasattr(data.get("created_at"), "to_native"):
        data["created_at"] = data["created_at"].to_native()
    lu = _coerce_neo4j_dt(data.get("last_used_at"))
    if lu is not None:
        data["last_used_at"] = lu
    elif data.get("created_at") is not None:
        data["last_used_at"] = data["created_at"]
    else:
        data.pop("last_used_at", None)
    da = _coerce_neo4j_dt(data.get("deprecated_at"))
    if da is not None:
        data["deprecated_at"] = da
    else:
        data.pop("deprecated_at", None)
    if data.get("deprecated_reason") == "":
        data["deprecated_reason"] = None
    if data.get("needs_venv") is None:
        data.pop("needs_venv", None)
    if data.get("trust_tier") is None:
        data.pop("trust_tier", None)
    return Tool.model_validate(data)


def is_discoverable(tool: Tool) -> bool:
    """Builtin tools always appear in discovery; installed tools only if not deprecated."""
    if tool.kind is ToolKind.builtin:
        return True
    return tool.deprecated_at is None


class ToolStore:
    """Neo4j-backed CRUD for ``:Tool`` nodes."""

    def __init__(
        self,
        substrate: Substrate,
        *,
        embedding_store: SQLiteEmbeddingStore | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.substrate = substrate
        self._embedding_store = embedding_store
        self._embedder = embedder

    @property
    def semantic_search_configured(self) -> bool:
        """True when SQLite embeddings + embedder are wired for semantic ranking."""
        return self._embedding_store is not None and self._embedder is not None

    def semantic_rank_tool_names(
        self, query: str, limit: int | None = None
    ) -> list[str]:
        """Rank tool names by semantic similarity; empty if embeddings are not configured."""
        if not self.semantic_search_configured:
            return []
        assert self._embedding_store is not None and self._embedder is not None
        return rank_tools_by_query(
            query,
            store=self._embedding_store,
            embedder=self._embedder,
            limit=limit,
        )

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
            "ON CREATE SET t.created_at = datetime($created_at), "
            "             t.last_used_at = datetime($created_at) "
            "SET t.description = $description, "
            "    t.input_schema_json = $schema, "
            "    t.kind = $kind, "
            "    t.usage = $usage, "
            "    t.install_commands = $install_commands, "
            "    t.depends_on = $depends_on, "
            "    t.needs_venv = $needs_venv, "
            "    t.installed_by_drone_id = null, "
            "    t.skill_package_path = null, "
            "    t.skill_package_id = null, "
            "    t.flagged_by_alignment = coalesce(t.flagged_by_alignment, false), "
            "    t.trust_tier = $trust_tier",
            name=tool.name,
            description=tool.description,
            schema=tool.input_schema_json,
            kind=tool.kind.value,
            usage=tool.usage,
            install_commands=list(tool.install_commands),
            depends_on=list(tool.depends_on),
            needs_venv=tool.needs_venv,
            trust_tier=tool.trust_tier.value,
            created_at=tool.created_at.isoformat(),
        )
        maybe_index_tool_description(
            tool, self._embedding_store, self._embedder
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
            "  needs_venv: $needs_venv, "
            "  skill_package_path: $skill_package_path, "
            "  skill_package_id: $skill_package_id, "
            "  installed_by_drone_id: $by, flagged_by_alignment: false, "
            "  trust_tier: $trust_tier, "
            "  created_at: datetime($created_at), "
            "  last_used_at: datetime($created_at) "
            "})",
            name=tool.name,
            description=tool.description,
            schema=tool.input_schema_json,
            kind=tool.kind.value,
            usage=tool.usage,
            install_commands=list(tool.install_commands),
            depends_on=list(tool.depends_on),
            needs_venv=tool.needs_venv,
            skill_package_path=tool.skill_package_path,
            skill_package_id=tool.skill_package_id,
            by=tool.installed_by_drone_id,
            trust_tier=tool.trust_tier.value,
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
        maybe_index_tool_description(
            registered, self._embedding_store, self._embedder
        )
        return registered

    def record_usage(self, tool_name: str, gap_id: str) -> None:
        """Add a ``USED_BY`` edge from the tool to the gap (idempotent).

        Bumps ``last_used_at`` and clears soft-deprecation so the tool can
        re-enter default discovery after reuse.
        """
        now_iso = datetime.now(UTC).isoformat()
        self.substrate.execute_write(
            "MATCH (t:Tool {name: $name}), (g:Gap {id: $gap_id}) "
            "MERGE (t)-[:USED_BY]->(g) "
            "SET t.last_used_at = datetime($now), "
            "    t.deprecated_at = null, "
            "    t.deprecated_reason = null",
            name=tool_name,
            gap_id=gap_id,
            now=now_iso,
        )

    def deprecate_stale_installed_tools(
        self,
        *,
        max_age_days: float,
        deprecate_flagged: bool = True,
        dry_run: bool = False,
        reference_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Soft-deprecate installed tools that are stale or (optionally) alignment-flagged.

        Sets ``deprecated_at`` / ``deprecated_reason``; removes description embeddings
        from the sidecar when configured. Never modifies builtins.
        """
        ref = reference_time or datetime.now(UTC)
        cutoff = ref - timedelta(days=max_age_days)
        deprecated_names: list[str] = []
        reasons: dict[str, str] = {}
        for t in self.all_tools():
            if t.kind is not ToolKind.installed or t.deprecated_at is not None:
                continue
            stale = t.last_used_at < cutoff
            flagged_hit = bool(deprecate_flagged and t.flagged_by_alignment)
            if not stale and not flagged_hit:
                continue
            parts: list[str] = []
            if flagged_hit:
                parts.append("alignment_flag")
            if stale:
                parts.append("stale")
            reason = "+".join(parts) if parts else "stale"
            deprecated_names.append(t.name)
            reasons[t.name] = reason
            if dry_run:
                continue
            now_iso = ref.isoformat()
            self.substrate.execute_write(
                "MATCH (tool:Tool {name: $name}) "
                "SET tool.deprecated_at = datetime($now), "
                "    tool.deprecated_reason = $reason",
                name=t.name,
                now=now_iso,
                reason=reason,
            )
            if self.semantic_search_configured:
                assert self._embedding_store is not None and self._embedder is not None
                self._embedding_store.delete(
                    t.name,
                    SCOPE_DESCRIPTION,
                    self._embedder.model_id,
                )
        return {
            "dry_run": dry_run,
            "deprecated_count": len(deprecated_names),
            "deprecated_names": deprecated_names,
            "reasons": reasons,
            "cutoff_iso": cutoff.isoformat(),
            "reference_iso": ref.isoformat(),
        }

    def flag(self, tool_name: str, *, flagged: bool = True) -> None:
        """Alignment marks a registration as suspicious (or clears the flag)."""
        self.substrate.execute_write(
            "MATCH (t:Tool {name: $name}) "
            "SET t.flagged_by_alignment = $flagged",
            name=tool_name,
            flagged=flagged,
        )
