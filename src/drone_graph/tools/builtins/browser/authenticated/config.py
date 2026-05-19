"""Configuration for the authenticated Chrome profile lane.

Stored as a standalone JSON file at
``~/.config/drone-graph/authenticated-browser-config.json`` — separate from
``settings.json`` because the profile path itself lives in the Settings model
(``authenticated_chrome_profile_path``) and is never exposed to the AI.

Fields
------
cdp_port : int
    Port for Chrome's remote debugging protocol. Default 9222.
authenticated_domains : list[str]
    Domain patterns that should be routed through the authenticated lane.
chrome_path : str | None
    Path to Chrome/Chromium executable. ``None`` = auto-detect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_FILE_NAME = "authenticated-browser-config.json"
DEFAULT_CDP_PORT = 9222
DEFAULT_DOMAINS: list[str] = [
    "accounts.google.com",
    "mail.google.com",
    "gmail.com",
    "youtube.com",
    "googleapis.com",
]


def _config_dir() -> Path:
    return Path.home() / ".config" / "drone-graph"


def config_path() -> Path:
    return _config_dir() / CONFIG_FILE_NAME


@dataclass
class AuthenticatedConfig:
    """Settings for the authenticated Chrome lane.

    Note: The profile directory path is NOT stored here — it lives in the
    Settings model (``settings.authenticated_chrome_profile_path``) so it
    is never exposed to the AI.
    """

    cdp_port: int = DEFAULT_CDP_PORT
    authenticated_domains: list[str] = field(default_factory=lambda: DEFAULT_DOMAINS.copy())
    chrome_path: str | None = None  # None = auto-detect


def load_config() -> AuthenticatedConfig:
    """Load config from the JSON file, falling back to defaults if the file
    doesn't exist or is malformed."""
    p = config_path()
    if not p.exists():
        return AuthenticatedConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return AuthenticatedConfig(
            cdp_port=raw.get("cdp_port", DEFAULT_CDP_PORT),
            authenticated_domains=raw.get(
                "authenticated_domains", DEFAULT_DOMAINS.copy()
            ),
            chrome_path=raw.get("chrome_path"),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return AuthenticatedConfig()


def save_config(cfg: AuthenticatedConfig) -> None:
    """Persist config to the JSON file."""
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(asdict(cfg), indent=2, default=str),
        encoding="utf-8",
    )
