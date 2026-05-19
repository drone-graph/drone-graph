"""``cm_authenticated_browser`` — drive the authenticated Chrome profile via CDP.

This tool has **NO ``profile`` parameter** — the profile path is resolved
server-side from ``settings.json``, never exposed to the AI. Each call goes
through a confirmation gate, then opens a dedicated tab in the shared
authenticated Chrome instance.

Tab isolation
-------------
Every tool call opens a new ``page`` via ``context.new_page()``. The drone
interacts only with that single tab. No ``list_pages`` or ``switch_tab``
functionality is exposed — the drone cannot see or interact with other tabs.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from drone_graph.api.settings import load_settings
from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (
    AuthenticatedChrome,
)
from drone_graph.tools.builtins.browser.authenticated.config import (
    load_config,
)
from drone_graph.tools.builtins.browser.authenticated.confirmation import (
    require_confirmation,
)
from drone_graph.tools.registry import register_tool, ToolResult, DroneContext


# ── Screenshot directory (same convention as session.py) ──────────────
def _screenshot_dir() -> Path:
    """Return (and lazily create) the shared browser-screenshots temp directory."""
    d = Path(tempfile.gettempdir()) / "drone-graph-browser-screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d

# ---- Action handlers (same semantics as cm_browser but operate on a single
#      page from the authenticated Chrome) --------------------------------


def _result(ok: bool, **fields: Any) -> str:
    payload: dict[str, Any] = {"ok": ok, **fields}
    return json.dumps(payload)


def _action_open_url(page: Any, args: dict[str, Any]) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        return _result(False, error="url required")
    page.goto(url, wait_until="domcontentloaded")
    return _result(True, url=page.url, title=page.title() or "")


def _action_screenshot(page: Any, args: dict[str, Any]) -> str:
    label = str(args.get("label", "authenticated_screenshot"))
    # Save to a temp path and return the path.
    path = _screenshot_dir() / f"{label}_{id(page)}.png"
    page.screenshot(path=str(path))
    return _result(True, path=str(path), url=page.url, title=page.title() or "")


def _action_click(page: Any, args: dict[str, Any]) -> str:
    selector = str(args.get("selector", "")).strip()
    if not selector:
        return _result(False, error="selector required")
    page.click(selector, timeout=15000)
    return _result(True)


def _action_type(page: Any, args: dict[str, Any]) -> str:
    selector = str(args.get("selector", "")).strip()
    text = args.get("text", "")
    if not selector:
        return _result(False, error="selector required")
    page.fill(selector, str(text), timeout=15000)
    if args.get("submit"):
        page.press(selector, "Enter")
    return _result(True)


def _action_press(page: Any, args: dict[str, Any]) -> str:
    key = str(args.get("key", "")).strip()
    if not key:
        return _result(False, error="key required")
    selector = args.get("selector")
    if selector:
        page.press(str(selector), key)
    else:
        page.keyboard.press(key)
    return _result(True)


def _action_fill_form(page: Any, args: dict[str, Any]) -> str:
    raw_fields = args.get("fields") or []
    if not isinstance(raw_fields, list) or not raw_fields:
        return _result(False, error="fields[] required")
    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        sel = str(f.get("selector", "")).strip()
        val = str(f.get("value", ""))
        if not sel:
            continue
        page.fill(sel, val, timeout=15000)
    if args.get("submit_selector"):
        page.click(str(args["submit_selector"]), timeout=15000)
    return _result(True, fields_filled=len(raw_fields))


def _action_select_option(page: Any, args: dict[str, Any]) -> str:
    selector = str(args.get("selector", "")).strip()
    if not selector:
        return _result(False, error="selector required")
    value = args.get("value")
    label = args.get("label")
    if value is not None:
        page.select_option(selector, value=str(value))
    elif label is not None:
        page.select_option(selector, label=str(label))
    else:
        return _result(False, error="value or label required")
    return _result(True)


def _action_scroll(page: Any, args: dict[str, Any]) -> str:
    selector = args.get("selector")
    if selector:
        page.locator(str(selector)).scroll_into_view_if_needed(timeout=15000)
    else:
        dx = int(args.get("dx", 0) or 0)
        dy = int(args.get("dy", 0) or 0)
        page.mouse.wheel(dx, dy)
    return _result(True)


def _action_wait_for(page: Any, args: dict[str, Any]) -> str:
    timeout = float(args.get("timeout_s", 15)) * 1000
    if "selector" in args and args["selector"]:
        page.wait_for_selector(str(args["selector"]), timeout=timeout)
        return _result(True, matched="selector")
    if "url" in args and args["url"]:
        page.wait_for_url(str(args["url"]), timeout=timeout)
        return _result(True, matched="url")
    return _result(False, error="selector or url required")


def _action_extract_text(page: Any, args: dict[str, Any]) -> str:
    selector = args.get("selector")
    if selector:
        text = page.locator(str(selector)).inner_text(timeout=15000)
    else:
        text = page.evaluate("() => document.body.innerText")
    text = str(text or "")[:8000]
    return _result(True, text=text, url=page.url, title=page.title() or "")


def _action_evaluate(page: Any, args: dict[str, Any]) -> str:
    script = str(args.get("script", "")).strip()
    if not script:
        return _result(False, error="script required")
    result = page.evaluate(script)
    if isinstance(result, str):
        result = result[:12000]
    elif isinstance(result, list):
        result = result[:500]
    return _result(True, result=result)


def _action_close(page: Any, args: dict[str, Any]) -> str:  # noqa: ARG001
    try:
        page.close()
    except Exception:
        pass
    return _result(True, closed=True)


# ---- Dispatcher table -------------------------------------------------------


_ACTIONS = {
    "open_url": _action_open_url,
    "screenshot": _action_screenshot,
    "click": _action_click,
    "type": _action_type,
    "press": _action_press,
    "fill_form": _action_fill_form,
    "select_option": _action_select_option,
    "scroll": _action_scroll,
    "wait_for": _action_wait_for,
    "extract_text": _action_extract_text,
    "evaluate": _action_evaluate,
    "close": _action_close,
}


# ---- Registered tool --------------------------------------------------------


@register_tool(
    "cm_authenticated_browser",
    (
        "Drive the authenticated Chrome profile. "
        "Only for tasks needing a logged-in Google/Gmail session. "
        "No profile parameter needed — the backend resolves the profile "
        "automatically. "
        "Use cm_check_auth_profile first to see if a profile is configured. "
        "If none exists, create the account manually via cm_browser. "
        "Before every action the operator must confirm. "
        "Do NOT ask for or specify any profile name — the system handles this. "
        "Actions: open_url, screenshot, click, type, press, fill_form, "
        "select_option, scroll, wait_for, extract_text, evaluate, close."
    ),
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "open_url",
                    "screenshot",
                    "click",
                    "type",
                    "press",
                    "fill_form",
                    "select_option",
                    "scroll",
                    "wait_for",
                    "extract_text",
                    "evaluate",
                    "close",
                ],
                "description": "Which browser verb to run.",
            },
            "url": {"type": "string", "description": "open_url / wait_for(url) target."},
            "selector": {
                "type": "string",
                "description": (
                    "CSS selector for click/type/wait_for/extract_text/scroll."
                ),
            },
            "text": {"type": "string", "description": "Text to type."},
            "submit": {
                "type": "boolean",
                "description": "type: press Enter after filling.",
            },
            "key": {"type": "string", "description": "press: key name (Enter, Tab…)."},
            "fields": {
                "type": "array",
                "description": "fill_form: [{selector, value}, ...]",
                "items": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["selector", "value"],
                },
            },
            "submit_selector": {
                "type": "string",
                "description": (
                    "fill_form: optional submit button selector to click after."
                ),
            },
            "value": {
                "type": "string",
                "description": "select_option: option value.",
            },
            "label": {
                "type": "string",
                "description": "select_option: option label.",
            },
            "dx": {"type": "integer", "description": "scroll: pixels right."},
            "dy": {"type": "integer", "description": "scroll: pixels down."},
            "timeout_s": {
                "type": "number",
                "description": "wait_for: max seconds.",
            },
            "script": {
                "type": "string",
                "description": (
                    "evaluate: JavaScript expression or function body. "
                    "Return value must be JSON-serialisable."
                ),
            },
        },
        "required": ["action"],
    },
)
def cm_authenticated_browser(
    args: dict[str, Any], ctx: DroneContext
) -> ToolResult:
    """Dispatcher for the authenticated Chrome profile lane.

    Flow:
    1. Load settings → check profile path exists.
    2. Load config (domains, port, chrome path).
    3. Require operator confirmation.
    4. Ensure Chrome is running with the stored profile.
    5. Connect via CDP and create a new page (tab isolation).
    6. Perform the requested action.
    7. Return the result.
    """
    action = str(args.get("action", "")).strip()
    if not action:
        return ToolResult(content=_result(False, error="action required"))
    if action not in _ACTIONS:
        return ToolResult(
            content=_result(
                False,
                error=f"unknown action {action!r}",
                actions=sorted(_ACTIONS),
            )
        )

    # ---- Step 1: Load settings and verify profile path -----------------------
    settings = load_settings()
    if not settings.authenticated_chrome_profile_path:
        return ToolResult(
            content=_result(
                False,
                error=(
                    "No authenticated Chrome profile configured. "
                    "Use cm_check_auth_profile for status, then "
                    "create the account manually via cm_browser."
                ),
            )
        )

    profile_dir = Path(settings.authenticated_chrome_profile_path)
    if not profile_dir.is_dir():
        return ToolResult(
            content=_result(
                False,
                error=(
                    f"Authenticated profile directory does not exist: "
                    f"{profile_dir}. "
                    "Use the Settings UI to configure the profile path."
                ),
            )
        )

    # ---- Step 2: Load config ------------------------------------------------
    config = load_config()

    # ---- Step 3: Confirmation gate ------------------------------------------
    url_arg = args.get("url")
    decision = require_confirmation(
        action=action,
        url=url_arg,
        description=(
            f"Drone {ctx.drone_id[:8] if ctx.drone_id else '?'} wants to "
            f"{action} on the authenticated Chrome profile."
        ),
        drone_id=ctx.drone_id or "",
        gap_id=ctx.gap_id or "",
        signals=ctx.signals,
        tape=ctx.tape,
    )
    if not decision.approved:
        reason = decision.reason or "denied by operator"
        if decision.timed_out:
            reason = "operator did not confirm in time"
        return ToolResult(
            content=_result(False, error=f"Confirmation denied: {reason}")
        )

    # ---- Step 4: Ensure Chrome is running ------------------------------------
    try:
        browser = AuthenticatedChrome.ensure_running(
            config=config, profile_dir=profile_dir
        )
    except RuntimeError as e:
        return ToolResult(content=_result(False, error=str(e)))

    # ---- Step 5: Create a new page (tab isolation) --------------------------
    # Use the default browser context (shared across the profile).
    # Each call gets its own page so the drone cannot see other tabs.
    try:
        context = browser.contexts[0]  # single default context
        page = context.new_page()
    except Exception as e:
        return ToolResult(
            content=_result(
                False,
                error=f"Failed to create page: {type(e).__name__}: {e}",
            )
        )

    # ---- Step 6: Perform action ---------------------------------------------
    fn = _ACTIONS[action]
    try:
        out = fn(page, args)
    except Exception as e:
        # Clean up the page on error.
        try:
            page.close()
        except Exception:
            pass
        return ToolResult(
            content=_result(
                False,
                error=f"{type(e).__name__}: {e}",
            )
        )

    # For close action, the page was already closed in the handler.
    if action != "close":
        try:
            page.close()
        except Exception:
            pass

    return ToolResult(content=out)
