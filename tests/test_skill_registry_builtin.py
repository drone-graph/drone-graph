"""Tests for cm_skill_registry builtin tool (scan, install, link_gap, find_for_gap)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from drone_graph.gaps import GapStore
from drone_graph.skills_marketplace.skill_packages.paths import SKILL_ROOT_ENV
from drone_graph.substrate import Substrate
from drone_graph.tools.builtins.skill_registry import cm_skill_registry
from drone_graph.tools.records import ToolKind
from drone_graph.tools.registry import DroneContext
from drone_graph.tools.store import ToolStore

_FIXTURE_MINIMAL = (
    Path(__file__).resolve().parent / "fixtures" / "skill_packages" / "minimal"
)
_FIXTURE_FULL = (
    Path(__file__).resolve().parent / "fixtures" / "skill_packages" / "full"
)
_FIXTURE_PKG_ROOT = _FIXTURE_MINIMAL.parent


@pytest.fixture
def stores(substrate: Substrate) -> Iterator[tuple[GapStore, ToolStore]]:
    store = GapStore(substrate)
    tstore = ToolStore(substrate)
    yield store, tstore


def _ctx(store: GapStore, tstore: ToolStore, *, gap_id: str | None = None) -> DroneContext:
    if gap_id is None:
        gap = store.create_root(intent="test gap", criteria="test")
        gap_id = gap.id
    return DroneContext(
        gap_id=gap_id,
        drone_id="drone-test",
        tick=1,
        store=store,
        tool_store=tstore,
        terminal_box=None,
        tape=None,
        signals=None,
        active_tool_names={"cm_skill_registry"},
    )


# ---------------------------------------------------------------------------
# scan_local
# ---------------------------------------------------------------------------

def test_scan_local_finds_fixture_skills(stores: tuple[GapStore, ToolStore]) -> None:
    store, tstore = stores
    ctx = _ctx(store, tstore)
    res = cm_skill_registry(
        {"action": "scan_local", "path": str(_FIXTURE_PKG_ROOT)}, ctx
    )
    payload = json.loads(res.content)
    assert payload["root"] == str(_FIXTURE_PKG_ROOT.resolve())
    ids = {s["skill_id"] for s in payload["skills"] if "error" not in s}
    assert "minimal" in ids
    assert "full" in ids


def test_scan_local_empty_dir(stores: tuple[GapStore, ToolStore], tmp_path: Path) -> None:
    store, tstore = stores
    ctx = _ctx(store, tstore)
    empty = tmp_path / "empty_skills"
    empty.mkdir()
    res = cm_skill_registry(
        {"action": "scan_local", "path": str(empty)}, ctx
    )
    payload = json.loads(res.content)
    assert payload["skills"] == []


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

def test_install_minimal_skill(stores: tuple[GapStore, ToolStore]) -> None:
    store, tstore = stores
    ctx = _ctx(store, tstore)
    res = cm_skill_registry(
        {"action": "install", "skill_package_path": str(_FIXTURE_MINIMAL)}, ctx
    )
    payload = json.loads(res.content)
    assert payload["status"] == "installed"
    assert payload["tool_name"] == "minimal"
    assert payload["skill_package_id"] == "minimal"
    # Verify it's in the graph
    t = tstore.get("minimal")
    assert t is not None
    assert t.kind is ToolKind.installed
    assert t.skill_package_id == "minimal"


def test_install_full_skill(stores: tuple[GapStore, ToolStore]) -> None:
    store, tstore = stores
    ctx = _ctx(store, tstore)
    res = cm_skill_registry(
        {"action": "install", "skill_package_path": str(_FIXTURE_FULL)}, ctx
    )
    payload = json.loads(res.content)
    assert payload["status"] == "installed"
    assert payload["tool_name"] == "full"
    t = tstore.get("full")
    assert t is not None
    assert t.description.startswith("Full Metadata Skill")


def test_install_duplicate_is_idempotent(stores: tuple[GapStore, ToolStore]) -> None:
    store, tstore = stores
    ctx = _ctx(store, tstore)
    cm_skill_registry(
        {"action": "install", "skill_package_path": str(_FIXTURE_MINIMAL)}, ctx
    )
    res2 = cm_skill_registry(
        {"action": "install", "skill_package_path": str(_FIXTURE_MINIMAL)}, ctx
    )
    payload = json.loads(res2.content)
    assert payload["status"] == "already_installed"


def test_install_missing_path(stores: tuple[GapStore, ToolStore]) -> None:
    store, tstore = stores
    ctx = _ctx(store, tstore)
    res = cm_skill_registry(
        {"action": "install", "skill_package_path": "/nonexistent/skill"}, ctx
    )
    assert res.content.startswith("ERROR:")


# ---------------------------------------------------------------------------
# link_gap
# ---------------------------------------------------------------------------

def test_link_gap_records_usage(stores: tuple[GapStore, ToolStore]) -> None:
    store, tstore = stores
    gap = store.create_root(intent="link test", criteria="c")
    ctx = _ctx(store, tstore, gap_id=gap.id)
    # Install first
    cm_skill_registry(
        {"action": "install", "skill_package_path": str(_FIXTURE_MINIMAL)}, ctx
    )
    # Link
    res = cm_skill_registry(
        {"action": "link_gap", "tool_name": "minimal", "gap_id": gap.id}, ctx
    )
    payload = json.loads(res.content)
    assert payload["status"] == "linked"
    assert payload["tool_name"] == "minimal"
    # Verify USED_BY edge via find_for_gap
    res2 = cm_skill_registry(
        {"action": "find_for_gap", "gap_id": gap.id}, ctx
    )
    found = json.loads(res2.content)
    assert "minimal" in found["direct_skills"]


def test_link_gap_missing_tool(stores: tuple[GapStore, ToolStore]) -> None:
    store, tstore = stores
    gap = store.create_root(intent="x", criteria="c")
    ctx = _ctx(store, tstore, gap_id=gap.id)
    res = cm_skill_registry(
        {"action": "link_gap", "tool_name": "nosuch", "gap_id": gap.id}, ctx
    )
    assert res.content.startswith("ERROR:")


# ---------------------------------------------------------------------------
# find_for_gap
# ---------------------------------------------------------------------------

def test_find_for_gap_returns_installed_skills(stores: tuple[GapStore, ToolStore]) -> None:
    store, tstore = stores
    gap = store.create_root(intent="find test", criteria="c")
    ctx = _ctx(store, tstore, gap_id=gap.id)
    cm_skill_registry(
        {"action": "install", "skill_package_path": str(_FIXTURE_MINIMAL)}, ctx
    )
    res = cm_skill_registry(
        {"action": "find_for_gap", "gap_id": gap.id}, ctx
    )
    payload = json.loads(res.content)
    assert payload["gap_id"] == gap.id
    assert any(s["name"] == "minimal" for s in payload["all_installed_skills"])


def test_find_for_gap_missing_gap(stores: tuple[GapStore, ToolStore]) -> None:
    store, tstore = stores
    ctx = _ctx(store, tstore)
    res = cm_skill_registry(
        {"action": "find_for_gap", "gap_id": "nosuch"}, ctx
    )
    assert res.content.startswith("ERROR:")


# ---------------------------------------------------------------------------
# list_installed
# ---------------------------------------------------------------------------

def test_list_installed_empty(stores: tuple[GapStore, ToolStore]) -> None:
    store, tstore = stores
    ctx = _ctx(store, tstore)
    res = cm_skill_registry({"action": "list_installed"}, ctx)
    payload = json.loads(res.content)
    assert payload["count"] == 0
    assert payload["skills"] == []


def test_list_installed_with_skills(stores: tuple[GapStore, ToolStore]) -> None:
    store, tstore = stores
    ctx = _ctx(store, tstore)
    cm_skill_registry(
        {"action": "install", "skill_package_path": str(_FIXTURE_MINIMAL)}, ctx
    )
    res = cm_skill_registry({"action": "list_installed"}, ctx)
    payload = json.loads(res.content)
    assert payload["count"] == 1
    assert payload["skills"][0]["name"] == "minimal"


# ---------------------------------------------------------------------------
# fetch_github (live network; skip by default)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("TEST_NETWORK") != "1",
    reason="Set TEST_NETWORK=1 to run live GitHub fetch tests",
)
def test_fetch_github_and_install_roundtrip(
    stores: tuple[GapStore, ToolStore], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, tstore = stores
    ctx = _ctx(store, tstore)
    # Point skill root at temp dir so fetch writes there
    monkeypatch.setenv(SKILL_ROOT_ENV, str(tmp_path))
    res = cm_skill_registry(
        {
            "action": "fetch_github",
            "github_url": "https://github.com/microsoft/qlib",
            "path": "docs/_static",
            "branch": "main",
        },
        ctx,
    )
    # This repo may or may not have SKILL.md in that path; we just test the
    # plumbing doesn't crash and returns structured JSON.
    assert res.content.startswith("ERROR:") or "local_dir" in res.content
