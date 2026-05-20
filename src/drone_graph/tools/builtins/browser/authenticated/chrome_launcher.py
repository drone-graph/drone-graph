"""Manage a long-running system Chrome process with CDP for the drone-graph
browser lane.

Key anti-detection strategy
---------------------------
Chrome is launched via ``subprocess.Popen`` — **not** via Playwright — so the
process never receives Playwright's ``--enable-automation`` flag. Playwright
then attaches to the already-running instance via ``connect_over_cdp``, which
does not set ``navigator.webdriver``. Google's detection sees a normal Chrome
browser being used by a real person.

Architecture note
-----------------
Chrome should be launched **once** by the scheduler (parent) process so that
all drone subprocesses share the same Chrome instance on the same CDP port.
Subprocesses detect the already-running CDP endpoint and connect without
attempting to launch a second Chrome.

Profile management
------------------
The Chrome profile lives permanently at ``<project-root>/chrome-data/``.
Chrome reads and writes to this directory directly — **no copying** on every
run.  Google's anti-abuse detection flags session cloning, so once the profile
is placed in ``chrome-data/`` it is never copied again.  Chrome manages its
own session state (cookies, Local Storage, etc.) inside this directory across
runs, exactly as it does for a real user.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import sys
from pathlib import Path
from typing import Any

from drone_graph.tools.builtins.browser.authenticated.config import (
    AuthenticatedConfig,
    load_config,
)

# How long (seconds) to wait for the CDP endpoint to become ready after launch.
_CDP_READY_TIMEOUT_S = 30.0
_CDP_POLL_S = 0.3

# File that records the managed Chrome PID so subprocesses and subsequent
# scheduler runs can find / clean up the shared instance.
_MANAGED_PID_FILENAME = ".chrome-managed-pid"

# Project root — derived from this file's location in the source tree.
# chrome_launcher.py → authenticated/ → browser/ → builtins/ → tools/ → drone_graph/ → src/ → (project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent.parent


def _managed_pid_path() -> Path:
    return _PROJECT_ROOT / "chrome-data" / _MANAGED_PID_FILENAME


def _probe_cdp(port: int) -> bool:
    """Return ``True`` if the CDP ``/json/version`` endpoint responds on
    ``127.0.0.1:port``."""
    import urllib.request
    import urllib.error

    try:
        url = f"http://127.0.0.1:{port}/json/version"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _write_pid_file(pid: int) -> None:
    p = _managed_pid_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"pid": pid}))
    except Exception:
        pass


def _read_pid_file() -> int | None:
    p = _managed_pid_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return int(data["pid"])
    except Exception:
        return None


def _remove_pid_file() -> None:
    try:
        _managed_pid_path().unlink(missing_ok=True)
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    """Check if *pid* names a running process.

    On Windows uses ``OpenProcess`` with the ``SYNCHRONIZE`` flag.
    On POSIX uses ``os.kill(pid, 0)`` which tests process existence without
    sending a signal.
    """
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True

    # POSIX: signal 0 tests process existence without sending a signal.
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_pid(pid: int) -> None:
    """Force-kill a process by PID on Windows."""
    import subprocess as _sp

    try:
        _sp.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
    except Exception:
        pass


class AuthenticatedChrome:
    """Singleton: one Chrome process with the dedicated drone-graph profile.

    Class-level state so the same Chrome instance is shared across all drone
    tool calls **in the same process**.  When the scheduler launches Chrome
    via :meth:`start_managed`, subprocesses detect it via the PID file and
    CDP probe and connect without launching a new instance.
    """

    _process: subprocess.Popen | None = None
    _browser: Any | None = None  # playwright.sync_api.Browser — typed Any to
    # avoid forcing a playwright import at module
    # load time.
    _playwright: Any | None = None  # playwright.sync_api.Playwright context

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def ensure_running(
        cls,
        config: AuthenticatedConfig | None = None,
        profile_dir: str | Path | None = None,
    ) -> Any:
        """Return the Playwright ``Browser`` connected via CDP.

        1. If this process already has a live browser object, return it.
        2. Otherwise, probe the CDP port — if something is already listening
           (launched by the scheduler or another subprocess), connect without
           launching a new Chrome.
        3. Only if CDP is unreachable, launch Chrome via ``subprocess.Popen``.

        Parameters
        ----------
        config : AuthenticatedConfig | None
            Browser configuration. If ``None``, loaded from disk.
        profile_dir : str | Path | None
            Chrome user data directory. Resolved from
            ``settings.chrome_profile_dir`` if not provided.

        Returns
        -------
        playwright.sync_api.Browser
        """
        if config is None:
            config = load_config()
        if profile_dir is None:
            from drone_graph.api.settings import (  # lazy: break circular import
                load_settings,
            )

            settings = load_settings()
            if not settings.chrome_profile_dir:
                raise RuntimeError(
                    "No Chrome profile configured. "
                    "Use cm_check_browser for status."
                )
            profile_dir = Path(settings.chrome_profile_dir)

        profile_dir = Path(profile_dir)

        # If we already have a live browser, return it.
        if cls._browser is not None:
            try:
                # Quick liveness check: list contexts.
                _ = cls._browser.contexts
                return cls._browser
            except Exception:
                # Browser went away — clean up and reconnect.
                cls._cleanup()

        # If a managed (parent-process) Chrome is already running on the CDP
        # port, just connect — do NOT launch another Chrome.
        if cls._process is None and _probe_cdp(config.cdp_port):
            print(
                f"[authenticated] CDP already reachable on port {config.cdp_port}; "
                "connecting without launching",
                file=sys.stderr,
                flush=True,
            )
            cls._connect_cdp(config)
            return cls._browser

        # If a process exists but CDP is dead, reap it.
        if cls._process is not None:
            if not cls._is_process_alive():
                cls._process = None

        # Launch if nothing is running.
        if cls._process is None:
            cls._launch_chrome(config, profile_dir)

        # Connect Playwright via CDP.
        cls._connect_cdp(config)
        return cls._browser

    @classmethod
    def connect(cls, config: AuthenticatedConfig | None = None) -> Any:
        """Connect Playwright to an **already-running** Chrome via CDP.

        Raises ``RuntimeError`` if Chrome is not running or CDP is
        unreachable.

        Parameters
        ----------
        config : AuthenticatedConfig | None
            Lane configuration. If ``None``, loaded from disk.
        """
        if config is None:
            config = load_config()
        if cls._browser is not None:
            try:
                _ = cls._browser.contexts
                return cls._browser
            except Exception:
                cls._cleanup()
        cls._connect_cdp(config)
        return cls._browser

    @classmethod
    def stop(cls) -> None:
        """Kill the Chrome process and clean up Playwright resources."""
        cls._cleanup()
        _remove_pid_file()

    @classmethod
    def is_running(cls) -> bool:
        """Check if the Chrome process is still alive."""
        if cls._process is not None and cls._is_process_alive():
            return True
        # Fall back to PID-file check in case Chrome was launched by the
        # scheduler (parent process) and subprocess has no ``_process`` set.
        pid = _read_pid_file()
        if pid is not None:
            return _pid_alive(pid)
        return False

    @classmethod
    def start_managed(cls, config: AuthenticatedConfig, profile_dir: Path) -> bool:
        """Launch Chrome in the **parent** (scheduler) process so all drone
        subprocesses share the same instance.

        If Chrome is already running (detected via PID file + CDP probe),
        returns ``True`` without launching a new instance.

        Returns
        -------
        bool
            ``True`` if Chrome is confirmed running after this call.
        """
        # 1. Check the PID file — if the recorded process is still alive and
        #    CDP responds, we're good.
        pid = _read_pid_file()
        if pid is not None and _pid_alive(pid) and _probe_cdp(config.cdp_port):
            print(
                f"[authenticated] Managed Chrome already running (pid={pid})",
                file=sys.stderr,
                flush=True,
            )
            return True

        # 2. Stale PID — clean it up.
        if pid is not None:
            _kill_pid(pid)
            _remove_pid_file()

        # 3. If this process already has a running Chrome, we're done.
        if cls._process is not None and cls._is_process_alive():
            _write_pid_file(cls._process.pid)  # ensure PID file exists
            return True

        # 4. Launch Chrome.
        cls._launch_chrome(config, profile_dir)
        if cls._process is not None:
            _write_pid_file(cls._process.pid)

        print(
            f"[authenticated] Managed Chrome started (pid={cls._process.pid if cls._process else '?'})",
            file=sys.stderr,
            flush=True,
        )
        return True

    @classmethod
    def stop_managed(cls) -> None:
        """Kill the managed Chrome process and clean up."""
        pid = _read_pid_file()
        if pid is not None:
            print(
                f"[authenticated] Stopping managed Chrome (pid={pid})",
                file=sys.stderr,
                flush=True,
            )
            _kill_pid(pid)
            _remove_pid_file()
        cls._cleanup()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _launch_chrome(
        cls,
        config: AuthenticatedConfig,
        profile_dir: Path,
    ) -> None:
        """Start Chrome as a subprocess with CDP enabled.

        ``profile_dir`` should be the path to a Chrome profile directory
        (e.g. ``...\\User Data\\Default`` or ``...\\User Data\\Profile 2``).

        Chrome is launched with:
        * ``--user-data-dir=<parent>`` — the ``User Data`` directory
        * ``--profile-directory=<name>`` — the specific profile (e.g. ``Default``, ``Profile 2``)

        This ensures the selected profile's cookies, sessions, and extensions
        are loaded rather than Chrome creating a fresh ``Default`` profile.
        """
        chrome_path = config.chrome_path or _detect_chrome()
        if chrome_path is None:
            raise RuntimeError(
                "Could not find Chrome executable. Set ``chrome_path`` "
                "in the authenticated browser config."
            )

        # Resolve the user-data-dir + profile-directory.  Chrome refuses to
        # enable CDP when --user-data-dir is the default Chrome data dir, so
        # we may redirect to a custom non-default directory.
        user_data_dir, profile_name = _resolve_cdp_user_data(profile_dir)

        args = [
            chrome_path,
            f"--remote-debugging-port={config.cdp_port}",
            f"--user-data-dir={user_data_dir}",
            f"--profile-directory={profile_name}",
            "--no-first-run",
            "--no-default-browser-check",
            # Allow a second Chrome instance even when the profile is already
            # in use by a user's normal browser window. Without this flag
            # Chrome detects the existing instance, forwards the launch to
            # it, and exits immediately — so the CDP port never opens.
            "--disable-running-process-check",
        ]

        print(
            f"[authenticated] Launching Chrome: {' '.join(args)}",
            file=sys.stderr,
            flush=True,
        )

        cls._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        # Immediate crash check — poll right after launch.
        if cls._process.poll() is not None:
            stderr_output = cls._process.stderr.read().decode("utf-8", errors="replace") if cls._process.stderr else ""
            print(
                f"[authenticated] Chrome exited immediately (code={cls._process.returncode}). "
                f"Stderr: {stderr_output[:2000]}",
                file=sys.stderr,
                flush=True,
            )
            cls._process = None
            raise RuntimeError(
                f"Chrome exited immediately with code {cls._process.returncode if cls._process else '?'}. "
                f"Stderr: {stderr_output[:500]}"
            )

        # Wait for the CDP endpoint to become responsive.
        cdp_url = f"http://127.0.0.1:{config.cdp_port}/json/version"
        try:
            _await_cdp_ready(cdp_url, timeout=_CDP_READY_TIMEOUT_S)
        except TimeoutError:
            # Diagnose: is the process still alive?
            alive = cls._is_process_alive()
            stderr_output = ""
            if cls._process is not None and cls._process.stderr is not None:
                try:
                    stderr_output = cls._process.stderr.read().decode("utf-8", errors="replace")
                except Exception:
                    stderr_output = "(failed to read stderr)"
            # Check if port is in use by another process
            port_in_use = _probe_cdp(config.cdp_port)
            print(
                f"[authenticated] CDP timeout diagnostics:\n"
                f"  Process alive: {alive}\n"
                f"  Port {config.cdp_port} responding: {port_in_use}\n"
                f"  Chrome stderr: {stderr_output[:2000]}",
                file=sys.stderr,
                flush=True,
            )
            if not alive:
                cls._process = None
            raise

        # **Post-launch alive check**: if the process died before CDP became
        # ready (e.g. port conflict), the CDP probe may have connected to an
        # *already-running* Chrome from another process.  Detect this and
        # clear ``_process`` so we don't enter a dead-process retry loop.
        if cls._process is not None and not cls._is_process_alive():
            print(
                f"[authenticated] Chrome pid={cls._process.pid} died after launch "
                "(port conflict?), clearing process handle",
                file=sys.stderr,
                flush=True,
            )
            cls._process = None

        print(
            f"[authenticated] Chrome launched (pid={cls._process.pid if cls._process else 'N/A'}), "
            f"CDP ready at port {config.cdp_port}",
            file=sys.stderr,
            flush=True,
        )

    @classmethod
    def _connect_cdp(cls, config: AuthenticatedConfig) -> None:
        """Attach Playwright to the already-running Chrome via CDP."""
        os.environ.setdefault("NODE_NO_WARNINGS", "1")
        from playwright.sync_api import sync_playwright

        cls._playwright = sync_playwright().start()
        cdp_endpoint = f"http://127.0.0.1:{config.cdp_port}"
        cls._browser = cls._playwright.chromium.connect_over_cdp(cdp_endpoint)

        # Register popup/page event listener for OAuth flows
        cls._popup_pages: list[Any] = []

        def _on_page(page: Any) -> None:
            """Capture newly created pages (popups, new tabs) from OAuth flows."""
            cls._popup_pages.append(page)

        if cls._browser.contexts:
            cls._browser.contexts[0].on("page", _on_page)

        print(
            f"[authenticated] Playwright connected via CDP at {cdp_endpoint}",
            file=sys.stderr,
            flush=True,
        )

    @classmethod
    def popup_pages(cls) -> list[Any]:
        """Return any popup windows captured since connection."""
        return getattr(cls, "_popup_pages", [])

    @classmethod
    def _cleanup(cls) -> None:
        """Kill Chrome and tear down Playwright."""
        # Close Playwright browser first.
        if cls._browser is not None:
            try:
                cls._browser.close()
            except Exception:
                pass
            cls._browser = None
        # Stop the Playwright driver.
        if cls._playwright is not None:
            try:
                cls._playwright.stop()
            except Exception:
                pass
            cls._playwright = None
        # Kill the Chrome process.
        if cls._process is not None:
            try:
                cls._process.kill()
                cls._process.wait(timeout=5)
            except Exception:
                pass
            cls._process = None

    @classmethod
    def _is_process_alive(cls) -> bool:
        if cls._process is None:
            return False
        ret = cls._process.poll()
        return ret is None  # None means still running


def _default_chrome_user_data_dir() -> Path | None:
    """Return the default Chrome user data directory for this OS, or ``None``
    if it cannot be determined."""
    # Windows
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Google" / "Chrome" / "User Data"
        if candidate.is_dir():
            return candidate.resolve()
    # macOS
    candidate = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    if candidate.is_dir():
        return candidate.resolve()
    # Linux
    candidate = Path.home() / ".config" / "google-chrome"
    if candidate.is_dir():
        return candidate.resolve()
    return None


# ---------------------------------------------------------------------------
# Lock file helpers — prevent concurrent profile copies across processes
# ---------------------------------------------------------------------------

_LOCK_FILE = _PROJECT_ROOT / "chrome-data" / ".profile-copy.lock"
_LOCK_ACQUIRED: bool = False  # per-process tracking for cleanup


def _acquire_copy_lock(timeout: float = 60.0) -> bool:
    """Cross-process mutual-exclusion for profile copy operations.

    Uses ``O_CREAT | O_EXCL`` which is atomic on NTFS (Windows) and
    POSIX filesystems.  A process that crashes while holding the lock
    leaves a stale file, so the lock file stores the PID — if the PID
    is dead the next caller claims it.
    """
    deadline = time.monotonic() + timeout
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    while time.monotonic() < deadline:
        # Stale-check: if the lock file exists but the owning PID is dead.
        if _LOCK_FILE.exists():
            try:
                raw = _LOCK_FILE.read_text("utf-8").strip()
                if raw:
                    stale_pid = int(raw)
                    if not _pid_alive(stale_pid):
                        _LOCK_FILE.unlink(missing_ok=True)
            except (ValueError, OSError):
                _LOCK_FILE.unlink(missing_ok=True)

        try:
            fd = os.open(
                str(_LOCK_FILE),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            global _LOCK_ACQUIRED
            _LOCK_ACQUIRED = True
            return True
        except FileExistsError:
            time.sleep(0.3)

    return False


def _release_copy_lock() -> None:
    """Release the cross-process profile-copy lock."""
    global _LOCK_ACQUIRED
    if not _LOCK_ACQUIRED:
        return
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    _LOCK_ACQUIRED = False


# ---------------------------------------------------------------------------
# Profile directory copy — handles locked files on Windows via robocopy
# ---------------------------------------------------------------------------


def _copy_profile_tree(src: Path, dst: Path) -> None:
    """Copy *src* to *dst* using ``robocopy`` (Windows only).

    On Windows the built-in ``robocopy`` utility is used because it
    can read files locked by another process (e.g. Chrome's SQLite
    cookie/login databases).  Non-Windows platforms are not supported
    for profile copy — Chrome CDP on this app targets Windows.
    """
    if sys.platform != "win32":
        print(
            f"[authenticated] Profile copy is only supported on Windows "
            f"(current platform: {sys.platform})",
            file=sys.stderr,
            flush=True,
        )
        return

    # robocopy returns 0-7 for success, 8+ for errors.
    retry_count = 0
    max_retries = 3
    while retry_count < max_retries:
        result = subprocess.run(
            [
                "robocopy",
                str(src),
                str(dst),
                "/E",              # copy subdirectories (including empty)
                "/COPY:DAT",       # copy Data + Attributes + Timestamps
                "/R:3",            # 3 retries on locked files
                "/W:2",            # 2 s wait between retries
                "/NFL",            # no file list logging
                "/NDL",            # no directory list logging
                "/NJH",            # no job header
                "/NJS",            # no job summary
                "/XD",             # exclude cache dirs
                "Cache",
                "Code Cache",
                "GPUCache",
            ],
            capture_output=True,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        # robocopy exit codes: 0-7 = success (some files may have been
        # skipped or retried), 8+ = error (insufficient permissions etc.)
        if result.returncode < 8:
            return
        retry_count += 1
        print(
            f"[authenticated] robocopy attempt {retry_count} failed "
            f"(code={result.returncode}), retrying…",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(1.0)

    print(
        f"[authenticated] WARNING: robocopy failed after {max_retries} attempts "
        f"(code={result.returncode}), profile may be incomplete",
        file=sys.stderr,
        flush=True,
    )


def _resolve_cdp_user_data(profile_dir: Path) -> tuple[Path, str]:
    """Return ``(user_data_dir, profile_name)`` suitable for Chrome CDP launch.

    Chrome **refuses to enable remote debugging** when ``--user-data-dir``
    points to the default Chrome user data directory (the error reads
    *"DevTools remote debugging requires a non-default data directory"*).

    This function detects that situation and redirects to a dedicated
    non-default user data directory under ``<project-root>/chrome-data/``,
    copying the profile data there on first use.
    """
    profile_dir = profile_dir.resolve()
    parent = profile_dir.parent
    profile_name = profile_dir.name

    # If the user-provided path IS the user-data root itself (no
    # profile subdirectory), use "Default" as the profile name.
    if profile_name.lower() in ("user data", "chrome", "google-chrome"):
        profile_name = "Default"

    default_ud = _default_chrome_user_data_dir()
    if default_ud is None or parent.resolve() != default_ud:
        # Not the default directory — use as-is.
        return parent, profile_name

    # The parent IS the default Chrome user data dir → Chrome will refuse CDP.
    # Use a dedicated non-default directory instead.
    custom_root = _PROJECT_ROOT / "chrome-data"
    src_profile = profile_dir  # the original Profile 2 path
    dst_profile = custom_root / profile_name

    if dst_profile.exists():
        # Profile already exists from a prior run — use it as-is. Chrome
        # manages its own session state (cookies, Local Storage, etc.)
        # inside this directory across runs, just like a real user's profile.
        # NEVER re-copy, or Google detects session cloning / account hijacking.
        print(
            f"[authenticated] Using existing profile at {dst_profile} "
            f"(Chrome manages its own session state here)",
            file=sys.stderr,
            flush=True,
        )
        return custom_root, profile_name

    # First-time setup: copy the profile from the user's real Chrome data.
    # This is a one-time operation — after this, Chrome writes directly to
    # the managed copy and preserves its session naturally.
    print(
        f"[authenticated] First-run — copying profile {src_profile} -> {dst_profile} "
        f"(Chrome requires a non-default --user-data-dir for CDP)",
        file=sys.stderr,
        flush=True,
    )

    # Acquire cross-process lock so concurrent subprocesses do not race
    # on copytree during first-time setup.
    acquired = _acquire_copy_lock()
    if not acquired:
        print(
            f"[authenticated] Could not acquire profile-copy lock within 60 s; "
            f"proceeding without lock (risk of concurrent copies)",
            file=sys.stderr,
            flush=True,
        )

    try:
        custom_root.mkdir(parents=True, exist_ok=True)

        # Copy Local State from the original User Data root (needed for
        # profile detection).  Use robocopy on Windows for locked files.
        src_local_state = default_ud / "Local State"
        if src_local_state.exists():
            dst_local_state = custom_root / "Local State"
            if sys.platform == "win32":
                subprocess.run(
                    [
                        "robocopy",
                        str(default_ud),
                        str(custom_root),
                        "Local State",
                        "/COPY:DAT",
                        "/R:2",
                        "/W:1",
                        "/NFL",
                        "/NDL",
                        "/NJH",
                        "/NJS",
                    ],
                    capture_output=True,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0,
                )

        # Copy the profile directory using the platform-aware helper.
        _copy_profile_tree(src_profile, dst_profile)
    finally:
        if acquired:
            _release_copy_lock()

    print(
        f"[authenticated] Profile copy complete. Chrome will now manage "
        f"session state directly at {dst_profile}",
        file=sys.stderr,
        flush=True,
    )

    return custom_root, profile_name


def _detect_chrome() -> str | None:
    """Try to locate the Chrome executable on the system.

    Checks common paths on Windows, macOS, and Linux.
    """

    # Windows
    for candidate in (
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(
            r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"
        ),
        os.path.expandvars(
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
        ),
    ):
        if os.path.isfile(candidate):
            return candidate

    # macOS
    mac_path = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    if os.path.isfile(mac_path):
        return mac_path

    # Linux
    for candidate in (
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ):
        if os.path.isfile(candidate):
            return candidate

    return None


def _await_cdp_ready(url: str, timeout: float = _CDP_READY_TIMEOUT_S) -> None:
    """Poll the CDP ``/json/version`` endpoint until it responds.

    Raises ``TimeoutError`` if the endpoint does not become reachable within
    ``timeout`` seconds.
    """
    import urllib.request
    import urllib.error

    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError, ConnectionError) as e:
            last_err = e
        time.sleep(_CDP_POLL_S)
    raise TimeoutError(
        f"CDP endpoint at {url} did not become ready within {timeout}s. "
        f"Last error: {last_err}"
    )
