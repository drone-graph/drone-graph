"""Manage a long-running system Chrome process with CDP for the authenticated
profile lane.

Key anti-detection strategy
---------------------------
Chrome is launched via ``subprocess.Popen`` — **not** via Playwright — so the
process never receives Playwright's ``--enable-automation`` flag. Playwright
then attaches to the already-running instance via ``connect_over_cdp``, which
does not set ``navigator.webdriver``. Google's detection sees a normal Chrome
browser being used by a real person.
"""

from __future__ import annotations

import subprocess
import time
import sys
from pathlib import Path
from typing import Any

from drone_graph.tools.builtins.browser.authenticated.config import (
    AuthenticatedConfig,
    load_config,
)
from drone_graph.api.settings import load_settings

# How long (seconds) to wait for the CDP endpoint to become ready after launch.
_CDP_READY_TIMEOUT_S = 15.0
_CDP_POLL_S = 0.3


class AuthenticatedChrome:
    """Singleton: one Chrome process with the dedicated authenticated profile.

    Class-level state so the same Chrome instance is shared across all drone
    tool calls in the same process.
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

        1. If Chrome is already running and CDP is reachable, connect.
        2. Otherwise, launch Chrome via ``subprocess.Popen`` with the
           dedicated profile and ``--remote-debugging-port``, wait for CDP,
           then connect.

        Parameters
        ----------
        config : AuthenticatedConfig | None
            Lane configuration. If ``None``, loaded from disk.
        profile_dir : str | Path | None
            Chrome user data directory. If ``None``, read from settings.

        Returns
        -------
        playwright.sync_api.Browser
        """
        if config is None:
            config = load_config()
        if profile_dir is None:
            settings = load_settings()
            if not settings.authenticated_chrome_profile_path:
                raise RuntimeError(
                    "No authenticated Chrome profile configured. "
                    "Use cm_check_auth_profile for status."
                )
            profile_dir = Path(settings.authenticated_chrome_profile_path)

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

    @classmethod
    def is_running(cls) -> bool:
        """Check if the Chrome process is still alive."""
        return cls._process is not None and cls._is_process_alive()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _launch_chrome(
        cls,
        config: AuthenticatedConfig,
        profile_dir: Path,
    ) -> None:
        """Start Chrome as a subprocess with CDP enabled."""
        chrome_path = config.chrome_path or _detect_chrome()
        if chrome_path is None:
            raise RuntimeError(
                "Could not find Chrome executable. Set ``chrome_path`` "
                "in the authenticated browser config."
            )

        args = [
            chrome_path,
            f"--remote-debugging-port={config.cdp_port}",
            f"--user-data-dir={str(profile_dir)}",
            "--no-first-run",
            "--no-default-browser-check",
        ]

        print(
            f"[authenticated] Launching Chrome: {' '.join(args)}",
            file=sys.stderr,
            flush=True,
        )

        cls._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for the CDP endpoint to become responsive.
        cdp_url = f"http://127.0.0.1:{config.cdp_port}/json/version"
        _await_cdp_ready(cdp_url, timeout=_CDP_READY_TIMEOUT_S)

        print(
            f"[authenticated] Chrome launched (pid={cls._process.pid}), "
            f"CDP ready at port {config.cdp_port}",
            file=sys.stderr,
            flush=True,
        )

    @classmethod
    def _connect_cdp(cls, config: AuthenticatedConfig) -> None:
        """Attach Playwright to the already-running Chrome via CDP."""
        from playwright.sync_api import sync_playwright

        cls._playwright = sync_playwright().start()
        cdp_endpoint = f"http://127.0.0.1:{config.cdp_port}"
        cls._browser = cls._playwright.chromium.connect_over_cdp(cdp_endpoint)

        print(
            f"[authenticated] Playwright connected via CDP at {cdp_endpoint}",
            file=sys.stderr,
            flush=True,
        )

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


def _detect_chrome() -> str | None:
    """Try to locate the Chrome executable on the system.

    Checks common paths on Windows, macOS, and Linux.
    """
    import os

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
