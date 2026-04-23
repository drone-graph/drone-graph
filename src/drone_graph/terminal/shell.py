from __future__ import annotations

import os
import selectors
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Self


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


def _git_bash_candidates() -> list[Path]:
    # Prefer canonical Windows env keys; fall back for unusual shells / env dumps.
    roots = (
        os.environ.get("PROGRAMFILES")
        or os.environ.get("ProgramFiles"),  # noqa: SIM112
        os.environ.get("PROGRAMFILES(X86)")
        or os.environ.get("ProgramFiles(x86)"),  # noqa: SIM112
    )
    out: list[Path] = []
    for root in roots:
        if not root:
            continue
        p = Path(root) / "Git" / "bin" / "bash.exe"
        out.append(p)
    return out


def resolve_bash_executable() -> str:
    """Return a path to bash.

    On Windows, Git for Windows is preferred before ``shutil.which("bash")``, which may
    resolve to the WSL launcher (``System32\\bash.exe``) — that binary is a poor fit for
    piped subprocess sessions used here.
    """
    if sys.platform == "win32":
        for p in _git_bash_candidates():
            if p.is_file():
                return str(p)
    found = shutil.which("bash")
    if found:
        return found
    msg = (
        "bash is required for the drone terminal (persistent shell). "
        "On Windows, install Git for Windows or add bash to PATH."
    )
    raise FileNotFoundError(msg)


def is_terminal_supported() -> bool:
    """True if a bash executable is available for :class:`Terminal`."""
    try:
        resolve_bash_executable()
    except FileNotFoundError:
        return False
    else:
        return True


def _stderr_path_for_bash(path: Path) -> str:
    """Path string safe for bash ``2>>`` on Git Bash / MSYS (``/c/Users/...`` on Windows)."""
    resolved = path.resolve()
    if sys.platform != "win32":
        return str(resolved)
    s = str(resolved)
    if len(s) >= 2 and s[1] == ":":
        drive = s[0].lower()
        rest = s[2:].replace("\\", "/")
        return f"/{drive}{rest}"
    return s.replace("\\", "/")


class _BashTerminalBase(ABC):
    """Shared persistent bash session (marker framing, stderr capture)."""

    def __init__(self, cwd: str | None = None, env: dict[str, str] | None = None) -> None:
        stderr_f = NamedTemporaryFile(prefix="dg-stderr-", suffix=".log", delete=False)
        self._stderr_path = Path(stderr_f.name)
        stderr_f.close()
        self._stderr_pos = 0

        shell_env = dict(os.environ if env is None else env)
        shell_env["PS1"] = ""
        shell_env["PS2"] = ""

        bash = resolve_bash_executable()
        self._proc = subprocess.Popen(
            [bash, "--noprofile", "--norc"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=shell_env,
            bufsize=0,
        )
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        self._spawn_stdout_consumer()

    @abstractmethod
    def _spawn_stdout_consumer(self) -> None:
        """Start reading child stdout immediately after Popen (avoid Windows pipe backpressure)."""

    def _preamble(self) -> None:
        self._raw_send("unset HISTFILE\n")

    def _raw_send(self, s: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(s.encode("utf-8"))
        self._proc.stdin.flush()

    def _stderr_redirect_fragment(self) -> str:
        return shlex.quote(_stderr_path_for_bash(self._stderr_path))

    def _read_new_stderr(self) -> str:
        with self._stderr_path.open("rb") as f:
            f.seek(self._stderr_pos)
            data = f.read()
            self._stderr_pos = f.tell()
        return data.decode("utf-8", errors="replace")

    def _kill(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)

    @abstractmethod
    def run(self, cmd: str, timeout: float = 60.0) -> CommandResult:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class PosixBashTerminal(_BashTerminalBase):
    """Unix/macOS: read child stdout via selectors (efficient on POSIX pipes)."""

    def __init__(self, cwd: str | None = None, env: dict[str, str] | None = None) -> None:
        super().__init__(cwd=cwd, env=env)
        self._preamble()

    def _spawn_stdout_consumer(self) -> None:
        out = self._proc.stdout
        assert out is not None
        self._selector = selectors.DefaultSelector()
        self._selector.register(out, selectors.EVENT_READ)

    def run(self, cmd: str, timeout: float = 60.0) -> CommandResult:
        if self._proc.poll() is not None:
            raise TerminalDead("terminal process has exited")

        marker = f"{_MARKER_PREFIX}{uuid.uuid4().hex}"
        stderr_q = self._stderr_redirect_fragment()
        wrapped = (
            f"{{\n{cmd}\n}} 2>> {stderr_q}\n"
            f"__dg_ec=$?; printf '\\n{marker} %d\\n' \"$__dg_ec\"\n"
        )
        self._raw_send(wrapped)

        stdout_buf = bytearray()
        marker_bytes = marker.encode("ascii")
        deadline = time.monotonic() + timeout
        assert self._proc.stdout is not None
        pre: bytes
        post: bytes

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
            maybe_pre, maybe_post = _split_at_marker(stdout_buf, marker_bytes)
            if maybe_pre is not None and maybe_post is not None:
                pre = maybe_pre
                post = maybe_post
                break

        stdout = pre.decode("utf-8", errors="replace").rstrip("\r\n")
        exit_code = int(post.strip())
        stderr = self._read_new_stderr()
        return CommandResult(stdout=stdout, stderr=stderr, exit_code=exit_code)

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


class WindowsBashTerminal(_BashTerminalBase):
    """Windows: same bash session as POSIX, but pipe reads run in a background thread.

    ``select()``/``DefaultSelector`` on anonymous pipes is unreliable on Windows;
    a blocking reader thread feeds a shared buffer instead. The reader starts from
    :meth:`_spawn_stdout_consumer` immediately after ``Popen`` to avoid stdout pipe
    backpressure while bash prints its startup banner.
    """

    def __init__(self, cwd: str | None = None, env: dict[str, str] | None = None) -> None:
        self._stdout_lock = threading.Lock()
        self._stdout_buf = bytearray()
        self._reader_error: BaseException | None = None
        self._reader_stop = threading.Event()
        super().__init__(cwd=cwd, env=env)
        self._preamble()

    def _spawn_stdout_consumer(self) -> None:
        self._reader_thread = threading.Thread(target=self._stdout_reader_loop, daemon=True)
        self._reader_thread.start()

    def _stdout_reader_loop(self) -> None:
        assert self._proc.stdout is not None
        try:
            while not self._reader_stop.is_set():
                chunk = self._proc.stdout.read(4096)
                if not chunk:
                    break
                with self._stdout_lock:
                    self._stdout_buf.extend(chunk)
        except BaseException as e:
            self._reader_error = e

    def run(self, cmd: str, timeout: float = 60.0) -> CommandResult:
        if self._reader_error is not None:
            err = self._reader_error
            raise TerminalDead(f"stdout reader failed: {err!r}") from err
        if self._proc.poll() is not None:
            raise TerminalDead("terminal process has exited")

        marker = f"{_MARKER_PREFIX}{uuid.uuid4().hex}"
        stderr_q = self._stderr_redirect_fragment()
        wrapped = (
            f"{{\n{cmd}\n}} 2>> {stderr_q}\n"
            f"__dg_ec=$?; printf '\\n{marker} %d\\n' \"$__dg_ec\"\n"
        )
        self._raw_send(wrapped)

        marker_bytes = marker.encode("ascii")
        deadline = time.monotonic() + timeout
        pre: bytes
        post: bytes

        while True:
            if self._reader_error is not None:
                err = self._reader_error
                raise TerminalDead(f"stdout reader failed: {err!r}") from err
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                raise TerminalTimeout(f"command timed out after {timeout:.1f}s")
            with self._stdout_lock:
                idx = self._stdout_buf.find(marker_bytes)
                if idx != -1:
                    nl = self._stdout_buf.find(b"\n", idx + len(marker_bytes))
                    if nl != -1:
                        pre = bytes(self._stdout_buf[:idx])
                        post = bytes(self._stdout_buf[idx + len(marker_bytes) : nl])
                        del self._stdout_buf[: nl + 1]
                        break
            time.sleep(min(0.05, max(remaining, 0.01)))

        stdout = pre.decode("utf-8", errors="replace").rstrip("\r\n")
        exit_code = int(post.strip())
        stderr = self._read_new_stderr()
        return CommandResult(stdout=stdout, stderr=stderr, exit_code=exit_code)

    def close(self) -> None:
        self._reader_stop.set()
        self._kill()
        if self._reader_thread.is_alive():
            self._reader_thread.join(timeout=3.0)
        try:
            self._stderr_path.unlink()
        except FileNotFoundError:
            pass


def _split_at_marker(
    stdout_buf: bytearray, marker_bytes: bytes
) -> tuple[bytes, bytes] | tuple[None, None]:
    """If marker and exit-code line are present, return (pre, post) else (None, None)."""
    idx = stdout_buf.find(marker_bytes)
    if idx == -1:
        return None, None
    nl = stdout_buf.find(b"\n", idx + len(marker_bytes))
    if nl == -1:
        return None, None
    pre = bytes(stdout_buf[:idx])
    post = bytes(stdout_buf[idx + len(marker_bytes) : nl])
    return pre, post


if sys.platform == "win32":
    Terminal = WindowsBashTerminal
else:
    Terminal = PosixBashTerminal
