"""Context preloaders for preset gaps.

When a drone is dispatched against a preset gap, the runtime runs the gap's
``context_preload`` queries and concatenates their renderings into the drone's
initial user message. This avoids the wasted "obvious query" first turn for
the common case while still letting the drone pull more via cm_* tools.
"""

from __future__ import annotations

from typing import Callable

from drone_graph.gaps import GapStore


def _render_recent_findings(store: GapStore, limit: int = 30) -> str:
    findings = store.all_findings()
    if not findings:
        return "## Recent findings\n(none)\n"
    findings = findings[-limit:]
    lines = ["## Recent findings (oldest → newest)"]
    for f in findings:
        ids = ",".join(g[:8] for g in f.affected_gap_ids[:4])
        if len(f.affected_gap_ids) > 4:
            ids += f",+{len(f.affected_gap_ids) - 4}"
        artefact_marker = f"  [paths: {len(f.artefact_paths)}]" if f.artefact_paths else ""
        summary = f.summary.replace("\n", " ")
        if len(summary) > 220:
            summary = summary[:217] + "…"
        lines.append(
            f"- {f.id[:8]}  tick={f.tick:<3}  {f.author.value:<11}  "
            f"{f.kind.value:<28}  affects=[{ids}]{artefact_marker}\n"
            f"      {summary}"
        )
    return "\n".join(lines) + "\n"


def _render_leaves(store: GapStore) -> str:
    leaves = store.leaves()
    if not leaves:
        return "## Active leaves (emergent, awaiting workers)\n(none)\n"
    lines = ["## Active leaves (emergent, awaiting workers)"]
    for leaf in leaves:
        intent = leaf.intent.replace("\n", " ")
        if len(intent) > 140:
            intent = intent[:137] + "…"
        lines.append(f"- {leaf.id[:8]}  {intent}")
    return "\n".join(lines) + "\n"


def _render_tree_shape(store: GapStore) -> str:
    """Compact rendering of the gap tree (id prefixes, status, intent prefix)."""
    roots = [g for g in store.roots() if g.preset_kind is None]
    presets = [g for g in store.all_gaps() if g.preset_kind is not None]
    if not roots and not presets:
        return "## Tree shape\n(empty)\n"
    out: list[str] = ["## Tree shape"]
    if presets:
        out.append("Preset gaps (persistent):")
        for p in presets:
            out.append(f"  ★ [{p.preset_kind}] {p.id}")
    out.append("Emergent tree:")
    edges = store.parent_edges()
    children_of: dict[str, list[str]] = {}
    for parent_id, child_id in edges:
        children_of.setdefault(parent_id, []).append(child_id)

    def _walk(node_id: str, depth: int) -> None:
        gap = store.get(node_id)
        if gap is None:
            return
        intent = gap.intent.replace("\n", " ")
        if len(intent) > 100:
            intent = intent[:97] + "…"
        marker = {"unfilled": "⊘", "filled": "✓", "retired": "↯"}.get(
            gap.status.value, "?"
        )
        out.append("  " + "  " * depth + f"{marker} [{gap.id[:8]}] {intent}")
        for cid in children_of.get(node_id, []):
            _walk(cid, depth + 1)

    for root in roots:
        _walk(root.id, 0)
    return "\n".join(out) + "\n"


PRELOADERS: dict[str, Callable[[GapStore], str]] = {
    "recent_findings": _render_recent_findings,
    "leaves": _render_leaves,
    "tree_shape": _render_tree_shape,
}


def render_preloads(store: GapStore, preload_names: list[str]) -> str:
    """Run the listed preloaders and concatenate their output."""
    parts: list[str] = []
    for name in preload_names:
        fn = PRELOADERS.get(name)
        if fn is None:
            parts.append(f"## (unknown preload: {name})\n")
            continue
        parts.append(fn(store))
    return "\n".join(parts)
