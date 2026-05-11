"""Cross-platform desktop notifications.

Best-effort. The drone tool calls ``notify(title, body)`` and we run the
appropriate command for the platform. Failures are swallowed — a missing
notification is annoying but not load-bearing for correctness.

  * macOS — ``osascript -e 'display notification ...'`` (built-in).
  * Linux — ``notify-send`` (libnotify; pre-installed on most desktops).
  * Windows — PowerShell. Tries BurntToast first for nicer rendering;
    falls back to a MessageBox if the module isn't installed.

First call on macOS pops the standard permission prompt. We re-issue
prompts after ``tccutil reset Notifications`` and on first run anyway.
"""

from __future__ import annotations

import platform
import shutil
import subprocess


def notify(title: str, body: str, *, timeout_s: float = 5.0) -> bool:
    """Show a desktop notification. Returns True on best-effort success."""
    system = platform.system()
    if system == "Darwin":
        return _notify_macos(title, body, timeout_s)
    if system == "Linux":
        return _notify_linux(title, body, timeout_s)
    if system == "Windows":
        return _notify_windows(title, body, timeout_s)
    return False


def _safe_run(args: list[str], timeout_s: float) -> bool:
    try:
        subprocess.run(args, timeout=timeout_s, check=False, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _escape_applescript(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _notify_macos(title: str, body: str, timeout_s: float) -> bool:
    if not shutil.which("osascript"):
        return False
    script = (
        f'display notification "{_escape_applescript(body)}" '
        f'with title "{_escape_applescript(title)}"'
    )
    return _safe_run(["osascript", "-e", script], timeout_s)


def _notify_linux(title: str, body: str, timeout_s: float) -> bool:
    if shutil.which("notify-send"):
        return _safe_run(["notify-send", "--", title, body], timeout_s)
    return False


def _notify_windows(title: str, body: str, timeout_s: float) -> bool:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return False
    # Prefer BurntToast; fall back to MessageBox.
    safe_title = title.replace('"', "'")
    safe_body = body.replace('"', "'")
    script = (
        f'if (Get-Module -ListAvailable -Name BurntToast) {{ '
        f'New-BurntToastNotification -Text "{safe_title}", "{safe_body}" '
        f'}} else {{ '
        f"Add-Type -AssemblyName PresentationFramework; "
        f'[System.Windows.MessageBox]::Show("{safe_body}", "{safe_title}") '
        f"}}"
    )
    return _safe_run([powershell, "-Command", script], timeout_s)
