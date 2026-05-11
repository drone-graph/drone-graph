"""Per-drone Playwright wrapper. One ``BrowserSessionManager`` per drone
subprocess, with a cache of persistent-context Chromiums keyed by profile
name.

The manager lazily starts Playwright on first use, keeps each profile's
context alive across tool calls (so a single drone working a multi-step
form sees the same page state across turns), and tears everything down
when the drone exits.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from drone_graph.tools.builtins.browser.profiles import profile_dir

if TYPE_CHECKING:  # pragma: no cover
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
    )

logger = logging.getLogger(__name__)


class BrowserSessionManager:
    """Owns a single Playwright instance and its persistent contexts.

    Contexts are keyed by profile name. Each context wraps a Chromium
    user-data-dir on disk, so cookies / local storage / saved logins
    persist across drone restarts. A long-running drone calling
    ``page("linkedin-main")`` repeatedly reuses the same window.

    Headed by default — the operator sees real Chrome windows pop up on
    the desktop, can intervene by clicking / typing directly. Headless
    mode is available via the ``DRONE_GRAPH_BROWSER_HEADLESS`` env var
    for unattended runs.
    """

    def __init__(self, drone_id: str, *, screenshots_dir: Path | None = None) -> None:
        self.drone_id = drone_id
        self._pw_cm = None
        self._pw: Playwright | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}
        self._screenshots = screenshots_dir or (
            Path(tempfile.gettempdir())
            / "drone-graph-browser-screenshots"
            / drone_id
        )
        self._screenshots.mkdir(parents=True, exist_ok=True)

    # ---- Lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._pw is not None:
            return
        from playwright.sync_api import sync_playwright  # heavy import

        self._pw_cm = sync_playwright()
        self._pw = self._pw_cm.__enter__()

    def stop(self) -> None:
        """Close every context + the Playwright driver. Idempotent."""
        for name, ctx in list(self._contexts.items()):
            try:
                ctx.close()
            except Exception as e:  # noqa: BLE001 - best-effort teardown
                logger.warning("closing context %s failed: %s", name, e)
        self._contexts.clear()
        self._pages.clear()
        if self._pw_cm is not None:
            try:
                self._pw_cm.__exit__(None, None, None)
            except Exception:
                pass
            self._pw_cm = None
            self._pw = None

    def __enter__(self) -> "BrowserSessionManager":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ---- Context + page management -------------------------------------

    def context(self, profile_name: str) -> "BrowserContext":
        """Get or open a persistent context for ``profile_name``."""
        if profile_name in self._contexts:
            return self._contexts[profile_name]
        self.start()
        assert self._pw is not None
        path = profile_dir(profile_name)
        headless = _headless_from_env()
        # Window title hint so the operator can identify drone-owned
        # windows in the OS window manager. Chromium picks this up from
        # the page title, which we set explicitly after each navigation.
        ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(path),
            headless=headless,
            no_viewport=True,  # let the OS size it naturally
            args=[
                # Identify the drone in window manager listings:
                f"--window-name=drone-graph:{self.drone_id[:8]}/{profile_name}",
            ],
        )
        self._contexts[profile_name] = ctx
        if ctx.pages:
            self._pages[profile_name] = ctx.pages[0]
        else:
            self._pages[profile_name] = ctx.new_page()
        # Tag the initial page so it's identifiable while idle.
        try:
            self._pages[profile_name].set_default_timeout(30_000)
        except Exception:
            pass
        return ctx

    def page(self, profile_name: str) -> "Page":
        self.context(profile_name)
        return self._pages[profile_name]

    def close_profile(self, profile_name: str) -> bool:
        ctx = self._contexts.pop(profile_name, None)
        self._pages.pop(profile_name, None)
        if ctx is None:
            return False
        try:
            ctx.close()
        except Exception:
            pass
        return True

    def active_profiles(self) -> list[str]:
        return list(self._contexts.keys())

    # ---- Convenience ----------------------------------------------------

    def screenshot(self, profile_name: str, *, label: str = "screenshot") -> Path:
        """Take a screenshot of the profile's current page. Returns the
        path on disk; the caller embeds it in the tool result and/or any
        finding artefact paths."""
        page = self.page(profile_name)
        ts = time.strftime("%Y%m%dT%H%M%S")
        out = self._screenshots / f"{ts}-{profile_name}-{label}.png"
        try:
            page.screenshot(path=str(out), full_page=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("screenshot failed: %s", e)
            return out
        return out

    def set_window_title_hint(self, profile_name: str, hint: str) -> None:
        """Set the page title so window managers can identify the drone's
        window. Best-effort — some sites overwrite the title on
        navigation."""
        try:
            page = self.page(profile_name)
            safe = hint.replace("\\", "\\\\").replace("`", "\\`")
            page.evaluate(f"document.title = `{safe}`")
        except Exception:
            pass


# ---- Module helpers --------------------------------------------------------


def _headless_from_env() -> bool:
    v = os.environ.get("DRONE_GRAPH_BROWSER_HEADLESS", "").strip().lower()
    return v in ("1", "true", "yes", "on")


_DRONE_MANAGER: dict[str, BrowserSessionManager] = {}


def manager_for_drone(drone_id: str) -> BrowserSessionManager:
    """Module-level cache so successive ``cm_browser`` calls inside the
    same drone process share contexts. The drone runtime is single-
    threaded so we don't need locking."""
    m = _DRONE_MANAGER.get(drone_id)
    if m is None:
        m = BrowserSessionManager(drone_id)
        _DRONE_MANAGER[drone_id] = m
    return m


def stop_all_managers() -> None:
    """Tear down every browser session for the current process. Called
    by the drone runtime at exit."""
    for m in list(_DRONE_MANAGER.values()):
        try:
            m.stop()
        except Exception:
            pass
    _DRONE_MANAGER.clear()


def has_active_manager(drone_id: str) -> bool:
    return drone_id in _DRONE_MANAGER
