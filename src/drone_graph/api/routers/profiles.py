"""Browser-profile provisioning API.

Endpoints to list, launch, and register headed-browser profiles so the
operator can sign into services interactively without leaving the web UI.
"""

from __future__ import annotations

from pathlib import Path

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
    cdp_running: bool


class ChromeProfileInfo(BaseModel):
    """A discovered Chrome profile on the local system."""

    name: str
    path: str
    is_default: bool = False


class AuthConfigDTO(BaseModel):
    cdp_port: int
    authenticated_domains: list[str]
    chrome_path: str | None = None


class AuthConfigUpdate(BaseModel):
    cdp_port: int | None = None
    authenticated_domains: list[str] | None = None
    chrome_path: str | None = None


def _discover_chrome_profiles() -> list[ChromeProfileInfo]:
    """Scan the local system for available Chrome user-data profiles.

    Returns a list of ``ChromeProfileInfo`` entries with the profile display
    name (from ``Preferences``) and absolute path.
    """
    import json
    import os
    import platform

    # Determine the Chrome User Data root directory per OS.
    system = platform.system()
    if system == "Windows":
        root = Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"))
    elif system == "Darwin":
        root = Path.home() / "Library/Application Support/Google/Chrome"
    elif system == "Linux":
        root = Path.home() / ".config/google-chrome"
    else:
        return []

    if not root.is_dir():
        return []

    results: list[ChromeProfileInfo] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        # Profile dirs are either "Default" or "Profile N"
        if child.name != "Default" and not child.name.startswith("Profile "):
            continue
        # Read the display name from Preferences JSON.
        prefs_path = child / "Preferences"
        display_name = child.name
        if prefs_path.is_file():
            try:
                prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
                name = prefs.get("profile", {}).get("name", "")
                if name:
                    display_name = name
            except Exception:
                pass
        results.append(
            ChromeProfileInfo(
                name=display_name,
                path=str(child),
                is_default=(child.name == "Default"),
            )
        )
    return results


@router.get("/authenticated/available", response_model=list[ChromeProfileInfo])
def available_chrome_profiles() -> list[ChromeProfileInfo]:
    """List Chrome profiles found on the local system.

    Scans the Chrome User Data directory for profile folders (Default,
    Profile 1, …) and reads their display names from the ``Preferences``
    file. Returns an empty list if Chrome is not installed or no profiles
    exist.
    """
    return _discover_chrome_profiles()


@router.post("/authenticated/setup")
def setup_authenticated_profile(req: AuthSetupRequest) -> AuthSetupResponse:
    """Set the Chrome profile directory. The path should point to a
    Chrome user-data directory (created by signing into Chrome with a Google
    account). Saves the path to Settings — never exposed to AI."""
    p = Path(req.profile_path).expanduser().resolve()
    if not p.is_dir():
        return AuthSetupResponse(success=False, message=f"Directory not found: {p}")
    from drone_graph.api.settings import load_settings, save_settings

    s = load_settings()
    s.chrome_profile_dir = str(p)
    save_settings(s)
    return AuthSetupResponse(success=True, message=f"Profile set to {p}")


@router.get("/authenticated/status", response_model=AuthStatusResponse)
def authenticated_status() -> AuthStatusResponse:
    """Check if a Chrome profile is configured and whether
    the Chrome process is currently running."""
    from drone_graph.api.settings import load_settings

    s = load_settings()
    has_profile = bool(s.chrome_profile_dir) and Path(
        s.chrome_profile_dir
    ).is_dir()
    if not has_profile:
        return AuthStatusResponse(has_profile=False, cdp_running=False)
    try:
        from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (
            AuthenticatedChrome,
        )

        running = AuthenticatedChrome.is_running()
    except Exception:
        running = False
    return AuthStatusResponse(has_profile=True, cdp_running=running)


@router.post("/authenticated/start")
def start_authenticated_browser() -> AuthSetupResponse:
    """Launch the Chrome instance with the configured profile."""
    from drone_graph.api.settings import load_settings
    from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (
        AuthenticatedChrome,
    )
    from drone_graph.tools.builtins.browser.authenticated.config import load_config

    s = load_settings()
    if not s.chrome_profile_dir:
        return AuthSetupResponse(
            success=False, message="No Chrome profile configured"
        )
    cfg = load_config()
    try:
        AuthenticatedChrome.ensure_running(cfg, s.chrome_profile_dir)
        return AuthSetupResponse(
            success=True,
            message=f"Chrome started on port {cfg.cdp_port}",
        )
    except Exception as exc:
        return AuthSetupResponse(success=False, message=str(exc))


@router.post("/authenticated/stop")
def stop_authenticated_browser() -> AuthSetupResponse:
    """Stop the Chrome instance."""
    from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (
        AuthenticatedChrome,
    )

    try:
        AuthenticatedChrome.stop()
        return AuthSetupResponse(success=True, message="Chrome stopped")
    except Exception as exc:
        return AuthSetupResponse(success=False, message=str(exc))


@router.post("/authenticated/launch-browser")
def launch_authenticated_browser() -> AuthSetupResponse:
    """Launch Chrome as a CDP-enabled browser for manual sign-in.

    Uses the configured profile path from settings. The user can sign in to
    Google, accept cookies, etc. — the session will then be available to
    the CDP-managed Chrome on subsequent drone runs.
    """
    import subprocess
    import sys

    from drone_graph.api.settings import load_settings
    from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (
        _detect_chrome,
        _write_pid_file,
    )
    from drone_graph.tools.builtins.browser.authenticated.config import load_config

    s = load_settings()
    if not s.chrome_profile_dir:
        return AuthSetupResponse(
            success=False, message="No Chrome profile configured"
        )

    profile_dir = Path(s.chrome_profile_dir).resolve()
    if not profile_dir.is_dir():
        return AuthSetupResponse(
            success=False, message=f"Profile directory not found: {profile_dir}"
        )

    chrome_path = _detect_chrome()
    if chrome_path is None:
        return AuthSetupResponse(
            success=False,
            message="Could not find Chrome executable on this system",
        )

    config = load_config()

    parent = profile_dir.parent
    profile_name = profile_dir.name
    # If the path IS the User Data root itself, use "Default" as the profile.
    if profile_name.lower() in ("user data", "chrome", "google-chrome"):
        profile_name = "Default"

    args = [
        chrome_path,
        f"--user-data-dir={parent}",
        f"--profile-directory={profile_name}",
        f"--remote-debugging-port={config.cdp_port}",
        "--disable-running-process-check",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    print(
        f"[profiles] Launching browser with CDP: {' '.join(args)}",
        file=sys.stderr,
        flush=True,
    )

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _write_pid_file(proc.pid)
        return AuthSetupResponse(
            success=True,
            message=f"Chrome launched with CDP on port {config.cdp_port}, profile {profile_name}",
        )
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
