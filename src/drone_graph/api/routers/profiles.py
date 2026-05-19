"""Browser-profile provisioning API.

Endpoints to list, launch, and register headed-browser profiles so the
operator can sign into services interactively without leaving the web UI.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from drone_graph.api.state import get_state
from drone_graph.tools.builtins.browser.profiles import (
    get_profile_services,
    list_profiles,
    profile_dir,
    profiles_root,
    registered_tool_name,
    set_profile_services,
)
from drone_graph.tools.records import Tool, ToolKind

router = APIRouter(prefix="/api/profiles")


# ---- DTOs ------------------------------------------------------------------


class ProfileDTO(BaseModel):
    name: str
    path: str
    is_registered: bool
    registered_tool_name: str | None
    size_bytes: int
    size_label: str
    services: list[str]


class ProfileListResponse(BaseModel):
    profiles: list[ProfileDTO]
    profiles_root: str


class LaunchResponse(BaseModel):
    success: bool
    message: str


class RegisterRequest(BaseModel):
    profile_name: str
    summary: str = ""
    description: str = ""


class RegisterResponse(BaseModel):
    success: bool
    tool_name: str | None
    error: str | None = None


class UpdateServicesRequest(BaseModel):
    services: list[str]


# ---- Helpers ---------------------------------------------------------------


def _size_info(path: Path) -> tuple[int, str]:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    if total > 1_000_000:
        label = f"{total / 1_000_000:.1f} MB"
    elif total > 1_000:
        label = f"{total / 1_000:.0f} KB"
    else:
        label = f"{total} B"
    return total, label


def _is_registered(profile_name: str) -> bool:
    """Check if this profile has a registered tool in the graph."""
    state = get_state()
    tool_name = registered_tool_name(profile_name)
    rec = state.tool_store.get(tool_name)
    return rec is not None and rec.kind == ToolKind.installed


# ---- Endpoints -------------------------------------------------------------


@router.get("", response_model=ProfileListResponse)
def list_all_profiles() -> ProfileListResponse:
    """List all browser profiles on disk with registration status."""
    names = list_profiles()
    profiles: list[ProfileDTO] = []
    for name in names:
        p = profile_dir(name)
        size_bytes, size_label = _size_info(p)
        registered = _is_registered(name)
        services = get_profile_services(name)
        profiles.append(
            ProfileDTO(
                name=name,
                path=str(p),
                is_registered=registered,
                registered_tool_name=registered_tool_name(name) if registered else None,
                size_bytes=size_bytes,
                size_label=size_label,
                services=services,
            )
        )
    return ProfileListResponse(
        profiles=sorted(profiles, key=lambda x: x.name),
        profiles_root=str(profiles_root()),
    )


@router.post("/launch", response_model=LaunchResponse)
def launch_profile(profile_name: str) -> LaunchResponse:
    """Launch headed Chromium (Playwright's bundled) with the given profile so
    the operator can sign in to services interactively."""
    p = profile_dir(profile_name)
    if not p.exists():
        return LaunchResponse(
            success=False,
            message=f"Profile directory does not exist: {p}",
        )
    try:
        _spawn_browser_worker(profile_name, p)
        return LaunchResponse(
            success=True,
            message=(
                f"Launched Chromium with profile '{profile_name}'. "
                "Sign in to your services, then close the window."
            ),
        )
    except Exception as e:
        return LaunchResponse(
            success=False,
            message=f"Failed to launch browser: {e}",
        )


# ---- Playwright background worker -------------------------------------------


def _spawn_browser_worker(name: str, user_data_dir: Path) -> None:
    """Start headed Playwright Chromium on a daemon thread."""
    from threading import Thread

    Thread(
        target=_run_browser_session,
        args=(name, user_data_dir),
        daemon=True,
    ).start()


def _run_browser_session(name: str, user_data_dir: Path) -> None:
    """Run a headed (headless=False) Chrome session using the **user's real
    system Chrome** via Playwright's ``channel="chrome"``.

    Google's sign-in flow blocks Playwright's bundled Chromium with *"This
    browser or app may not be secure"*.  Using the real system Chrome
    (which the user's own Google account is likely already signed into)
    avoids that detection.

    Two anti-automation measures are applied:
    1. ``--disable-blink-features=AutomationControlled`` — prevents Chrome
       from exposing ``navigator.webdriver`` to JavaScript.
    2. ``playwright-stealth`` — patches 19+ browser-fingerprint vectors
       (webdriver, plugins, languages, chrome.*, WebGL, etc.) as a second
       line of defence.

    The session blocks until the operator closes all pages, then persists
    cookies / local storage back to disk for later drone usage.
    """
    import logging

    _log = logging.getLogger(__name__)
    print(
        f"[profiles._run_browser_session] Starting browser session for profile={name!r} dir={user_data_dir}",
        file=sys.stderr,
        flush=True,
    )

    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth

    _stealth = Stealth()
    try:
        with sync_playwright() as pw:
            print(
                "[profiles._run_browser_session] Playwright started, launching system Chrome (channel='chrome')...",
                file=sys.stderr,
                flush=True,
            )

            # ---- Launch the user's real system Chrome ------------------------
            # channel="chrome" tells Playwright to find and launch the user's
            # actual Chrome installation (via Windows registry/known paths)
            # instead of its bundled Chromium.  This makes the browser look
            # completely natural to Google's sign-in flow.
            # --disable-blink-features=AutomationControlled removes Playwright's
            # automation flag that would otherwise set navigator.webdriver=true.
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                channel="chrome",
                headless=False,
                no_viewport=True,
                args=[
                    f"--window-name=drone-graph:provisioning/{name}",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            print(
                f"[profiles._run_browser_session] Context launched, pages={len(ctx.pages)}",
                file=sys.stderr,
                flush=True,
            )

            # Apply stealth as a second line of defence.
            _stealth.apply_stealth_sync(ctx)
            print(
                "[profiles._run_browser_session] Stealth applied",
                file=sys.stderr,
                flush=True,
            )

            # Ensure at least one page is visible.
            if not ctx.pages:
                print(
                    "[profiles._run_browser_session] No pages, creating new page",
                    file=sys.stderr,
                    flush=True,
                )
                ctx.new_page()
            else:
                _bring_to_front(ctx.pages[0])

            print(
                "[profiles._run_browser_session] Browser window should now be visible. Polling for close...",
                file=sys.stderr,
                flush=True,
            )

            # Poll until the user closes all pages.
            import time

            while _has_open_pages(ctx):
                time.sleep(2)

            print(
                "[profiles._run_browser_session] All pages closed, cleaning up",
                file=sys.stderr,
                flush=True,
            )
            ctx.close()
            print(
                "[profiles._run_browser_session] Session ended cleanly",
                file=sys.stderr,
                flush=True,
            )
    except Exception as exc:
        print(
            f"[profiles._run_browser_session] ERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        _log.exception("Browser session crashed for profile %s", name)


def _has_open_pages(ctx: "Any") -> bool:  # noqa: ANN401 — avoid playwright import at module level
    """Return True if at least one non-closed page remains."""
    try:
        pages = ctx.pages
        if not pages:
            return False
        return any(not p.is_closed() for p in pages)
    except Exception:
        return False


def _bring_to_front(page: "Any") -> None:  # noqa: ANN401
    """Best-effort: raise the browser window to the foreground."""
    try:
        page.bring_to_front()
    except Exception:
        pass


@router.post("/register", response_model=RegisterResponse)
def register_profile(req: RegisterRequest) -> RegisterResponse:
    """Register a profile as a discoverable tool in the graph."""
    state = get_state()
    tool_name = registered_tool_name(req.profile_name)
    try:
        rec = Tool(
            name=tool_name,
            description=req.description
            or f"Headed Chromium browser session with profile {req.profile_name!r}. "
            "Use cm_browser(profile=...) to drive.",
            input_schema_json='{"type": "object", "properties": {}}',
            kind=ToolKind.installed,
            usage=f'cm_browser(action="open_url", profile="{req.profile_name}", url="…")',
            install_commands=[],
            depends_on=["cm_browser"],
        )
        state.tool_store.register_installed(rec)
        return RegisterResponse(success=True, tool_name=tool_name)
    except (ValueError, KeyError, TypeError) as e:
        return RegisterResponse(
            success=False,
            tool_name=None,
            error=f"{type(e).__name__}: {e}",
        )


@router.patch("/{profile_name}/services", response_model=ProfileDTO)
def update_profile_services(profile_name: str, req: UpdateServicesRequest) -> ProfileDTO:
    """Update the service tags for a profile."""
    p = profile_dir(profile_name)
    size_bytes, size_label = _size_info(p)
    registered = _is_registered(profile_name)
    services = set_profile_services(profile_name, req.services)
    return ProfileDTO(
        name=profile_name,
        path=str(p),
        is_registered=registered,
        registered_tool_name=registered_tool_name(profile_name) if registered else None,
        size_bytes=size_bytes,
        size_label=size_label,
        services=services,
    )


@router.delete("/{profile_name}")
def delete_profile_ep(profile_name: str) -> dict[str, bool]:
    """Delete a profile from disk."""
    from drone_graph.tools.builtins.browser.profiles import delete_profile

    ok = delete_profile(profile_name)
    return {"deleted": ok}


# ── Authenticated Chrome profile lane ───────────────────────────────────


class AuthSetupRequest(BaseModel):
    profile_path: str


class AuthSetupResponse(BaseModel):
    success: bool
    message: str


class AuthStatusResponse(BaseModel):
    has_profile: bool
    is_running: bool


class AuthConfigDTO(BaseModel):
    cdp_port: int
    authenticated_domains: list[str]
    chrome_path: str | None = None


class AuthConfigUpdate(BaseModel):
    cdp_port: int | None = None
    authenticated_domains: list[str] | None = None
    chrome_path: str | None = None


@router.post("/authenticated/setup")
def setup_authenticated_profile(req: AuthSetupRequest) -> AuthSetupResponse:
    """Set the authenticated Chrome profile path. The path should point to a
    Chrome user-data directory (created by signing into Chrome with a Google
    account). Saves the path to Settings — never exposed to AI."""
    p = Path(req.profile_path).expanduser().resolve()
    if not p.is_dir():
        return AuthSetupResponse(success=False, message=f"Directory not found: {p}")
    from drone_graph.api.settings import load_settings, save_settings

    s = load_settings()
    s.authenticated_chrome_profile_path = str(p)
    save_settings(s)
    return AuthSetupResponse(success=True, message=f"Profile set to {p}")


@router.get("/authenticated/status", response_model=AuthStatusResponse)
def authenticated_status() -> AuthStatusResponse:
    """Check if an authenticated Chrome profile is configured and whether
    the Chrome process is currently running."""
    from drone_graph.api.settings import load_settings

    s = load_settings()
    has_profile = bool(s.authenticated_chrome_profile_path) and Path(
        s.authenticated_chrome_profile_path
    ).is_dir()
    if not has_profile:
        return AuthStatusResponse(has_profile=False, is_running=False)
    try:
        from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (
            AuthenticatedChrome,
        )

        running = AuthenticatedChrome.is_running()
    except Exception:
        running = False
    return AuthStatusResponse(has_profile=True, is_running=running)


@router.post("/authenticated/start")
def start_authenticated_browser() -> AuthSetupResponse:
    """Launch the authenticated Chrome instance."""
    from drone_graph.api.settings import load_settings
    from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (
        AuthenticatedChrome,
    )
    from drone_graph.tools.builtins.browser.authenticated.config import load_config

    s = load_settings()
    if not s.authenticated_chrome_profile_path:
        return AuthSetupResponse(
            success=False, message="No authenticated profile configured"
        )
    cfg = load_config()
    try:
        AuthenticatedChrome.ensure_running(cfg, s.authenticated_chrome_profile_path)
        return AuthSetupResponse(
            success=True,
            message=f"Authenticated Chrome started on port {cfg.cdp_port}",
        )
    except Exception as exc:
        return AuthSetupResponse(success=False, message=str(exc))


@router.post("/authenticated/stop")
def stop_authenticated_browser() -> AuthSetupResponse:
    """Stop the authenticated Chrome instance."""
    from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (
        AuthenticatedChrome,
    )

    try:
        AuthenticatedChrome.stop()
        return AuthSetupResponse(success=True, message="Authenticated Chrome stopped")
    except Exception as exc:
        return AuthSetupResponse(success=False, message=str(exc))


@router.get("/authenticated/config", response_model=AuthConfigDTO)
def get_authenticated_config() -> AuthConfigDTO:
    """Get the authenticated browser configuration (CDP port, domains, etc.)."""
    from drone_graph.tools.builtins.browser.authenticated.config import load_config

    cfg = load_config()
    return AuthConfigDTO(
        cdp_port=cfg.cdp_port,
        authenticated_domains=list(cfg.authenticated_domains),
        chrome_path=cfg.chrome_path,
    )


@router.put("/authenticated/config")
def update_authenticated_config(req: AuthConfigUpdate) -> AuthSetupResponse:
    """Update the authenticated browser configuration."""
    from drone_graph.tools.builtins.browser.authenticated.config import (
        load_config,
        save_config,
    )

    cfg = load_config()
    if req.cdp_port is not None:
        cfg.cdp_port = req.cdp_port
    if req.authenticated_domains is not None:
        cfg.authenticated_domains = list(req.authenticated_domains)
    if req.chrome_path is not None:
        cfg.chrome_path = req.chrome_path
    save_config(cfg)
    return AuthSetupResponse(success=True, message="Configuration updated")
