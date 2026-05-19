"""Operator settings: keys, default provider/model, default ceilings.

Stored in ``~/.config/drone-graph/settings.json`` so they survive across
runs without polluting the project's ``.env``. Secrets file is chmod'd to
0600 on write.

On boot, ``apply_to_env()`` mirrors any stored keys into ``os.environ``
before provider resolution runs. The mission-control UI can mutate this
file at runtime; the controller is reconstructed on save.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def settings_path() -> Path:
    base = Path.home() / ".config" / "drone-graph"
    return base / "settings.json"


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    default_cost_ceiling_usd: float | None = None
    # Tracks whether the operator has explicitly decided on a cost ceiling
    # during onboarding (either picked a number or knowingly chose
    # "unlimited"). Distinguishes "user hasn't seen the budget step yet"
    # from "user actively chose unlimited" — both leave
    # ``default_cost_ceiling_usd`` as ``None``.
    cost_ceiling_acknowledged: bool = False
    default_paranoid_install: bool = False
    sound_enabled: bool = False
    # Operator overrides for per-provider tier routing. Outer key is the
    # provider name (``anthropic`` / ``openai``); inner key is the
    # ModelTier value; inner value is a dgraph_model_id from the registry.
    # When set, takes precedence over ``tier_defaults_by_provider`` in the
    # packaged registry JSON. Empty/missing entries fall through to the
    # registry default.
    tier_overrides: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Computer-use concurrency. Each drone holding a Chromium window costs
    # ~150-300MB RAM and visible desktop real-estate; 4 is comfortable on a
    # 16GB laptop, more for bigger machines. Drones beyond this wait in
    # queue (via signals.try_acquire) until a slot frees up.
    max_concurrent_browsers: int = 4
    # Permission tier governs which drone tool calls block for operator
    # approval. ``open`` runs everything with no friction;
    # ``ask_external`` prompts before tool calls with external-world
    # effects (sending mail, posting publicly, deploying, charging,
    # pushing to remotes); ``ask_everything`` prompts before any tool
    # call that touches the machine or web (read-only substrate
    # queries still run freely). Drones always have full access to the
    # operator's machine and accounts — the tier only governs prompts.
    permission_tier: Literal["open", "ask_external", "ask_everything"] = (
        "ask_external"
    )
    # Tracks whether the operator has explicitly seen + answered the
    # permission step during onboarding.
    permission_tier_acknowledged: bool = False
    # Absolute path to the directory where drones save all generated files.
    # Defaults to a "workspace" folder in the project root. The frontend
    # provides a folder picker during onboarding so the user can place
    # output wherever they want (e.g. Desktop, Documents, a shared drive).
    workspace_dir: str | None = None
    # Tracks whether the operator has explicitly seen + answered the
    # workspace step during onboarding.
    workspace_dir_acknowledged: bool = False
    # Absolute path to a Chrome user-data directory that carries an
    # authenticated session (Google/Gmail/YouTube etc.). Set via the
    # authenticated-browser setup API. **Never** exposed to AI — the
    # authenticated-browser tool resolves it server-side.
    authenticated_chrome_profile_path: str | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


def load_settings() -> Settings:
    p = settings_path()
    if not p.exists():
        return Settings()
    try:
        return Settings.model_validate_json(p.read_text(encoding="utf-8"))
    except Exception:
        return Settings()


def save_settings(s: Settings) -> Settings:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    s.updated_at = datetime.utcnow()
    p.write_text(s.model_dump_json(indent=2), encoding="utf-8")
    # 0600 — the file holds API keys.
    p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return s


def apply_to_env(s: Settings) -> None:
    """Mirror configured keys into ``os.environ`` so existing code paths
    that read env vars (providers.py, model_registry) pick them up.

    Settings is the operator's source of truth — if the file has a value,
    it overrides whatever was in the shell. If the file clears a value,
    we clear the env too so saving "no key" actually means "no key" without
    requiring a server restart.
    """
    for key_name, value in (
        ("ANTHROPIC_API_KEY", s.anthropic_api_key),
        ("OPENAI_API_KEY", s.openai_api_key),
    ):
        if value and value.strip():
            os.environ[key_name] = value.strip()
        else:
            # Cleared in Settings → clear from env.
            os.environ.pop(key_name, None)
    # Drone subprocesses inherit the parent's env at spawn; mirror the
    # browser-slot cap here so the concurrency layer sees it.
    os.environ["DRONE_GRAPH_MAX_BROWSER_SLOTS"] = str(s.max_concurrent_browsers)
    # Permission tier flows to drone subprocesses via env var; the tool
    # dispatcher reads it to decide which calls block for approval.
    os.environ["DRONE_GRAPH_PERMISSION_TIER"] = s.permission_tier


def has_any_key(s: Settings) -> bool:
    return bool(
        (s.anthropic_api_key and s.anthropic_api_key.strip())
        or (s.openai_api_key and s.openai_api_key.strip())
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


# ---- DTOs ------------------------------------------------------------------


class SettingsView(BaseModel):
    """Wire shape — masks the keys to last-4 only."""

    has_anthropic_key: bool
    has_openai_key: bool
    anthropic_key_hint: str | None = None
    openai_key_hint: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    default_cost_ceiling_usd: float | None = None
    cost_ceiling_acknowledged: bool = False
    default_paranoid_install: bool = False
    sound_enabled: bool = False
    tier_overrides: dict[str, dict[str, str]] = Field(default_factory=dict)
    max_concurrent_browsers: int = 4
    permission_tier: Literal["open", "ask_external", "ask_everything"] = (
        "ask_external"
    )
    permission_tier_acknowledged: bool = False
    # Absolute path to the directory where drones save generated files.
    # Empty string means the default "workspace" folder in the project root.
    workspace_dir: str
    workspace_dir_acknowledged: bool = False
    settings_path: str
    updated_at: datetime


def view(s: Settings) -> SettingsView:
    return SettingsView(
        has_anthropic_key=bool(
            (s.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        ),
        has_openai_key=bool(
            (s.openai_api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
        ),
        anthropic_key_hint=_hint(s.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")),
        openai_key_hint=_hint(s.openai_api_key or os.environ.get("OPENAI_API_KEY")),
        default_provider=s.default_provider,
        default_model=s.default_model,
        default_cost_ceiling_usd=s.default_cost_ceiling_usd,
        cost_ceiling_acknowledged=s.cost_ceiling_acknowledged,
        default_paranoid_install=s.default_paranoid_install,
        sound_enabled=s.sound_enabled,
        tier_overrides=dict(s.tier_overrides),
        max_concurrent_browsers=s.max_concurrent_browsers,
        permission_tier=s.permission_tier,
        permission_tier_acknowledged=s.permission_tier_acknowledged,
        workspace_dir=_effective_workspace_dir(s),
        workspace_dir_acknowledged=s.workspace_dir_acknowledged,
        settings_path=str(settings_path()),
        updated_at=s.updated_at,
    )


def _hint(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip()
    if len(v) <= 8:
        return "•" * len(v)
    return f"{v[:3]}…{v[-4:]}"


def _effective_workspace_dir(s: Settings) -> str:
    """Return the absolute workspace directory path.

    Uses ``s.workspace_dir`` if set; otherwise falls back to a ``workspace``
    folder in the project root so the operator doesn't have to configure
    anything to get started.
    """
    if s.workspace_dir:
        return str(Path(s.workspace_dir).resolve())
    return str(Path("workspace").resolve())


class SettingsPatch(BaseModel):
    anthropic_api_key: str | None = None
    """Pass empty string to clear. Pass ``None`` to leave unchanged."""
    openai_api_key: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    default_cost_ceiling_usd: float | None = None
    cost_ceiling_acknowledged: bool | None = None
    default_paranoid_install: bool | None = None
    sound_enabled: bool | None = None
    # Per-provider tier overrides. ``None`` leaves the whole map alone; a
    # dict replaces it wholesale. To clear a single tier override, set its
    # value to an empty string and the merge step will drop it.
    tier_overrides: dict[str, dict[str, str]] | None = None
    max_concurrent_browsers: int | None = None
    permission_tier: Literal["open", "ask_external", "ask_everything"] | None = None
    permission_tier_acknowledged: bool | None = None
    # Absolute path to the workspace directory. Empty string resets to default.
    workspace_dir: str | None = None
    workspace_dir_acknowledged: bool | None = None


def merge_patch(s: Settings, p: SettingsPatch) -> Settings:
    """Apply patch values to a fresh ``Settings`` instance. Empty string in a
    key field means "clear this key"; ``None`` means "leave alone"."""
    new = s.model_copy()
    if p.anthropic_api_key is not None:
        new.anthropic_api_key = p.anthropic_api_key.strip() or None
    if p.openai_api_key is not None:
        new.openai_api_key = p.openai_api_key.strip() or None
    if p.default_provider is not None:
        new.default_provider = p.default_provider or None
    if p.default_model is not None:
        new.default_model = p.default_model or None
    if p.default_cost_ceiling_usd is not None:
        new.default_cost_ceiling_usd = p.default_cost_ceiling_usd
    if p.cost_ceiling_acknowledged is not None:
        new.cost_ceiling_acknowledged = p.cost_ceiling_acknowledged
    if p.default_paranoid_install is not None:
        new.default_paranoid_install = p.default_paranoid_install
    if p.sound_enabled is not None:
        new.sound_enabled = p.sound_enabled
    if p.tier_overrides is not None:
        # Filter empty strings (= clear that override).
        cleaned: dict[str, dict[str, str]] = {}
        for prov, ladder in p.tier_overrides.items():
            inner = {t: gid for t, gid in ladder.items() if gid and gid.strip()}
            if inner:
                cleaned[prov] = inner
        new.tier_overrides = cleaned
    if p.max_concurrent_browsers is not None:
        n = int(p.max_concurrent_browsers)
        new.max_concurrent_browsers = n if n > 0 else 4
    if p.permission_tier is not None:
        new.permission_tier = p.permission_tier
        # Any explicit save of the tier counts as the operator
        # acknowledging the onboarding step.
        new.permission_tier_acknowledged = True
    if p.permission_tier_acknowledged is not None:
        new.permission_tier_acknowledged = p.permission_tier_acknowledged
    if p.workspace_dir is not None:
        new.workspace_dir = p.workspace_dir.strip() or None
    if p.workspace_dir_acknowledged is not None:
        new.workspace_dir_acknowledged = p.workspace_dir_acknowledged
    return new
