"""Direct tests for the Phase 3 concurrency builtins.

These exercise the tool dispatchers against a real ``SQLiteSignalStore`` and
a fake terminal — no Neo4j or model API calls required.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from drone_graph.signals import SQLiteSignalStore
from drone_graph.tools.builtins.concurrency import (
    cm_acquire_file,
    cm_install_package,
    cm_release_file,
)
from drone_graph.tools.registry import DroneContext

# ---- Fakes ----------------------------------------------------------------


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
        self.commands: list[str] = []

    def run(self, cmd: str, *, timeout: float) -> _FakeRunResult:
        self.commands.append(cmd)
        return _FakeRunResult(self._stdout, self._stderr, self._exit_code)


class _FakeTerminalBox:
    def __init__(self, terminal: _FakeTerminal) -> None:
        self._terminal = terminal

    def get(self) -> _FakeTerminal:
        return self._terminal

    def respawn(self) -> None:
        pass


def _ctx(
    store: SQLiteSignalStore,
    drone_id: str,
    terminal: _FakeTerminal | None = None,
) -> DroneContext:
    return DroneContext(
        gap_id="g1",
        drone_id=drone_id,
        tick=0,
        store=None,
        tool_store=None,
        terminal_box=_FakeTerminalBox(terminal) if terminal else None,
        tape=None,
        signals=store,
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteSignalStore]:
    s = SQLiteSignalStore(tmp_path / "signals.db")
    try:
        yield s
    finally:
        s.close()


# ---- File acquire / release ----------------------------------------------


def test_acquire_file_succeeds(store: SQLiteSignalStore) -> None:
    r = cm_acquire_file({"path": "/tmp/x", "timeout_s": 0.1}, _ctx(store, "drone-a"))
    payload = json.loads(r.content)
    assert payload == {"path": "/tmp/x", "mode": "write", "acquired": True}
    held = store.get_claim("file", "/tmp/x")
    assert held is not None and held.drone_id == "drone-a"


def test_acquire_file_blocks_second_drone(store: SQLiteSignalStore) -> None:
    cm_acquire_file({"path": "/tmp/x", "timeout_s": 0.1}, _ctx(store, "drone-a"))
    r = cm_acquire_file({"path": "/tmp/x", "timeout_s": 0.1}, _ctx(store, "drone-b"))
    payload = json.loads(r.content)
    assert payload["acquired"] is False
    assert payload["reason"] == "timeout"
    assert payload["current_writer"] == "drone-a"


def test_release_file_unblocks(store: SQLiteSignalStore) -> None:
    cm_acquire_file({"path": "/tmp/x", "timeout_s": 0.1}, _ctx(store, "drone-a"))
    cm_release_file({"path": "/tmp/x"}, _ctx(store, "drone-a"))
    r = cm_acquire_file({"path": "/tmp/x", "timeout_s": 0.1}, _ctx(store, "drone-b"))
    assert json.loads(r.content)["acquired"] is True


def test_acquire_file_read_mode_never_blocks(store: SQLiteSignalStore) -> None:
    cm_acquire_file({"path": "/tmp/x", "timeout_s": 0.1}, _ctx(store, "drone-a"))
    r = cm_acquire_file(
        {"path": "/tmp/x", "mode": "read", "timeout_s": 0.0},
        _ctx(store, "drone-b"),
    )
    payload = json.loads(r.content)
    assert payload["mode"] == "read"
    assert payload["current_writer"] == "drone-a"


def test_acquire_file_no_signals_returns_error() -> None:
    ctx = DroneContext(
        gap_id="g1", drone_id="drone-a", tick=0,
        store=None, tool_store=None, signals=None,
    )
    r = cm_acquire_file({"path": "/tmp/x"}, ctx)
    assert "no SignalStore" in r.content


# ---- Install ---------------------------------------------------------------


def test_install_package_first_caller_runs_and_registers(
    store: SQLiteSignalStore,
) -> None:
    term = _FakeTerminal(exit_code=0)
    r = cm_install_package(
        {
            "install_key": "playwright",
            "install_commands": ["pip install playwright", "playwright install"],
            "usage": "from playwright.sync_api import sync_playwright",
        },
        _ctx(store, "drone-a", term),
    )
    payload = json.loads(r.content)
    assert payload["ok"] is True
    assert payload["installed_by"] == "drone-a"
    assert term.commands == ["pip install playwright", "playwright install"]
    rec = store.install_lookup("playwright")
    assert rec is not None
    assert rec.installed_by == "drone-a"


def test_install_package_second_caller_skips_install(
    store: SQLiteSignalStore,
) -> None:
    store.install_register(
        "pandas",
        "drone-a",
        ["pip install pandas"],
        usage="import pandas as pd",
    )
    term = _FakeTerminal(exit_code=0)
    r = cm_install_package(
        {"install_key": "pandas", "install_commands": ["pip install pandas"]},
        _ctx(store, "drone-b", term),
    )
    payload = json.loads(r.content)
    assert payload["already_installed"] is True
    assert payload["installed_by"] == "drone-a"
    assert payload["usage"] == "import pandas as pd"
    assert term.commands == []


def test_install_package_failed_command_releases_lock(
    store: SQLiteSignalStore,
) -> None:
    term = _FakeTerminal(exit_code=1, stderr="package not found")
    r = cm_install_package(
        {
            "install_key": "missing-pkg",
            "install_commands": ["pip install missing-pkg"],
        },
        _ctx(store, "drone-a", term),
    )
    payload = json.loads(r.content)
    assert payload["ok"] is False
    assert payload["failed_step"] == 1
    assert payload["exit_code"] == 1
    assert store.get_claim("install", "missing-pkg") is None
    assert store.install_lookup("missing-pkg") is None


def test_install_package_concurrent_second_caller_sees_lock(
    store: SQLiteSignalStore,
) -> None:
    store.try_acquire("install", "tf", "drone-a", ttl_s=600)
    term = _FakeTerminal(exit_code=0)
    r = cm_install_package(
        {"install_key": "tf", "install_commands": ["pip install tf"]},
        _ctx(store, "drone-b", term),
    )
    payload = json.loads(r.content)
    assert payload["acquired"] is False
    assert "another drone is installing" in payload["reason"]
    assert term.commands == []


def test_release_all_for_drone_cleans_file_claims(store: SQLiteSignalStore) -> None:
    cm_acquire_file({"path": "/tmp/a", "timeout_s": 0.1}, _ctx(store, "drone-a"))
    cm_acquire_file({"path": "/tmp/b", "timeout_s": 0.1}, _ctx(store, "drone-a"))
    n = store.release_all_for_drone("drone-a")
    assert n == 2
    assert store.get_claim("file", "/tmp/a") is None
    assert store.get_claim("file", "/tmp/b") is None
