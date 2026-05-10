"""terminal_run optional invocation_tool_name emits skill_invocation findings."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from drone_graph.gaps import FindingKind, GapStore
from drone_graph.tools.builtins.worker import terminal_run
from drone_graph.tools.records import Tool, ToolKind, empty_input_schema
from drone_graph.tools.registry import DroneContext
from drone_graph.tools.store import ToolStore


@dataclass
class _FakeRunResult:
    stdout: str
    stderr: str
    exit_code: int


class _FakeTerminal:
    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self._exit_code = exit_code
        self._stdout = stdout
        self._stderr = stderr

    def run(self, cmd: str, *, timeout: float) -> _FakeRunResult:
        return _FakeRunResult(self._stdout, self._stderr, self._exit_code)


class _FakeTerminalBox:
    def __init__(self, terminal: _FakeTerminal) -> None:
        self._terminal = terminal

    def get(self) -> _FakeTerminal:
        return self._terminal

    def respawn(self) -> None:
        pass


@pytest.fixture
def gap_store_tool_store(substrate) -> Iterator[tuple[GapStore, ToolStore]]:
    store = GapStore(substrate)
    tstore = ToolStore(substrate)
    yield store, tstore


def _demo_installed_tool() -> Tool:
    return Tool(
        name="demo_cli",
        description="demo installed tool",
        input_schema_json=json.dumps(empty_input_schema()),
        kind=ToolKind.installed,
        usage="demo_cli --help",
        installed_by_drone_id="test-drone",
    )


def test_terminal_run_skill_invocation_success_records_finding(
    gap_store_tool_store: tuple[GapStore, ToolStore],
) -> None:
    store, tool_store = gap_store_tool_store
    gap = store.create_root(intent="run demo", criteria="ok")
    tool_store.register_installed(_demo_installed_tool())
    term = _FakeTerminal(exit_code=0, stdout="ok\n")
    ctx = DroneContext(
        gap_id=gap.id,
        drone_id="drone-1",
        tick=3,
        store=store,
        tool_store=tool_store,
        terminal_box=_FakeTerminalBox(term),
        tape=None,
        signals=None,
        active_tool_names={"terminal_run"},
    )
    r = terminal_run(
        {"cmd": "echo hi", "invocation_tool_name": "demo_cli"},
        ctx,
    )
    assert r.extra_findings_written == 1
    payload = json.loads(r.content)
    assert payload["exit_code"] == 0
    skill = [
        f
        for f in store.all_findings()
        if f.kind is FindingKind.skill_invocation
    ]
    assert len(skill) == 1
    assert skill[0].invocation_tool_name == "demo_cli"
    assert skill[0].invocation_outcome == "success"
    assert skill[0].affected_gap_ids == [gap.id]
    metrics = json.loads(skill[0].invocation_metrics_json or "{}")
    assert metrics["exit_code"] == 0
    assert "duration_ms" in metrics


def test_terminal_run_unknown_tool_no_skill_finding(
    gap_store_tool_store: tuple[GapStore, ToolStore],
) -> None:
    store, tool_store = gap_store_tool_store
    gap = store.create_root(intent="x", criteria="y")
    term = _FakeTerminal()
    ctx = DroneContext(
        gap_id=gap.id,
        drone_id="d",
        tick=1,
        store=store,
        tool_store=tool_store,
        terminal_box=_FakeTerminalBox(term),
        active_tool_names={"terminal_run"},
    )
    r = terminal_run(
        {"cmd": "echo hi", "invocation_tool_name": "missing_tool"},
        ctx,
    )
    assert "ERROR" in r.content
    assert r.extra_findings_written == 0
    assert not any(
        f.kind is FindingKind.skill_invocation for f in store.all_findings()
    )


def test_terminal_run_without_invocation_tool_name_no_skill_finding(
    gap_store_tool_store: tuple[GapStore, ToolStore],
) -> None:
    store, tool_store = gap_store_tool_store
    gap = store.create_root(intent="x", criteria="y")
    tool_store.register_installed(_demo_installed_tool())
    term = _FakeTerminal()
    ctx = DroneContext(
        gap_id=gap.id,
        drone_id="d",
        tick=1,
        store=store,
        tool_store=tool_store,
        terminal_box=_FakeTerminalBox(term),
        active_tool_names={"terminal_run"},
    )
    r = terminal_run({"cmd": "echo hi"}, ctx)
    assert r.extra_findings_written == 0
    assert not any(
        f.kind is FindingKind.skill_invocation for f in store.all_findings()
    )
