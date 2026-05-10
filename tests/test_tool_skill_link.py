"""Tool nodes optionally link to on-disk skill packages (SKILL.md directories)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from drone_graph.gaps import GapStore
from drone_graph.skills_marketplace.skill_packages.paths import SKILL_ROOT_ENV
from drone_graph.substrate import Substrate
from drone_graph.tools.builtins.queries import cm_get_tool
from drone_graph.tools.builtins.worker import cm_register_tool
from drone_graph.tools.records import Tool, ToolKind
from drone_graph.tools.registry import DroneContext
from drone_graph.tools.store import ToolStore

_FIXTURE_MINIMAL = (
    Path(__file__).resolve().parent / "fixtures" / "skill_packages" / "minimal"
)
_FIXTURE_PKG_ROOT = _FIXTURE_MINIMAL.parent


@pytest.fixture
def gap_store_tool_store(substrate: Substrate) -> Iterator[tuple[GapStore, ToolStore]]:
    store = GapStore(substrate)
    tstore = ToolStore(substrate)
    yield store, tstore


def test_register_installed_round_trips_skill_fields(
    gap_store_tool_store: tuple[GapStore, ToolStore],
) -> None:
    _, tool_store = gap_store_tool_store
    path_s = str(_FIXTURE_MINIMAL.resolve())
    tool_store.register_installed(
        Tool(
            name="skill_link_roundtrip",
            description="d",
            input_schema_json='{"type":"object","properties":{}}',
            kind=ToolKind.installed,
            usage="x",
            skill_package_path=path_s,
            skill_package_id="minimal",
            installed_by_drone_id="d1",
        )
    )
    t = tool_store.get("skill_link_roundtrip")
    assert t is not None
    assert t.skill_package_path == path_s
    assert t.skill_package_id == "minimal"


def test_cm_register_tool_skill_path_exposed_in_cm_get_tool(
    gap_store_tool_store: tuple[GapStore, ToolStore],
) -> None:
    store, tool_store = gap_store_tool_store
    gap = store.create_root(intent="r", criteria="c")
    ctx = DroneContext(
        gap_id=gap.id,
        drone_id="drone-reg",
        tick=1,
        store=store,
        tool_store=tool_store,
        terminal_box=None,
        tape=None,
        signals=None,
        active_tool_names={"cm_register_tool"},
    )
    path_s = str(_FIXTURE_MINIMAL.resolve())
    reg = cm_register_tool(
        {
            "name": "linked_cli_tool",
            "description": "desc",
            "usage": "linked_cli --help",
            "skill_package_path": path_s,
        },
        ctx,
    )
    assert "registered tool" in reg.content
    got = cm_get_tool({"name": "linked_cli_tool"}, ctx)
    payload = json.loads(got.content)
    assert payload["skill_package_path"] == path_s
    assert payload["skill_package_id"] == "minimal"


def test_cm_register_tool_invalid_path_does_not_register(
    gap_store_tool_store: tuple[GapStore, ToolStore],
) -> None:
    store, tool_store = gap_store_tool_store
    gap = store.create_root(intent="r", criteria="c")
    ctx = DroneContext(
        gap_id=gap.id,
        drone_id="drone-bad",
        tick=1,
        store=store,
        tool_store=tool_store,
        terminal_box=None,
        tape=None,
        signals=None,
        active_tool_names={"cm_register_tool"},
    )
    reg = cm_register_tool(
        {
            "name": "no_such_skill_tool",
            "description": "d",
            "usage": "u",
            "skill_package_path": "/nonexistent/path/to/skill_pkg_zzz",
        },
        ctx,
    )
    assert reg.content.startswith("ERROR:")
    assert tool_store.get("no_such_skill_tool") is None


def test_cm_register_tool_id_only_requires_skill_root(
    gap_store_tool_store: tuple[GapStore, ToolStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, tool_store = gap_store_tool_store
    gap = store.create_root(intent="r", criteria="c")
    ctx = DroneContext(
        gap_id=gap.id,
        drone_id="drone-env",
        tick=1,
        store=store,
        tool_store=tool_store,
        terminal_box=None,
        tape=None,
        signals=None,
        active_tool_names={"cm_register_tool"},
    )
    monkeypatch.delenv(SKILL_ROOT_ENV, raising=False)
    reg = cm_register_tool(
        {
            "name": "id_only_tool",
            "description": "d",
            "usage": "u",
            "skill_package_id": "minimal",
        },
        ctx,
    )
    assert "ERROR:" in reg.content
    assert SKILL_ROOT_ENV in reg.content
    assert tool_store.get("id_only_tool") is None


def test_cm_register_tool_id_only_with_skill_root(
    gap_store_tool_store: tuple[GapStore, ToolStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, tool_store = gap_store_tool_store
    gap = store.create_root(intent="r", criteria="c")
    ctx = DroneContext(
        gap_id=gap.id,
        drone_id="drone-id",
        tick=1,
        store=store,
        tool_store=tool_store,
        terminal_box=None,
        tape=None,
        signals=None,
        active_tool_names={"cm_register_tool"},
    )
    monkeypatch.setenv(SKILL_ROOT_ENV, str(_FIXTURE_PKG_ROOT.resolve()))
    reg = cm_register_tool(
        {
            "name": "id_resolved_tool",
            "description": "d",
            "usage": "u",
            "skill_package_id": "minimal",
        },
        ctx,
    )
    assert "registered tool" in reg.content
    got = cm_get_tool({"name": "id_resolved_tool"}, ctx)
    payload = json.loads(got.content)
    assert payload["skill_package_id"] == "minimal"
    assert payload["skill_package_path"] == str(_FIXTURE_MINIMAL.resolve())
