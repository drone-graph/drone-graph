from __future__ import annotations

import pytest

from drone_graph.terminal import Terminal, TerminalTimeout, is_terminal_supported

pytestmark = pytest.mark.skipif(
    not is_terminal_supported(),
    reason="bash not available (install Git for Windows or add bash to PATH)",
)


def test_basic_stdout_exit_code() -> None:
    with Terminal() as t:
        r = t.run("echo hello; (exit 7)")
        assert r.stdout == "hello"
        assert r.exit_code == 7


def test_stderr_captured_separately() -> None:
    with Terminal() as t:
        r = t.run("echo out; echo err 1>&2")
        assert r.stdout == "out"
        assert r.stderr.strip() == "err"
        assert r.exit_code == 0


def test_cwd_persists_across_calls() -> None:
    with Terminal() as t:
        t.run("cd /tmp")
        r = t.run("pwd")
        assert r.stdout == "/tmp"


def test_env_persists_across_calls() -> None:
    with Terminal() as t:
        t.run("export DRONE_GRAPH_TEST=42")
        r = t.run("echo $DRONE_GRAPH_TEST")
        assert r.stdout == "42"


def test_multiline_output() -> None:
    with Terminal() as t:
        r = t.run("seq 1 3")
        assert r.stdout == "1\n2\n3"


def test_timeout_raises_and_kills() -> None:
    t = Terminal()
    try:
        with pytest.raises(TerminalTimeout):
            t.run("sleep 5", timeout=0.3)
    finally:
        t.close()
