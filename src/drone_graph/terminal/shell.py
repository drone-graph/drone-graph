from __future__ import annotations

import os
import selectors
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


class TerminalTimeout(RuntimeError):
    pass


class TerminalDead(RuntimeError):
    pass


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int


_MARKER_PREFIX = "__DG_END__"


class Terminal:
    """Per-drone persistent bash.

    State (cwd, env, functions) persists across `run()` calls because every
    command executes in the same shell process. Each call emits a unique
    sentinel so stdout framing is unambiguous; stderr is captured to a per-
    terminal append file and the new tail is read after each command.

    Not thread-safe. One caller at a time per terminal.
    """

    def __init__(self, cwd: str | None = None, env: dict[str, str] | None = None) -> None:
        stderr_f = NamedTemporaryFile(prefix="dg-stderr-", suffix=".log", delete=False)
        self._stderr_path = Path(stderr_f.name)
        stderr_f.close()
        self._stderr_pos = 0

        shell_env = dict(os.environ if env is None else env)
        shell_env["PS1"] = ""
        shell_env["PS2"] = ""

        self._proc = subprocess.Popen(
            ["bash", "--noprofile", "--norc"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=shell_env,
            bufsize=0,
        )
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None

        self._selector = selectors.DefaultSelector()
        self._selector.register(self._proc.stdout, selectors.EVENT_READ)

        self._preamble()

    def _preamble(self) -> None:
        # Harden the shell: exit on unhandled pipe failure is *not* set,
        # because drones benefit from seeing non-zero exit codes rather than
        # the shell dying. Disable history so temp files don't accumulate.
        self._raw_send("unset HISTFILE\n")

    def _raw_send(self, s: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(s.encode("utf-8"))
        self._proc.stdin.flush()

    def run(self, cmd: str, timeout: float = 60.0) -> CommandResult:
        if self._proc.poll() is not None:
            raise TerminalDead("terminal process has exited")

        marker = f"{_MARKER_PREFIX}{uuid.uuid4().hex}"
        stderr_q = shlex.quote(str(self._stderr_path))
        # Brace group preserves cd/export state in the parent shell. Each command
        # appends its stderr to our capture file. We print the marker on its own
        # line with the exit code so the parser can split cleanly.
        wrapped = (
            f"{{\n{cmd}\n}} 2>> {stderr_q}\n"
            f"__dg_ec=$?; printf '\\n{marker} %d\\n' \"$__dg_ec\"\n"
        )
        self._raw_send(wrapped)

        stdout_buf = bytearray()
        marker_bytes = marker.encode("ascii")
        deadline = time.monotonic() + timeout
        assert self._proc.stdout is not None

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                raise TerminalTimeout(f"command timed out after {timeout:.1f}s")
            events = self._selector.select(timeout=min(remaining, 0.5))
            if not events:
                continue
            chunk = os.read(self._proc.stdout.fileno(), 4096)
            if not chunk:
                raise TerminalDead("terminal exited unexpectedly")
            stdout_buf.extend(chunk)
            idx = stdout_buf.find(marker_bytes)
            if idx == -1:
                continue
            # Need the exit code line after the marker. Wait until we have a newline.
            nl = stdout_buf.find(b"\n", idx + len(marker_bytes))
            if nl == -1:
                continue
            pre = bytes(stdout_buf[:idx])
            post = bytes(stdout_buf[idx + len(marker_bytes) : nl])
            break

        stdout = pre.decode("utf-8", errors="replace").rstrip("\n")
        exit_code = int(post.strip())
        stderr = self._read_new_stderr()
        return CommandResult(stdout=stdout, stderr=stderr, exit_code=exit_code)

    def _read_new_stderr(self) -> str:
        with self._stderr_path.open("rb") as f:
            f.seek(self._stderr_pos)
            data = f.read()
            self._stderr_pos = f.tell()
        return data.decode("utf-8", errors="replace")

    def close(self) -> None:
        self._kill()
        try:
            self._selector.close()
        except Exception:
            pass
        try:
            self._stderr_path.unlink()
        except FileNotFoundError:
            pass

    def _kill(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)

    def __enter__(self) -> Terminal:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
