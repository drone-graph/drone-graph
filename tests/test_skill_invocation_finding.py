from __future__ import annotations

import pytest

from drone_graph.gaps import FindingAuthor, FindingKind, GapStore


def test_skill_invocation_finding_round_trip(substrate) -> None:
    store = GapStore(substrate)
    gap = store.create_root(intent="invoke test", criteria="tool runs")
    metrics = '{"exit_code":0,"duration_ms":42}'
    written = store.append_finding(
        tick=7,
        author=FindingAuthor.worker,
        kind=FindingKind.skill_invocation,
        summary="demo invocation",
        affected_gap_ids=[gap.id],
        artefact_paths=["var/tapes/run/x.log"],
        invocation_tool_name="demo_tool",
        invocation_outcome="success",
        invocation_provider="anthropic",
        invocation_model="claude-haiku-4-5-20251001",
        invocation_cost_usd=0.001234,
        invocation_metrics_json=metrics,
    )
    loaded = next(f for f in store.all_findings() if f.id == written.id)
    assert loaded.kind is FindingKind.skill_invocation
    assert loaded.author is FindingAuthor.worker
    assert loaded.tick == 7
    assert loaded.summary == "demo invocation"
    assert loaded.affected_gap_ids == [gap.id]
    assert loaded.artefact_paths == ["var/tapes/run/x.log"]
    assert loaded.invocation_tool_name == "demo_tool"
    assert loaded.invocation_outcome == "success"
    assert loaded.invocation_provider == "anthropic"
    assert loaded.invocation_model == "claude-haiku-4-5-20251001"
    assert loaded.invocation_cost_usd == pytest.approx(0.001234)
    assert loaded.invocation_metrics_json == metrics


def test_append_finding_non_skill_has_null_invocation_fields(substrate) -> None:
    store = GapStore(substrate)
    gap = store.create_root(intent="a", criteria="b")
    f = store.append_finding(
        tick=1,
        author=FindingAuthor.user,
        kind=FindingKind.user_input,
        summary="hello",
        affected_gap_ids=[gap.id],
    )
    loaded = next(x for x in store.all_findings() if x.id == f.id)
    assert loaded.invocation_tool_name is None
    assert loaded.invocation_outcome is None
    assert loaded.invocation_provider is None
    assert loaded.invocation_model is None
    assert loaded.invocation_cost_usd is None
    assert loaded.invocation_metrics_json is None
