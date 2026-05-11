"""Two drone runs share on-disk workspace .venv (needs_venv + DRONE_GRAPH_WORKSPACE)."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from drone_graph.drones.providers import ChatResponse, Provider, ToolCall, Usage
from drone_graph.drones.runtime import run_drone
from drone_graph.gaps import GapStore
from drone_graph.terminal import is_terminal_supported
from drone_graph.tools import Tool, ToolKind, empty_input_schema
from drone_graph.tools.store import ToolStore

PROBE_NAME = "step10_pkg_probe"


def _site_packages(workspace: Path) -> Path:
    venv = workspace / ".venv"
    if sys.platform == "win32":
        sp = venv / "Lib" / "site-packages"
        if sp.is_dir():
            return sp
    else:
        for p in sorted((venv / "lib").glob("python*/site-packages")):
            if p.is_dir():
                return p
    raise RuntimeError(f"site-packages not found under {venv}")


class _WriteThenFillClient:
    provider = Provider.anthropic
    model = "claude-test"

    def __init__(self, terminal_cmd: str) -> None:
        self.turn = 0
        self.terminal_cmd = terminal_cmd

    def chat(
        self,
        system: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        max_tokens: int | None = None,
    ) -> ChatResponse:
        self.turn += 1
        if self.turn == 1:
            raw = [
                {
                    "type": "tool_use",
                    "id": "tu_term",
                    "name": "terminal_run",
                    "input": {
                        "cmd": self.terminal_cmd,
                        "invocation_tool_name": PROBE_NAME,
                    },
                }
            ]
            return ChatResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="tu_term",
                        name="terminal_run",
                        input={
                            "cmd": self.terminal_cmd,
                            "invocation_tool_name": PROBE_NAME,
                        },
                    )
                ],
                raw_assistant_content=raw,
                usage=Usage(tokens_in=1, tokens_out=2),
            )
        raw = [
            {
                "type": "tool_use",
                "id": "tu_fill",
                "name": "cm_write_finding",
                "input": {"kind": "fill", "summary": "done"},
            }
        ]
        return ChatResponse(
            text="",
            tool_calls=[
                ToolCall(
                    id="tu_fill",
                    name="cm_write_finding",
                    input={"kind": "fill", "summary": "done"},
                )
            ],
            raw_assistant_content=raw,
            usage=Usage(tokens_in=1, tokens_out=2),
        )


@pytest.fixture
def workspace_with_venv(tmp_path: Path) -> Path:
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(tmp_path / ".venv")],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        pytest.skip(f"could not create venv: {e}")
    return tmp_path


def test_two_drone_runs_share_venv_site_packages(
    substrate,
    workspace_with_venv: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not is_terminal_supported():
        pytest.skip("bash/GNU environment terminal not available")

    monkeypatch.setenv("DRONE_GRAPH_WORKSPACE", str(workspace_with_venv.resolve()))

    gap_store = GapStore(substrate)
    tool_store = ToolStore(substrate)
    tool_store.register_installed(
        Tool(
            name=PROBE_NAME,
            description="step10 probe",
            input_schema_json=json.dumps(empty_input_schema()),
            kind=ToolKind.installed,
            usage="noop",
            needs_venv=True,
            installed_by_drone_id="fixture",
        )
    )

    site_pkg = _site_packages(workspace_with_venv)
    sentinel = site_pkg / "sentinel_step10.txt"
    inner_write = (
        f"from pathlib import Path; Path({str(sentinel)!r}).write_text('ok')"
    )
    cmd_write = "python -c " + shlex.quote(inner_write)
    inner_check = (
        f"from pathlib import Path; "
        f"assert Path({str(sentinel)!r}).read_text() == 'ok'"
    )
    cmd_check = "python -c " + shlex.quote(inner_check)

    gap_a = gap_store.create_root(intent="write sentinel", criteria="ok")
    r_a = run_drone(
        gap_a,
        store=gap_store,
        tool_store=tool_store,
        client=_WriteThenFillClient(cmd_write),
        tick=1,
        max_turns=5,
    )
    assert r_a.outcome == "fill"
    assert sentinel.is_file()
    assert sentinel.read_text(encoding="utf-8") == "ok"
    assert (workspace_with_venv / ".venv").is_dir()

    gap_b = gap_store.create_root(intent="verify sentinel", criteria="ok")
    r_b = run_drone(
        gap_b,
        store=gap_store,
        tool_store=tool_store,
        client=_WriteThenFillClient(cmd_check),
        tick=2,
        max_turns=5,
    )
    assert r_b.outcome == "fill"
    assert (workspace_with_venv / ".venv").is_dir()
