"""cm_findings filters for skill_invocation (Step 3, no ML)."""

from __future__ import annotations

import json

from drone_graph.gaps import FindingAuthor, FindingKind, GapStore
from drone_graph.tools.builtins.queries import cm_findings
from drone_graph.tools.registry import DroneContext
from drone_graph.tools.store import ToolStore


def test_cm_findings_skill_invocation_order_and_filters(substrate) -> None:
    store = GapStore(substrate)
    tool_store = ToolStore(substrate)
    gap = store.create_root(intent="task", criteria="done")

    store.append_finding(
        tick=1,
        author=FindingAuthor.worker,
        kind=FindingKind.skill_invocation,
        summary="first",
        affected_gap_ids=[gap.id],
        invocation_tool_name="demo_cli",
        invocation_outcome="success",
        invocation_metrics_json='{"run":1}',
    )
    store.append_finding(
        tick=2,
        author=FindingAuthor.worker,
        kind=FindingKind.skill_invocation,
        summary="second",
        affected_gap_ids=[gap.id],
        invocation_tool_name="demo_cli",
        invocation_outcome="failure",
        invocation_metrics_json='{"run":2}',
    )

    ctx = DroneContext(
        gap_id=gap.id,
        drone_id="d1",
        tick=0,
        store=store,
        tool_store=tool_store,
    )

    raw = cm_findings(
        {
            "kind": "skill_invocation",
            "invocation_tool_name": "demo_cli",
            "limit": 50,
        },
        ctx,
    )
    rows = json.loads(raw.content)
    assert len(rows) == 2
    assert rows[0]["tick"] == 1
    assert rows[0]["invocation_outcome"] == "success"
    assert rows[1]["tick"] == 2
    assert rows[1]["invocation_outcome"] == "failure"

    raw_success = cm_findings(
        {
            "kind": "skill_invocation",
            "invocation_tool_name": "demo_cli",
            "invocation_outcome": "success",
            "limit": 50,
        },
        ctx,
    )
    succ = json.loads(raw_success.content)
    assert len(succ) == 1
    assert succ[0]["tick"] == 1

    raw_other = cm_findings(
        {
            "kind": "skill_invocation",
            "invocation_tool_name": "other_tool",
            "limit": 50,
        },
        ctx,
    )
    assert json.loads(raw_other.content) == []
