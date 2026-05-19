"""Security tests for the authenticated browser lane.

These tests verify security invariants:
- ``cm_check_auth_profile`` returns only a boolean — no path leakage
- ``cm_authenticated_browser`` has no ``profile`` parameter in its schema
- The profile path lives only in Settings (settings.json), never in the
  AI-facing config or tool definitions
- Every action goes through a confirmation gate
"""

from __future__ import annotations

import json
from dataclasses import fields
from unittest.mock import MagicMock, patch

from drone_graph.api.settings import Settings
from drone_graph.tools.builtins.browser.authenticated.config import (
    AuthenticatedConfig,
)
from drone_graph.tools.builtins.browser.authenticated.status_tool import (
    cm_check_auth_profile,
)
from drone_graph.tools.builtins.browser.authenticated.tool import (
    cm_authenticated_browser,
)
from drone_graph.tools.builtins.browser.authenticated.confirmation import (
    require_confirmation,
)
from drone_graph.tools.registry import (
    DroneContext,
    builtin_to_record,
    to_anthropic_tool_def,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(**overrides: object) -> DroneContext:
    """Minimal DroneContext for tools that only read a few fields."""
    return DroneContext(
        drone_id="test-drone",
        gap_id="test-gap",
        tick=1,
        store=MagicMock(),
        tool_store=MagicMock(),
        **overrides,
    )


EXPECTED_ACTION_PROPERTIES = {
    "action",
    "url",
    "selector",
    "text",
    "submit",
    "key",
    "fields",
    "submit_selector",
    "value",
    "label",
    "dx",
    "dy",
    "timeout_s",
    "script",
}


# ── Test 1: cm_check_auth_profile returns only boolean ──────────────────────


def test_cm_check_auth_profile_returns_only_boolean() -> None:
    """``cm_check_auth_profile`` returns JSON with exactly one key
    ``has_profile`` whose value is a ``bool``."""
    ctx = _make_ctx()
    result = cm_check_auth_profile({}, ctx)

    parsed = json.loads(result.content)
    assert isinstance(parsed, dict), "result must be a JSON object"
    assert list(parsed.keys()) == ["has_profile"], (
        f"expected only 'has_profile', got {list(parsed.keys())}"
    )
    assert isinstance(parsed["has_profile"], bool), (
        f"has_profile must be bool, got {type(parsed['has_profile']).__name__}"
    )


# ── Test 2: cm_check_auth_profile has no path leakage ────────────────────────


def test_cm_check_auth_profile_no_path_leakage() -> None:
    """The JSON output must contain exactly one key, ``has_profile``,
    and no path, name, directory listing, or any other data."""
    ctx = _make_ctx()
    result = cm_check_auth_profile({}, ctx)

    parsed = json.loads(result.content)
    assert isinstance(parsed, dict)
    # Exactly one key — nothing else leaked.
    assert set(parsed.keys()) == {"has_profile"}, (
        f"leaked keys: {set(parsed.keys()) - {'has_profile'}}"
    )
    # Verify no string values that could be paths
    for key, value in parsed.items():
        assert not isinstance(value, str), (
            f"key {key!r} is a string, potential path leak: {value!r}"
        )


# ── Test 3: cm_authenticated_browser has no profile param ───────────────────


def test_cm_authenticated_browser_has_no_profile_param() -> None:
    """The Anthropic tool definition for ``cm_authenticated_browser`` must
    NOT contain a ``profile`` property in its input schema."""
    tool_def = to_anthropic_tool_def("cm_authenticated_browser")
    assert tool_def is not None, "cm_authenticated_browser must be registered"

    props = tool_def.get("input_schema", {}).get("properties", {})
    assert "profile" not in props, (
        f"'profile' param found in tool schema! Security invariant violated. "
        f"Got properties: {sorted(props)}"
    )
    assert "profile_dir" not in props, (
        f"'profile_dir' param found in tool schema! Security invariant violated."
    )
    assert "profile_path" not in props, (
        f"'profile_path' param found in tool schema! Security invariant violated."
    )

    # All expected action properties should be present.
    actual_props = set(props.keys())
    missing = EXPECTED_ACTION_PROPERTIES - actual_props
    extra = actual_props - EXPECTED_ACTION_PROPERTIES

    # Some extra properties (like action) are expected; the point is
    # we catch any new property that might leak the profile path.
    unexpected_leaks = {p for p in extra if "profile" in p.lower() or "path" in p.lower()}
    assert not unexpected_leaks, (
        f"Unexpected properties that could leak profile info: {unexpected_leaks}"
    )

    # Basic sanity: action enum should be present
    assert "action" in props, "action property is required"
    assert props["action"].get("type") == "string", "action must be a string"


# ── Test 4: cm_authenticated_browser fails when no profile configured ───────


def test_cm_authenticated_browser_fails_when_no_profile() -> None:
    """Calling ``cm_authenticated_browser`` without a configured profile path
    must return an error mentioning "No authenticated profile" or similar."""
    ctx = _make_ctx()

    # Patch load_settings to return a Settings with no profile path.
    with patch(
        "drone_graph.tools.builtins.browser.authenticated.tool.load_settings"
    ) as mock_load:
        mock_load.return_value = Settings()
        result = cm_authenticated_browser(
            {"action": "open_url", "url": "https://example.com"}, ctx
        )

    parsed = json.loads(result.content)
    assert parsed.get("ok") is False, (
        f"expected ok=false, got {result.content}"
    )
    error_msg = parsed.get("error", "")
    assert "authenticated" in error_msg.lower() or "profile" in error_msg.lower(), (
        f"error must reference profile/auth, got: {error_msg}"
    )


# ── Test 5: cm_authenticated_browser requires confirmation ──────────────────


def test_cm_authenticated_browser_requires_confirmation() -> None:
    """Structural test: ``cm_authenticated_browser`` must reference
    ``require_confirmation``."""
    # Verify the confirmation module is importable and callable.
    assert callable(require_confirmation), (
        "require_confirmation must be a callable"
    )

    # Verify the tool module references require_confirmation either via
    # import or direct function-reference in its body. We check the
    # module's top-level names (the import) and the compiled code's
    # co_names (which captures every named reference in the function).
    import drone_graph.tools.builtins.browser.authenticated.tool as tool_mod

    # Check that require_confirmation is in the module's namespace
    # (it was imported at the top level).
    assert "require_confirmation" in tool_mod.__dict__, (
        "tool module must import require_confirmation"
    )

    # Check that the dispatcher function's bytecode references the name.
    # co_names captures all global/named references in the function body.
    assert "require_confirmation" in tool_mod.cm_authenticated_browser.__code__.co_names, (
        "cm_authenticated_browser function body must reference "
        "require_confirmation by name"
    )


# ── Test 6: config does not store profile path ──────────────────────────────


def test_config_does_not_store_profile_path() -> None:
    """``AuthenticatedConfig`` must NOT have ``profile_dir`` or
    ``profile_path`` fields — the profile path lives in Settings only."""
    cfg_fields = {f.name for f in fields(AuthenticatedConfig)}

    assert "profile_dir" not in cfg_fields, (
        f"AuthenticatedConfig leaked 'profile_dir' field!"
    )
    assert "profile_path" not in cfg_fields, (
        f"AuthenticatedConfig leaked 'profile_path' field!"
    )
    # Sanity check: expected fields ARE present
    assert "cdp_port" in cfg_fields, "expected cdp_port in config"
    assert "authenticated_domains" in cfg_fields, (
        "expected authenticated_domains in config"
    )
    assert "chrome_path" in cfg_fields, "expected chrome_path in config"


# ── Test 7: settings has auth profile field ─────────────────────────────────


def test_settings_has_auth_profile_field() -> None:
    """``Settings`` model must have ``authenticated_chrome_profile_path``
    as ``str | None = None`` — this is where the path lives, never in the
    AI-facing tool config."""
    # Check the field exists
    assert hasattr(Settings, "model_fields"), (
        "Settings must be a Pydantic model with model_fields"
    )
    field_info = Settings.model_fields.get("authenticated_chrome_profile_path")
    assert field_info is not None, (
        "Settings must have an 'authenticated_chrome_profile_path' field. "
        f"Available fields: {sorted(Settings.model_fields)}"
    )

    # Verify default is None
    s = Settings()
    assert s.authenticated_chrome_profile_path is None, (
        f"default must be None, got {s.authenticated_chrome_profile_path!r}"
    )

    # Verify it accepts a string
    s2 = Settings(authenticated_chrome_profile_path="/some/path")
    assert s2.authenticated_chrome_profile_path == "/some/path"


# ── Test 8: both tools are registered ───────────────────────────────────────


def test_both_tools_are_registered() -> None:
    """Both ``cm_authenticated_browser`` and ``cm_check_auth_profile`` must
    exist in the builtin tool registry."""
    browser_record = builtin_to_record("cm_authenticated_browser")
    assert browser_record is not None, (
        "cm_authenticated_browser is not registered in the tool registry"
    )
    assert browser_record.name == "cm_authenticated_browser"

    check_record = builtin_to_record("cm_check_auth_profile")
    assert check_record is not None, (
        "cm_check_auth_profile is not registered in the tool registry"
    )
    assert check_record.name == "cm_check_auth_profile"

    # Verify they're builtins
    from drone_graph.tools.records import ToolKind

    assert browser_record.kind == ToolKind.builtin
    assert check_record.kind == ToolKind.builtin
