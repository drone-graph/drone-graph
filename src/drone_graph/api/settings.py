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
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

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
    # Identity firewall. When OFF (default), every drone runs with an
    # isolated identity — clean env, throwaway $HOME, no access to the
    # operator's gitconfig / ssh keys / browser profile / API creds. When
    # ON, Gap Finding can flag a gap with ``uses_operator_identity`` and
    # the operator approves it per-gap before dispatch. Flipping ON→OFF
    # only affects new dispatches; in-flight drones finish out.
    allow_operator_identity: bool = False
    # Tracks whether the operator has explicitly seen + answered the
    # identity step during onboarding. ``allow_operator_identity`` is a
    # binary state, so we need a separate ack flag to distinguish
    # "user said no" from "user hasn't seen the step yet."
    identity_acknowledged: bool = False
    # Strings to redact from any drone tool output. Auto-populated at
    # first server boot from ``~/.gitconfig`` (user.name, user.email) and
    # ``gh auth status`` (handle), plus hostname. The operator can edit
    # this list in Settings. Short tokens (<4 chars) are skipped at
    # redaction time to avoid false-positive matches on common words.
    identity_redaction_patterns: list[str] = Field(default_factory=list)
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
    # Master identity toggle. The scheduler reads this on every dispatch;
    # the runtime's redaction filter is independent (it always runs).
    if s.allow_operator_identity:
        os.environ["DRONE_GRAPH_ALLOW_OPERATOR_IDENTITY"] = "1"
    else:
        os.environ.pop("DRONE_GRAPH_ALLOW_OPERATOR_IDENTITY", None)
    # Redaction patterns flow into drone subprocesses via an env var.
    # We use ``\x1f`` (Unit Separator) as a delimiter — never appears in
    # plausible name/email/hostname strings and survives env transit.
    pats = [p.strip() for p in s.identity_redaction_patterns if p and p.strip()]
    if pats:
        os.environ["DRONE_GRAPH_REDACT_PATTERNS"] = "\x1f".join(pats)
    else:
        os.environ.pop("DRONE_GRAPH_REDACT_PATTERNS", None)


def has_any_key(s: Settings) -> bool:
    return bool(
        (s.anthropic_api_key and s.anthropic_api_key.strip())
        or (s.openai_api_key and s.openai_api_key.strip())
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


def detect_identity_patterns() -> list[str]:
    """Read the operator's identity hints from the host system.

    Pulled once at first boot so the drone-side redaction filter has
    something to mask. Looks at ``git config`` (user.name, user.email),
    ``gh auth status`` (login handle), hostname, and OS username. The
    operator can edit the resulting list in Settings.

    Returns deduped strings; short ones (<4 chars) are still included so
    the operator can keep or remove them — the *redaction* pass is what
    filters by length to avoid common-word false positives.
    """
    import getpass
    import platform
    import re
    import subprocess

    seen: set[str] = set()
    out: list[str] = []

    def add(s: str | None) -> None:
        if not s:
            return
        ss = s.strip()
        if not ss or ss in seen:
            return
        seen.add(ss)
        out.append(ss)

    for key in ("user.name", "user.email"):
        try:
            r = subprocess.run(
                ["git", "config", "--global", "--get", key],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                add(r.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    try:
        r = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=3,
        )
        # Example output line: "  ✓ Logged in to github.com as danporder (..."
        m = re.search(r"Logged in to \S+ as (\S+)", r.stdout + r.stderr)
        if m:
            add(m.group(1))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    try:
        add(platform.node())
    except Exception:  # noqa: BLE001
        pass

    try:
        add(getpass.getuser())
    except Exception:  # noqa: BLE001
        pass

    return out


def ensure_identity_patterns_seeded(s: Settings) -> Settings:
    """Populate ``identity_redaction_patterns`` on first run.

    If the operator has already explicitly cleared the list to ``[]``,
    we won't overwrite — they've opted out. We only seed when the field
    is its factory default (empty + the settings file hasn't been saved
    yet, signalled by the file not existing on disk).
    """
    if s.identity_redaction_patterns:
        return s
    if settings_path().exists():
        return s
    detected = detect_identity_patterns()
    if detected:
        s.identity_redaction_patterns = detected
    return s


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
    allow_operator_identity: bool = False
    identity_acknowledged: bool = False
    identity_redaction_patterns: list[str] = Field(default_factory=list)
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
        allow_operator_identity=s.allow_operator_identity,
        identity_acknowledged=s.identity_acknowledged,
        identity_redaction_patterns=list(s.identity_redaction_patterns),
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
    allow_operator_identity: bool | None = None
    identity_acknowledged: bool | None = None
    identity_redaction_patterns: list[str] | None = None


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
    if p.allow_operator_identity is not None:
        new.allow_operator_identity = p.allow_operator_identity
        # Any explicit save of this field counts as acknowledgement, so
        # the onboarding step doesn't reappear when the user later flips
        # it from Settings.
        new.identity_acknowledged = True
    if p.identity_acknowledged is not None:
        new.identity_acknowledged = p.identity_acknowledged
    if p.identity_redaction_patterns is not None:
        # Dedupe + drop empties; preserve operator-typed casing.
        seen: set[str] = set()
        out: list[str] = []
        for s in p.identity_redaction_patterns:
            ss = (s or "").strip()
            if not ss or ss in seen:
                continue
            seen.add(ss)
            out.append(ss)
        new.identity_redaction_patterns = out
    return new
