"""Integration tests for gap tree semantics (leaves, fill, rollup, fail).

Earlier revisions targeted a BLOCKED_BY + ``run_once`` orchestrator that no
longer exists. These tests assert the current Neo4j-backed behavior: ``PARENT_OF``
tree, ``leaves()``, worker ``apply_fill`` / ``apply_fail``, and auto-rollup.
"""

from __future__ import annotations

import time

import pytest

from drone_graph.gaps import FindingKind, GapStatus, GapStore


pytestmark = pytest.mark.integration


def test_leaves_only_deepest_unfilled_gaps(substrate) -> None:
    """Only gaps with no active children are leaves."""
    store = GapStore(substrate)
    root = store.create_root(intent="root", criteria="root crit")
    store.apply_decompose(
        parent_id=root.id,
        children=[{"intent": "mid", "criteria": "mid crit"}],
        rationale="one child",
        tick=1,
    )
    mid = store.children_of(root.id)[0]
    store.apply_decompose(
        parent_id=mid.id,
        children=[{"intent": "deep leaf", "criteria": "leaf crit"}],
        rationale="grandchild",
        tick=2,
    )
    leaves = store.leaves()
    assert len(leaves) == 1
    assert leaves[0].intent == "deep leaf"


def test_sibling_leaves_ordered_by_created_at(substrate) -> None:
    store = GapStore(substrate)
    root = store.create_root(intent="root", criteria="root crit")
    store.apply_decompose(
        parent_id=root.id,
        children=[{"intent": "first leaf", "criteria": "c1"}],
        rationale="a",
        tick=1,
    )
    time.sleep(0.01)
    store.apply_decompose(
        parent_id=root.id,
        children=[{"intent": "second leaf", "criteria": "c2"}],
        rationale="b",
        tick=2,
    )
    leaves = store.leaves()
    assert [g.intent for g in leaves] == ["first leaf", "second leaf"]


def test_auto_rollup_fills_parent_when_all_children_filled(substrate) -> None:
    store = GapStore(substrate)
    root = store.create_root(intent="root", criteria="root crit")
    store.apply_decompose(
        parent_id=root.id,
        children=[
            {"intent": "child a", "criteria": "ca"},
            {"intent": "child b", "criteria": "cb"},
        ],
        rationale="split",
        tick=1,
    )
    children = store.children_of(root.id)
    assert len(children) == 2
    store.apply_fill(gap_id=children[0].id, summary="done a", tick=2)
    assert store.get(root.id) is not None
    assert store.get(root.id).status is GapStatus.unfilled
    store.apply_fill(gap_id=children[1].id, summary="done b", tick=3)
    reloaded = store.get(root.id)
    assert reloaded is not None
    assert reloaded.status is GapStatus.filled


def test_apply_fail_does_not_fill_gap(substrate) -> None:
    """Worker fail is a finding; gap status stays unfilled (GF decides next)."""
    store = GapStore(substrate)
    g = store.create_root(intent="task", criteria="crit")
    store.apply_fail(gap_id=g.id, summary="stub failure", tick=1)
    reloaded = store.get(g.id)
    assert reloaded is not None
    assert reloaded.status is GapStatus.unfilled
    fails = [f for f in store.all_findings() if f.kind is FindingKind.fail]
    assert len(fails) == 1
    assert fails[0].summary == "stub failure"
