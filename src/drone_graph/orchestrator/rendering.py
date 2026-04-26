"""Render the current collective-mind state for drone prompts.

Each drone invocation sees a plain-text snapshot: the gap tree, a capped window
of recent findings, and (for Gap Finding) the active-leaf buffer size against
target. The data lives in Neo4j; this module loads it once per render and
builds the string client-side.
"""

from __future__ import annotations

from collections import defaultdict

from drone_graph.gaps import Finding, Gap, GapStatus, GapStore

_STATUS_MARKER: dict[GapStatus, str] = {
    GapStatus.unfilled: "○",
    GapStatus.filled: "●",
    GapStatus.retired: "⊘",
}


def render_tree(store: GapStore) -> str:
    """Indented outline of the gap tree, one line per gap."""
    gaps = store.all_gaps()
    if not gaps:
        return "(empty graph)"
    by_id: dict[str, Gap] = {g.id: g for g in gaps}
    children_of: dict[str, list[str]] = defaultdict(list)
    has_parent: set[str] = set()
    for parent_id, child_id in store.parent_edges():
        children_of[parent_id].append(child_id)
        has_parent.add(child_id)
    root_ids = [g.id for g in gaps if g.id not in has_parent]

    # Keep sibling order stable: by created_at, which is already the order gaps come in.
    created_order: dict[str, int] = {g.id: i for i, g in enumerate(gaps)}
    for cs in children_of.values():
        cs.sort(key=lambda gid: created_order.get(gid, 0))
    root_ids.sort(key=lambda gid: created_order.get(gid, 0))

    lines: list[str] = []

    def walk(gid: str, depth: int) -> None:
        g = by_id[gid]
        indent = "  " * depth
        marker = _STATUS_MARKER[g.status]
        lines.append(f"{indent}{marker} [{g.id}] {g.intent}")
        lines.append(f"{indent}  criteria: {g.criteria}")
        if g.status is GapStatus.retired and g.retire_reason:
            lines.append(f"{indent}  retired: {g.retire_reason}")
        for cid in children_of.get(gid, []):
            walk(cid, depth + 1)

    for rid in root_ids:
        walk(rid, 0)
    return "\n".join(lines)


def render_findings(store: GapStore, limit: int = 30) -> str:
    """Chronological findings, oldest first. Capped at ``limit`` most-recent entries."""
    findings: list[Finding] = store.recent_findings(limit=limit)
    if not findings:
        return "(no findings yet)"
    lines = []
    for f in findings:
        affected = ",".join(f.affected_gap_ids) if f.affected_gap_ids else "-"
        lines.append(
            f"[tick {f.tick}] [{f.author.value}:{f.kind.value}] ({affected}) {f.summary}"
        )
    return "\n".join(lines)


def render_state_for_drone(
    store: GapStore,
    *,
    tick: int,
    target_leaves: int,
) -> str:
    """What Gap Finding sees each invocation."""
    roots = store.roots()
    root_intent = roots[0].intent if roots else "(no root)"
    root_criteria = roots[0].criteria if roots else "(no root)"
    leaves = store.leaves()
    return (
        f"## Tree (tick {tick})\n\n"
        f"Root intent: {root_intent}\n"
        f"Root criteria: {root_criteria}\n\n"
        f"Current active leaves: {len(leaves)} / target {target_leaves}\n\n"
        f"{render_tree(store)}\n\n"
        f"## Recent findings (most recent last)\n\n"
        f"{render_findings(store)}\n"
    )


def render_state_for_alignment(store: GapStore, *, tick: int) -> str:
    """What Alignment sees each invocation. No target-leaves line."""
    roots = store.roots()
    root_intent = roots[0].intent if roots else "(no root)"
    root_criteria = roots[0].criteria if roots else "(no root)"
    return (
        f"## Tree (snapshot at tick {tick})\n\n"
        f"Root intent: {root_intent}\n"
        f"Root criteria: {root_criteria}\n\n"
        f"{render_tree(store)}\n\n"
        f"## Findings (chronological, most recent last)\n\n"
        f"{render_findings(store, limit=999)}\n"
    )
