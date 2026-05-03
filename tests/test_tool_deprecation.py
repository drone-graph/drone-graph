"""Soft deprecation of stale / flagged installed tools and discovery filtering."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from drone_graph.tools import Tool, ToolKind, empty_input_schema, is_discoverable
from drone_graph.tools.builtins.queries import cm_list_tools
from drone_graph.tools.builtins.registry_admin import cm_deprecate_stale_tools
from drone_graph.tools.registry import DroneContext
from drone_graph.tools.store import ToolStore


@pytest.fixture
def gap_store_tool_store(substrate):
    from drone_graph.gaps import GapStore

    store = GapStore(substrate)
    tstore = ToolStore(substrate)
    yield store, tstore


def _ctx(store, tool_store, gap_id: str) -> DroneContext:
    return DroneContext(
        gap_id=gap_id,
        drone_id="test-drone",
        tick=1,
        store=store,
        tool_store=tool_store,
        terminal_box=None,
        tape=None,
        signals=None,
        active_tool_names=set(),
        suggested_tool_names=set(),
    )


def test_record_usage_revives_deprecated_tool(
    gap_store_tool_store,
) -> None:
    store, tool_store = gap_store_tool_store
    gap = store.create_root(intent="x", criteria="y")
    tool_store.register_installed(
        Tool(
            name="revival_probe",
            description="probe",
            input_schema_json=json.dumps(empty_input_schema()),
            kind=ToolKind.installed,
            usage="noop",
            installed_by_drone_id="fixture",
        )
    )
    ref = datetime.now(UTC)
    tool_store.deprecate_stale_installed_tools(
        max_age_days=0.000001,
        deprecate_flagged=False,
        reference_time=ref + timedelta(days=1),
    )
    t = tool_store.get("revival_probe")
    assert t is not None and t.deprecated_at is not None
    assert not is_discoverable(t)

    tool_store.record_usage("revival_probe", gap.id)
    t2 = tool_store.get("revival_probe")
    assert t2 is not None
    assert t2.deprecated_at is None
    assert is_discoverable(t2)


def test_cm_list_tools_excludes_deprecated_without_include(
    gap_store_tool_store,
) -> None:
    store, tool_store = gap_store_tool_store
    gap = store.create_root(intent="x", criteria="y")
    tool_store.register_installed(
        Tool(
            name="hidden_stale",
            description="hidden",
            input_schema_json=json.dumps(empty_input_schema()),
            kind=ToolKind.installed,
            usage="noop",
            installed_by_drone_id="fixture",
        )
    )
    tool_store.deprecate_stale_installed_tools(
        max_age_days=0,
        deprecate_flagged=False,
        reference_time=datetime.now(UTC) + timedelta(days=1),
    )
    ctx = _ctx(store, tool_store, gap.id)
    out = cm_list_tools({}, ctx)
    payload = json.loads(out.content)
    names = [p["name"] for p in payload]
    assert "hidden_stale" not in names

    out_all = cm_list_tools({"include_deprecated": True}, ctx)
    names_all = [p["name"] for p in json.loads(out_all.content)]
    assert "hidden_stale" in names_all


def test_cm_deprecate_stale_tools_builtin_json(gap_store_tool_store) -> None:
    store, tool_store = gap_store_tool_store
    gap = store.create_root(intent="x", criteria="y")
    tool_store.register_installed(
        Tool(
            name="align_flag_probe",
            description="probe",
            input_schema_json=json.dumps(empty_input_schema()),
            kind=ToolKind.installed,
            usage="noop",
            installed_by_drone_id="fixture",
        )
    )
    tool_store.flag("align_flag_probe", flagged=True)
    ctx = _ctx(store, tool_store, gap.id)
    res = cm_deprecate_stale_tools(
        {"max_age_days": 99999, "dry_run": False, "deprecate_flagged": True},
        ctx,
    )
    report = json.loads(res.content)
    assert report["deprecated_count"] >= 1
    assert "align_flag_probe" in report["deprecated_names"]
    t = tool_store.get("align_flag_probe")
    assert t is not None and t.deprecated_reason is not None
