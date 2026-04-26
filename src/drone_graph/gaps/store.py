from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from drone_graph.gaps.records import (
    Finding,
    FindingAuthor,
    FindingKind,
    Gap,
    GapStatus,
    ModelTier,
)
from drone_graph.substrate import Substrate


def _to_native(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "to_native"):
        return v.to_native()
    return v


def _gap_from_node(node: Any) -> Gap:
    data: dict[str, Any] = {k: _to_native(v) for k, v in dict(node).items()}
    return Gap.model_validate(data)


def _finding_from_node(node: Any) -> Finding:
    data: dict[str, Any] = {k: _to_native(v) for k, v in dict(node).items()}
    return Finding.model_validate(data)


def _now() -> datetime:
    return datetime.now(UTC)


class GapStore:
    """Neo4j-backed tree store for gaps and findings.

    Structural invariants:
      - Exactly one ``PARENT_OF`` edge between a parent and each child.
      - A gap with an incoming ``PARENT_OF`` edge is a child; otherwise it is a root.
      - Status is one of {unfilled, filled, retired}. Retired is not deletion —
        the subtree stays in the graph with its findings intact.
      - A "leaf" is an unfilled gap with no non-retired children (so a gap whose
        children are all retired becomes a leaf again).

    Every verb (decompose/create/retire/reopen/noop/fill/fail) writes a single
    ``Finding`` node and, where applicable, mutates gap state atomically in the
    same transaction.
    """

    def __init__(self, substrate: Substrate) -> None:
        self.substrate = substrate

    # ---- Read operations ----

    def get(self, gap_id: str) -> Gap | None:
        rows = self.substrate.execute_read(
            "MATCH (g:Gap {id: $id}) RETURN g",
            id=gap_id,
        )
        if rows:
            return _gap_from_node(rows[0]["g"])
        # Prefix fallback: accept unambiguous short prefixes (matches CLI semantics
        # and how drones tend to quote ids back from rendered trees).
        if len(gap_id) >= 4:
            prefix_rows = self.substrate.execute_read(
                "MATCH (g:Gap) WHERE g.id STARTS WITH $id RETURN g LIMIT 2",
                id=gap_id,
            )
            if len(prefix_rows) == 1:
                return _gap_from_node(prefix_rows[0]["g"])
        return None

    def all_gaps(self) -> list[Gap]:
        rows = self.substrate.execute_read(
            "MATCH (g:Gap) RETURN g ORDER BY g.created_at ASC",
        )
        return [_gap_from_node(r["g"]) for r in rows]

    def children_of(self, gap_id: str) -> list[Gap]:
        rows = self.substrate.execute_read(
            "MATCH (:Gap {id: $id})-[:PARENT_OF]->(c:Gap) "
            "RETURN c ORDER BY c.created_at ASC",
            id=gap_id,
        )
        return [_gap_from_node(r["c"]) for r in rows]

    def parent_of(self, gap_id: str) -> Gap | None:
        rows = self.substrate.execute_read(
            "MATCH (p:Gap)-[:PARENT_OF]->(:Gap {id: $id}) RETURN p",
            id=gap_id,
        )
        return _gap_from_node(rows[0]["p"]) if rows else None

    def roots(self) -> list[Gap]:
        rows = self.substrate.execute_read(
            "MATCH (g:Gap) WHERE NOT ()-[:PARENT_OF]->(g) "
            "RETURN g ORDER BY g.created_at ASC",
        )
        return [_gap_from_node(r["g"]) for r in rows]

    def leaves(self) -> list[Gap]:
        """Active emergent leaves: unfilled non-preset gaps with no non-retired
        children. Preset gaps are excluded — they're not work for emergent
        worker drones."""
        rows = self.substrate.execute_read(
            "MATCH (g:Gap {status: $unfilled}) "
            "WHERE g.preset_kind IS NULL "
            "  AND NOT EXISTS { "
            "    MATCH (g)-[:PARENT_OF]->(c:Gap) WHERE c.status <> $retired "
            "  } "
            "RETURN g ORDER BY g.created_at ASC",
            unfilled=GapStatus.unfilled.value,
            retired=GapStatus.retired.value,
        )
        return [_gap_from_node(r["g"]) for r in rows]

    def parent_edges(self) -> list[tuple[str, str]]:
        """All (parent_id, child_id) pairs, for client-side tree rendering."""
        rows = self.substrate.execute_read(
            "MATCH (p:Gap)-[:PARENT_OF]->(c:Gap) "
            "RETURN p.id AS p, c.id AS c",
        )
        return [(r["p"], r["c"]) for r in rows]

    def recent_findings(self, limit: int = 30) -> list[Finding]:
        """Most recent findings, oldest first (ready to render chronologically)."""
        rows = self.substrate.execute_read(
            "MATCH (f:Finding) RETURN f ORDER BY f.tick DESC, f.created_at DESC LIMIT $limit",
            limit=limit,
        )
        findings = [_finding_from_node(r["f"]) for r in rows]
        findings.reverse()
        return findings

    def all_findings(self) -> list[Finding]:
        rows = self.substrate.execute_read(
            "MATCH (f:Finding) RETURN f ORDER BY f.tick ASC, f.created_at ASC",
        )
        return [_finding_from_node(r["f"]) for r in rows]

    def find_leaf_by_intent_substring(self, needle: str) -> Gap | None:
        """Scenario helper: match a live leaf by case-insensitive substring on intent."""
        needle_lc = needle.lower()
        for leaf in self.leaves():
            if needle_lc in leaf.intent.lower():
                return leaf
        return None

    # ---- Structural verbs (Gap Finding) ----

    def apply_decompose(
        self,
        *,
        parent_id: str,
        children: list[dict[str, str]],
        rationale: str,
        tick: int,
        author: FindingAuthor = FindingAuthor.gap_finding,
    ) -> Finding:
        if not children:
            raise ValueError("decompose requires at least one child")
        parent = self.get(parent_id)
        if parent is None:
            raise ValueError(f"no gap with id {parent_id}")
        if parent.status is GapStatus.retired:
            raise ValueError(f"cannot decompose retired gap {parent_id}")
        parent_id = parent.id  # normalize: accept short prefixes, use full id downstream
        # Decomposition is additive: if the parent already has active children,
        # new children are appended. Duplicate-intent children are silently
        # dropped so a confused GF pass can't mint the same leaf twice. Alignment
        # still has the final say on whether a given set of children is sound.
        existing_active = [
            c for c in self.children_of(parent_id) if c.status is not GapStatus.retired
        ]
        existing_intents_lc = {c.intent.strip().lower() for c in existing_active}
        child_records = [
            Gap(
                intent=c["intent"],
                criteria=c["criteria"],
                tool_loadout=list(c.get("tool_loadout", []) or []),
                tool_suggestions=list(c.get("tool_suggestions", []) or []),
            )
            for c in children
            if c["intent"].strip().lower() not in existing_intents_lc
        ]
        if not child_records:
            raise ValueError(
                f"gap {parent_id}: all proposed children duplicate existing active children"
            )
        finding = Finding(
            tick=tick,
            author=author,
            kind=FindingKind.decompose,
            summary=f"Decomposed {parent_id} into {len(child_records)} children: {rationale}",
            affected_gap_ids=[parent_id, *[c.id for c in child_records]],
        )

        self.substrate.execute_write(
            "MATCH (p:Gap {id: $parent_id}) "
            "WITH p "
            "UNWIND $children AS child "
            "CREATE (c:Gap { "
            "  id: child.id, intent: child.intent, criteria: child.criteria, "
            "  status: $unfilled, reopen_count: 0, retire_reason: null, "
            "  model_tier: $standard, created_at: datetime(child.created_at), "
            "  tool_loadout: child.tool_loadout, "
            "  tool_suggestions: child.tool_suggestions, "
            "  context_preload: [], preset_kind: null "
            "}) "
            "CREATE (p)-[:PARENT_OF]->(c) "
            "WITH p, collect(c.id) AS child_ids "
            "SET p.status = CASE WHEN p.status = $filled THEN $unfilled ELSE p.status END "
            "WITH p, child_ids "
            "CREATE (f:Finding { "
            "  id: $finding_id, tick: $tick, author: $author, kind: $kind, "
            "  summary: $summary, affected_gap_ids: $affected, "
            "  artefact_paths: [], "
            "  created_at: datetime($finding_created_at) "
            "}) "
            "WITH p, f, child_ids "
            "UNWIND child_ids AS cid "
            "MATCH (c:Gap {id: cid}) "
            "CREATE (f)-[:AFFECTS]->(c) "
            "WITH DISTINCT f, p "
            "CREATE (f)-[:AFFECTS]->(p)",
            parent_id=parent_id,
            children=[
                {
                    "id": c.id,
                    "intent": c.intent,
                    "criteria": c.criteria,
                    "created_at": c.created_at.isoformat(),
                    "tool_loadout": list(c.tool_loadout),
                    "tool_suggestions": list(c.tool_suggestions),
                }
                for c in child_records
            ],
            finding_id=finding.id,
            tick=tick,
            author=author.value,
            kind=FindingKind.decompose.value,
            summary=finding.summary,
            affected=finding.affected_gap_ids,
            finding_created_at=finding.created_at.isoformat(),
            unfilled=GapStatus.unfilled.value,
            filled=GapStatus.filled.value,
            standard=ModelTier.standard.value,
        )
        return finding

    def apply_create(
        self,
        *,
        intent: str,
        criteria: str,
        rationale: str,
        tick: int,
        author: FindingAuthor = FindingAuthor.gap_finding,
        tier: ModelTier = ModelTier.standard,
        tool_loadout: list[str] | None = None,
        tool_suggestions: list[str] | None = None,
    ) -> Finding:
        new_gap = Gap(
            intent=intent,
            criteria=criteria,
            model_tier=tier,
            tool_loadout=list(tool_loadout or []),
            tool_suggestions=list(tool_suggestions or []),
        )
        finding = Finding(
            tick=tick,
            author=author,
            kind=FindingKind.create,
            summary=f"Created top-level gap {new_gap.id}: {rationale}",
            affected_gap_ids=[new_gap.id],
        )
        self.substrate.execute_write(
            "CREATE (g:Gap { "
            "  id: $gid, intent: $intent, criteria: $criteria, "
            "  status: $unfilled, reopen_count: 0, retire_reason: null, "
            "  model_tier: $tier, created_at: datetime($created_at), "
            "  tool_loadout: $tool_loadout, tool_suggestions: $tool_suggestions, "
            "  context_preload: [], preset_kind: null "
            "}) "
            "CREATE (f:Finding { "
            "  id: $finding_id, tick: $tick, author: $author, kind: $kind, "
            "  summary: $summary, affected_gap_ids: $affected, "
            "  artefact_paths: [], "
            "  created_at: datetime($finding_created_at) "
            "}) "
            "CREATE (f)-[:AFFECTS]->(g)",
            gid=new_gap.id,
            intent=intent,
            criteria=criteria,
            tier=new_gap.model_tier.value,
            created_at=new_gap.created_at.isoformat(),
            tool_loadout=list(new_gap.tool_loadout),
            tool_suggestions=list(new_gap.tool_suggestions),
            finding_id=finding.id,
            tick=tick,
            author=author.value,
            kind=FindingKind.create.value,
            summary=finding.summary,
            affected=finding.affected_gap_ids,
            finding_created_at=finding.created_at.isoformat(),
            unfilled=GapStatus.unfilled.value,
        )
        return finding

    def apply_retire(
        self,
        *,
        gap_id: str,
        reason: str,
        tick: int,
        author: FindingAuthor = FindingAuthor.gap_finding,
    ) -> Finding:
        g = self.get(gap_id)
        if g is None:
            raise ValueError(f"no gap with id {gap_id}")
        if g.status is GapStatus.retired:
            raise ValueError(f"gap {gap_id} already retired")
        gap_id = g.id  # normalize prefix → full id

        # Mark root + all descendants retired in one transaction.
        rows = self.substrate.execute_write(
            "MATCH (root:Gap {id: $gap_id}) "
            "SET root.status = $retired, root.retire_reason = $reason "
            "WITH root "
            "OPTIONAL MATCH (root)-[:PARENT_OF*1..]->(desc:Gap) "
            "WHERE desc.status <> $retired "
            "WITH root, collect(desc) AS descendants "
            "FOREACH (d IN descendants | "
            "  SET d.status = $retired, "
            "      d.retire_reason = coalesce(d.retire_reason, 'parent_retired:' + $reason) "
            ") "
            "RETURN root.id AS root_id, [d IN descendants | d.id] AS desc_ids",
            gap_id=gap_id,
            retired=GapStatus.retired.value,
            reason=reason,
        )
        affected = [rows[0]["root_id"]] + list(rows[0]["desc_ids"]) if rows else [gap_id]

        finding = Finding(
            tick=tick,
            author=author,
            kind=FindingKind.retire,
            summary=f"Retired subtree rooted at {gap_id}: {reason}",
            affected_gap_ids=affected,
        )
        self._write_finding_node(finding)
        return finding

    def apply_reopen(
        self,
        *,
        gap_id: str,
        reason: str,
        tick: int,
        author: FindingAuthor = FindingAuthor.gap_finding,
    ) -> Finding:
        g = self.get(gap_id)
        if g is None:
            raise ValueError(f"no gap with id {gap_id}")
        if g.status is not GapStatus.filled:
            raise ValueError(
                f"can only reopen a filled gap; {gap_id} is {g.status.value}"
            )
        gap_id = g.id  # normalize prefix → full id
        self.substrate.execute_write(
            "MATCH (g:Gap {id: $id}) "
            "SET g.status = $unfilled, g.reopen_count = coalesce(g.reopen_count, 0) + 1",
            id=gap_id,
            unfilled=GapStatus.unfilled.value,
        )
        finding = Finding(
            tick=tick,
            author=author,
            kind=FindingKind.reopen,
            summary=f"Reopened {gap_id}: {reason}",
            affected_gap_ids=[gap_id],
        )
        self._write_finding_node(finding)
        return finding

    def apply_rewrite_intent(
        self,
        *,
        gap_id: str,
        new_intent: str,
        new_criteria: str,
        rationale: str,
        tick: int,
        author: FindingAuthor = FindingAuthor.gap_finding,
    ) -> Finding:
        """Rewrite an unfilled gap's intent + criteria. Emits an audit finding.

        Guardrails:
          - Only works on unfilled gaps. Filled/retired gaps are terminal states
            with settled semantics — rewriting them is nonsense.
          - Captures old intent/criteria verbatim in the finding summary so
            history is preserved even though the node is mutated.
          - Affects every descendant (they now serve a reframed parent), so
            alignment can see the blast radius and flag incoherence.
        """
        if not new_intent.strip():
            raise ValueError("rewrite_intent requires a non-empty new_intent")
        if not new_criteria.strip():
            raise ValueError("rewrite_intent requires a non-empty new_criteria")
        g = self.get(gap_id)
        if g is None:
            raise ValueError(f"no gap with id {gap_id}")
        if g.status is not GapStatus.unfilled:
            raise ValueError(
                f"can only rewrite_intent on unfilled gap; {gap_id} is {g.status.value}"
            )
        gap_id = g.id  # normalize prefix → full id
        old_intent = g.intent
        old_criteria = g.criteria
        # Compute blast radius — every descendant inherits the reframed meaning.
        rows = self.substrate.execute_read(
            "MATCH (:Gap {id: $id})-[:PARENT_OF*1..]->(d:Gap) RETURN d.id AS id",
            id=gap_id,
        )
        descendant_ids = [r["id"] for r in rows]
        self.substrate.execute_write(
            "MATCH (g:Gap {id: $id}) SET g.intent = $intent, g.criteria = $criteria",
            id=gap_id,
            intent=new_intent,
            criteria=new_criteria,
        )
        summary = (
            f"Rewrote intent of {gap_id}. "
            f"Rationale: {rationale}\n"
            f"--- OLD intent ---\n{old_intent}\n"
            f"--- OLD criteria ---\n{old_criteria}\n"
            f"--- NEW intent ---\n{new_intent}\n"
            f"--- NEW criteria ---\n{new_criteria}"
        )
        finding = Finding(
            tick=tick,
            author=author,
            kind=FindingKind.rewrite_intent,
            summary=summary,
            affected_gap_ids=[gap_id, *descendant_ids],
        )
        self._write_finding_node(finding)
        return finding

    def apply_noop(
        self,
        *,
        rationale: str,
        tick: int,
        author: FindingAuthor = FindingAuthor.gap_finding,
    ) -> Finding:
        finding = Finding(
            tick=tick,
            author=author,
            kind=FindingKind.noop,
            summary=f"No structural edit this invocation: {rationale}",
            affected_gap_ids=[],
        )
        self._write_finding_node(finding)
        return finding

    # ---- Worker outcomes ----

    def apply_fill(
        self,
        *,
        gap_id: str,
        summary: str,
        tick: int,
        author: FindingAuthor = FindingAuthor.worker,
        artefact_paths: list[str] | None = None,
    ) -> Finding:
        g = self.get(gap_id)
        if g is None:
            raise ValueError(f"no gap with id {gap_id}")
        if g.status is GapStatus.retired:
            raise ValueError(f"cannot fill retired gap {gap_id}")
        gap_id = g.id  # normalize prefix → full id
        if g.status is GapStatus.unfilled:
            self.substrate.execute_write(
                "MATCH (g:Gap {id: $id}) SET g.status = $filled",
                id=gap_id,
                filled=GapStatus.filled.value,
            )
        finding = Finding(
            tick=tick,
            author=author,
            kind=FindingKind.fill,
            summary=summary,
            affected_gap_ids=[gap_id],
            artefact_paths=list(artefact_paths or []),
        )
        self._write_finding_node(finding)
        self._propagate_fill_upwards(gap_id, tick=tick)
        return finding

    # ---- Deterministic rollup ----

    def _propagate_fill_upwards(self, child_id: str, *, tick: int) -> None:
        """Walk up the parent chain, auto-filling any parent whose non-retired
        children are all filled. Each rollup emits a separate ``system`` fill
        finding so Alignment and Gap Finding can see and contest it. Preset
        gaps are never auto-filled — they're persistent by design.
        """
        current_child_id = child_id
        while True:
            parent = self.parent_of(current_child_id)
            if parent is None or parent.status is not GapStatus.unfilled:
                return
            if parent.preset_kind is not None:
                return  # preset gaps never close
            siblings = self.children_of(parent.id)
            active = [s for s in siblings if s.status is not GapStatus.retired]
            if not active:
                # Parent has only retired descendants (or none); don't auto-fill.
                return
            if any(s.status is not GapStatus.filled for s in active):
                return
            self.substrate.execute_write(
                "MATCH (g:Gap {id: $id}) SET g.status = $filled",
                id=parent.id,
                filled=GapStatus.filled.value,
            )
            filled_child_ids = [s.id for s in active]
            rollup_summary = (
                f"Auto-filled {parent.id} because all {len(active)} non-retired "
                f"children are filled: {', '.join(c[:8] for c in filled_child_ids)}."
            )
            rollup = Finding(
                tick=tick,
                author=FindingAuthor.system,
                kind=FindingKind.fill,
                summary=rollup_summary,
                affected_gap_ids=[parent.id, *filled_child_ids],
            )
            self._write_finding_node(rollup)
            current_child_id = parent.id

    def apply_fail(
        self,
        *,
        gap_id: str,
        summary: str,
        tick: int,
        author: FindingAuthor = FindingAuthor.worker,
        artefact_paths: list[str] | None = None,
    ) -> Finding:
        # Worker fail does NOT change gap status — it's a finding GF reads and
        # decides what to do (decompose / retire / create / noop).
        finding = Finding(
            tick=tick,
            author=author,
            kind=FindingKind.fail,
            summary=summary,
            affected_gap_ids=[gap_id] if gap_id else [],
            artefact_paths=list(artefact_paths or []),
        )
        self._write_finding_node(finding)
        return finding

    # ---- Raw finding append (external signals: user_input, alignment) ----

    def append_finding(
        self,
        *,
        tick: int,
        author: FindingAuthor,
        kind: FindingKind,
        summary: str,
        affected_gap_ids: list[str] | None = None,
    ) -> Finding:
        finding = Finding(
            tick=tick,
            author=author,
            kind=kind,
            summary=summary,
            affected_gap_ids=list(affected_gap_ids or []),
        )
        self._write_finding_node(finding)
        return finding

    # ---- Bootstrap ----

    def create_root(
        self,
        *,
        intent: str,
        criteria: str,
        tier: ModelTier = ModelTier.standard,
        tool_loadout: list[str] | None = None,
        tool_suggestions: list[str] | None = None,
    ) -> Gap:
        gap = Gap(
            intent=intent,
            criteria=criteria,
            model_tier=tier,
            tool_loadout=list(tool_loadout or []),
            tool_suggestions=list(tool_suggestions or []),
        )
        self.substrate.execute_write(
            "CREATE (g:Gap { "
            "  id: $id, intent: $intent, criteria: $criteria, "
            "  status: $status, reopen_count: 0, retire_reason: null, "
            "  model_tier: $tier, created_at: datetime($created_at), "
            "  tool_loadout: $tool_loadout, tool_suggestions: $tool_suggestions, "
            "  context_preload: [], preset_kind: null "
            "})",
            id=gap.id,
            intent=gap.intent,
            criteria=gap.criteria,
            status=gap.status.value,
            tier=gap.model_tier.value,
            created_at=gap.created_at.isoformat(),
            tool_loadout=list(gap.tool_loadout),
            tool_suggestions=list(gap.tool_suggestions),
        )
        return gap

    def upsert_preset(
        self,
        *,
        preset_kind: str,
        intent: str,
        criteria: str,
        tool_loadout: list[str],
        tool_suggestions: list[str] | None = None,
        context_preload: list[str] | None = None,
        tier: ModelTier = ModelTier.standard,
    ) -> Gap:
        """Idempotently create-or-update a preset gap with stable id ``preset:<kind>``.

        Preset gaps are persistent (never retired by the loop), have explicit
        tool loadouts, and carry a ``context_preload`` of query names the
        runtime fetches before dispatching a drone — so e.g. Gap Finding
        doesn't waste its first turn on an obvious "show me the tree" query.
        """
        stable_id = f"preset:{preset_kind}"
        existing = self.get(stable_id)
        if existing is None:
            gap = Gap(
                id=stable_id,
                intent=intent,
                criteria=criteria,
                model_tier=tier,
                tool_loadout=list(tool_loadout),
                tool_suggestions=list(tool_suggestions or []),
                context_preload=list(context_preload or []),
                preset_kind=preset_kind,
            )
            self.substrate.execute_write(
                "CREATE (g:Gap { "
                "  id: $id, intent: $intent, criteria: $criteria, "
                "  status: $status, reopen_count: 0, retire_reason: null, "
                "  model_tier: $tier, created_at: datetime($created_at), "
                "  tool_loadout: $tool_loadout, tool_suggestions: $tool_suggestions, "
                "  context_preload: $context_preload, preset_kind: $preset_kind "
                "})",
                id=gap.id,
                intent=gap.intent,
                criteria=gap.criteria,
                status=gap.status.value,
                tier=gap.model_tier.value,
                created_at=gap.created_at.isoformat(),
                tool_loadout=list(gap.tool_loadout),
                tool_suggestions=list(gap.tool_suggestions),
                context_preload=list(gap.context_preload),
                preset_kind=gap.preset_kind,
            )
            return gap
        # Update intent / criteria / tools while keeping id, status, history.
        self.substrate.execute_write(
            "MATCH (g:Gap {id: $id}) "
            "SET g.intent = $intent, g.criteria = $criteria, "
            "    g.tool_loadout = $tool_loadout, "
            "    g.tool_suggestions = $tool_suggestions, "
            "    g.context_preload = $context_preload, "
            "    g.preset_kind = $preset_kind",
            id=stable_id,
            intent=intent,
            criteria=criteria,
            tool_loadout=list(tool_loadout),
            tool_suggestions=list(tool_suggestions or []),
            context_preload=list(context_preload or []),
            preset_kind=preset_kind,
        )
        updated = self.get(stable_id)
        assert updated is not None
        return updated

    def get_preset(self, preset_kind: str) -> Gap | None:
        return self.get(f"preset:{preset_kind}")

    def reset_all(self) -> None:
        """Wipe the whole substrate. Used at scenario start to guarantee isolation."""
        self.substrate.execute_write("MATCH (n) DETACH DELETE n")

    # ---- Internal ----

    def _write_finding_node(self, finding: Finding) -> None:
        self.substrate.execute_write(
            "CREATE (f:Finding { "
            "  id: $id, tick: $tick, author: $author, kind: $kind, "
            "  summary: $summary, affected_gap_ids: $affected, "
            "  artefact_paths: $paths, "
            "  created_at: datetime($created_at) "
            "}) "
            "WITH f "
            "UNWIND $affected AS gid "
            "MATCH (g:Gap {id: gid}) "
            "CREATE (f)-[:AFFECTS]->(g)",
            id=finding.id,
            tick=finding.tick,
            author=finding.author.value,
            kind=finding.kind.value,
            summary=finding.summary,
            affected=finding.affected_gap_ids,
            paths=list(finding.artefact_paths),
            created_at=finding.created_at.isoformat(),
        )
