from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int


class Terminal:
    def __init__(self) -> None:
        self._proc = subprocess.Popen(
            ["bash"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0,
        )

    def run(self, cmd: str, timeout: float = 60.0) -> CommandResult:
        raise NotImplementedError(
            "implement marker-based streaming: write cmd + sentinel, read until sentinel, "
            "capture stdout/stderr/exit separately"
        )

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
