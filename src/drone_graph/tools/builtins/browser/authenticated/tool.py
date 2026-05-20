"""``cm_browser`` — drive the real Chrome browser via CDP.

HARD REQUIREMENT
----------------
This tool is registered **only** when a Chrome profile directory has been
configured in Settings (``chrome_profile_dir`` in ``settings.json``).  If no
profile is configured, the tool simply does not exist in the registry — drones
cannot call it, period.  No fallback, no workaround.

The profile path is resolved server-side from ``settings.json``, never exposed
to the AI.  Each call goes through a confirmation gate, then opens a dedicated
tab in the shared Chrome instance.

Tab persistence
---------------
Each drone gets its own **persistent** page (tab) that lives across multiple
consecutive ``cm_browser`` calls. The drone can navigate → type → click →
screenshot in sequence without losing state. The page is only destroyed when
the drone explicitly calls ``action=close`` or when the drone exits (triggered
by ``cleanup_for_drone`` in the runtime).

This means multiple drones can each have their own tab in the same Chrome
window, operating independently.
"""

from __future__ import annotations

import sys

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from drone_graph.gaps.records import Finding, FindingKind, FindingAuthor
from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (
    AuthenticatedChrome,
)
from drone_graph.tools.builtins.browser.authenticated.config import (
    load_config,
)
from drone_graph.tools.builtins.browser.authenticated.confirmation import (
    require_confirmation,
)
from drone_graph.tools.builtins.browser.notifications import notify
from drone_graph.tools.registry import register_tool, ToolResult, DroneContext

# When no explicit timeout_s is given for await_operator
_DEFAULT_AWAIT_OPERATOR_TIMEOUT_S = 300.0

# Module-level reference to the current DroneContext so that action
# handlers (e.g. await_operator) can access context without changing
# their signature. Set by the dispatcher before each action call.
_CURRENT_CTX: DroneContext | None = None

# Per-drone persistent page tracking.
# Each drone gets its own page (tab) that persists across consecutive
# cm_browser calls. The page is only destroyed on explicit "close"
# action or when the drone exits (cleanup_for_drone).
# Structure: {drone_id: {"page": Page, "browser": Browser}}
_DRONE_PAGES: dict[str, dict[str, Any]] = {}


# ── Screenshot directory (same convention as session.py) ──────────────
def _screenshot_dir() -> Path:
    """Return (and lazily create) the shared browser-screenshots temp directory."""
    d = Path(tempfile.gettempdir()) / "drone-graph-browser-screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- Action handlers (operate on a single page from the Chrome instance) -


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
    label = str(args.get("label", "screenshot"))
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


def _action_await_operator(page: Any, args: dict[str, Any]) -> str:  # noqa: ARG001
    """Block until the operator chats with this drone, or timeout / cancel.

    Writes a ``chat_with_drone`` finding so the operator's UI knows what
    the drone is asking for. Returns the operator's reply.
    """
    prompt = str(args.get("prompt", "")).strip() or "(awaiting operator input)"
    timeout = float(args.get("timeout_s", _DEFAULT_AWAIT_OPERATOR_TIMEOUT_S))

    # Access DroneContext from the closure — it's set in the dispatcher.
    ctx = _CURRENT_CTX
    if ctx is None or ctx.store is None:
        return _result(False, error="no context / store available for await_operator")

    # Post a finding asking for input.
    ask = ctx.store.append_finding(
        tick=ctx.tick,
        author=FindingAuthor.worker,
        kind=FindingKind.chat_with_drone,
        summary=prompt,
        affected_gap_ids=[ctx.gap_id],
    )
    notify("Drone needs you", prompt[:140])
    if ctx.tape is not None:
        ctx.tape.emit(
            "browser.await_operator",
            drone_id=ctx.drone_id,
            gap_id=ctx.gap_id,
            prompt=prompt,
            ask_finding_id=ask.id,
        )

    deadline = time.monotonic() + timeout
    since = ctx.tick
    while time.monotonic() < deadline:
        # Cancellation check
        if ctx.signals is not None and ctx.signals.is_cancelled("gap", ctx.gap_id):
            return _result(False, cancelled=True)
        # Poll for operator reply
        try:
            findings = ctx.store.recent_findings(limit=50)
        except Exception:
            time.sleep(1)
            continue
        target = ctx.gap_id
        for f in reversed(findings):
            if f.tick <= since:
                continue
            if f.kind != FindingKind.chat_with_drone:
                continue
            if f.author != FindingAuthor.user:
                continue
            if target not in (f.affected_gap_ids or []):
                continue
            # Found a reply!
            if ctx.tape is not None:
                ctx.tape.emit(
                    "browser.operator_replied",
                    drone_id=ctx.drone_id,
                    gap_id=ctx.gap_id,
                    reply=f.summary,
                    finding_id=f.id,
                )
            return _result(True, reply=f.summary, finding_id=f.id)
        time.sleep(2)

    return _result(False, error="Operator did not respond in time", timed_out=True)


def _action_register_profile(page: Any, args: dict[str, Any]) -> str:  # noqa: ARG001
    """Advertise a capability for this browser profile.

    Writes metadata to the profile's metadata.json so future drones can
    discover this capability via service-based filtering.
    """
    summary = str(args.get("summary", "")).strip()
    services = args.get("services")
    if not isinstance(services, list):
        services = []
    if not summary:
        return _result(False, error="summary required for register_profile")

    from drone_graph.tools.builtins.browser.profiles import (
        save_profile_metadata,
        profiles_root,
    )

    # Use a well-known name for the profile's metadata.
    meta_name = "__drone_graph__"
    meta_path = profiles_root() / meta_name
    meta_path.mkdir(parents=True, exist_ok=True)
    data = save_profile_metadata(meta_name, {"summary": summary, "services": services})
    return _result(
        True,
        summary=summary,
        services=services,
        registered_name=meta_name,
        registered_tool_name=(
            f"profile-{meta_name}"
        ),
    )


def _action_list_pages(page: Any, args: dict[str, Any]) -> str:  # noqa: ARG001
    """List all open pages/popups for the current gap in the shared browser."""
    from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import AuthenticatedChrome
    browser = AuthenticatedChrome.connect()
    context = browser.contexts[0]
    pages = context.pages
    lines = []
    for i, p in enumerate(pages):
        try:
            lines.append(f"[{i}] {p.url} — title: {p.title()}")
        except Exception:
            lines.append(f"[{i}] <error reading page>")
    return "\n".join(lines) if lines else "(no open pages)"


def _action_switch_page(page: Any, args: dict[str, Any]) -> str:  # noqa: ARG001
    """Switch the current page to a different open page by index.

    Args:
        index (int): The index of the page to switch to (from list_pages).
    """
    from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import AuthenticatedChrome
    target_idx = int(args.get("index", 0))
    browser = AuthenticatedChrome.connect()
    context = browser.contexts[0]
    pages = context.pages
    if target_idx < 0 or target_idx >= len(pages):
        return f"Error: index {target_idx} out of range (0-{len(pages)-1})"
    new_page = pages[target_idx]
    # Update the global drone page registry
    import drone_graph.tools.builtins.browser.authenticated.tool as _self
    drone_id = args.get("_drone_id", "")
    _self._DRONE_PAGES[drone_id] = {"page": new_page, "browser": browser}
    return f"Switched to page [{target_idx}]: {new_page.url}"


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
    "await_operator": _action_await_operator,
    "register_profile": _action_register_profile,
    "list_pages": _action_list_pages,
    "switch_page": _action_switch_page,
}


# ---- Cleanup hook (called by runtime when a drone exits) --------------------


def cleanup_for_drone(drone_id: str) -> None:
    """Release the drone's page reference **without closing the page**.

    Called by ``runtime.py`` when a drone exits. The page stays open in
    Chrome so a successor drone for the same gap (spawned in a new
    subprocess) can find and reuse it via the shared ``page_ledger``.

    Cleanup of pages for filled / retired gaps is handled by the scheduler's
    ``_reap_orphan_pages()`` tick.
    """
    _DRONE_PAGES.pop(drone_id, None)
    # Intentionally NOT closing the page — it persists for the next drone.


# ---- Tool metadata (used by _ensure_registered) -----------------------------


_DESCRIPTION = (
    "Drive the real Chrome browser (not automated Playwright). "
    "No profile parameter needed — the backend resolves the Chrome profile "
    "automatically from settings. "
    "Use cm_check_browser first to see if a profile is configured. "
    "If none exists, the operator must configure one in Settings. "
    "Do NOT ask for or specify any profile name — the system handles this. "
    "Available actions: open_url, screenshot, click, type, press, fill_form, "
    "select_option, scroll, wait_for, extract_text, evaluate, close, "
    "await_operator, register_profile, list_pages, switch_page. "
    "Use list_pages to enumerate all open windows (including OAuth popups). "
    "Use switch_page to select a different page by index."
)

_INPUT_SCHEMA: dict[str, Any] = {
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
                "await_operator",
                "register_profile",
                "list_pages",
                "switch_page",
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
        "index": {
            "type": "integer",
            "description": "switch_page: index of the page to switch to (from list_pages).",
        },
    },
    "required": ["action"],
}


# ---- cm_browser dispatcher --------------------------------------------------


def cm_browser(
    args: dict[str, Any], ctx: DroneContext
) -> ToolResult:
    """Dispatcher for the Chrome browser lane.

    Flow:
    1. Load settings — check profile path exists (hard error if missing).
    2. Load config (port, chrome path).
    3. Require operator confirmation.
    4. Ensure Chrome is running with the stored profile (hard error if fails).
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
    from drone_graph.api.settings import load_settings  # lazy: break circular import

    settings = load_settings()
    if not settings.chrome_profile_dir:
        return ToolResult(
            content=_result(
                False,
                error=(
                    "FATAL: No Chrome profile configured. "
                    "cm_browser should not be reachable without a profile. "
                    "This is a system misconfiguration."
                ),
            ),
            terminal_finding=Finding(
                tick=ctx.tick,
                author=FindingAuthor.worker,
                kind=FindingKind.fail,
                summary=(
                    "Chrome profile not configured — cm_browser cannot operate. "
                    "Configure a Chrome profile in Settings and restart the swarm."
                ),
                affected_gap_ids=[ctx.gap_id],
            ),
            outcome="fail",
        )

    profile_dir = Path(settings.chrome_profile_dir)
    if not profile_dir.is_dir():
        return ToolResult(
            content=_result(
                False,
                error=(
                    f"FATAL: Chrome profile directory does not exist: "
                    f"{profile_dir}. "
                    "cm_browser should not be reachable without a valid profile. "
                    "This is a system misconfiguration."
                ),
            ),
            terminal_finding=Finding(
                tick=ctx.tick,
                author=FindingAuthor.worker,
                kind=FindingKind.fail,
                summary=(
                    f"Chrome profile directory not found: {profile_dir}. "
                    "Fix the profile path in Settings and restart the swarm."
                ),
                affected_gap_ids=[ctx.gap_id],
            ),
            outcome="fail",
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
            f"{action} on the Chrome browser."
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

    # ---- Step 4: Ensure Chrome is running -----------------------------------
    try:
        browser = AuthenticatedChrome.ensure_running(
            config=config, profile_dir=profile_dir
        )
    except RuntimeError as e:
        return ToolResult(
            content=_result(
                False,
                error=(
                    f"FATAL: Chrome failed to start — {e}. "
                    "The operator must fix the Chrome configuration and restart."
                ),
            ),
            terminal_finding=Finding(
                tick=ctx.tick,
                author=FindingAuthor.worker,
                kind=FindingKind.fail,
                summary=(
                    f"Chrome browser failed to start: {e}. "
                    "The operator must fix the Chrome configuration and restart."
                ),
                affected_gap_ids=[ctx.gap_id],
            ),
            outcome="fail",
        )

    # ---- Step 5: Get or create a per-drone persistent page -------------------
    # Each drone keeps its own page across consecutive calls so it can do
    # multi-step workflows (navigate → type → click → screenshot) without
    # losing state. The page is only destroyed on explicit "close" action
    # or when the drone exits (cleanup_for_drone).
    #
    # If this is a new subprocess for an existing gap, we first check the
    # shared page_ledger — a previous drone may have left a tab open.
    drone_id = ctx.drone_id or ""
    existing = _DRONE_PAGES.get(drone_id)
    page: Any = None
    if existing is not None:
        # Verify the cached page is still alive.
        candidate = existing["page"]
        try:
            _ = candidate.url  # lightweight property access
            page = candidate
        except Exception:
            # Page was closed or crashed — discard and create a new one.
            _DRONE_PAGES.pop(drone_id, None)

    if page is None:
        # Check the shared page ledger — a predecessor drone for this gap
        # may have left a tab open after hitting max_turns.
        gap_id = ctx.gap_id or ""
        if gap_id:
            try:
                from .page_ledger import lookup_page

                page = lookup_page(gap_id, browser)
                if page is not None:
                    _DRONE_PAGES[drone_id] = {"page": page, "browser": browser}
            except Exception:
                pass

    if page is None:
        try:
            context = browser.contexts[0]  # single default context
            page = context.new_page()
            _DRONE_PAGES[drone_id] = {"page": page, "browser": browser}
            # Register this page in the shared ledger so successor drones
            # for the same gap can find and reuse it.
            gap_id = ctx.gap_id or ""
            if gap_id:
                try:
                    from .page_ledger import register_page

                    register_page(gap_id, drone_id, page)
                except Exception:
                    pass
        except Exception as e:
            return ToolResult(
                content=_result(
                    False,
                    error=f"Failed to create page: {type(e).__name__}: {e}",
                )
            )

    # ---- Step 6: Perform action ---------------------------------------------
    global _CURRENT_CTX
    _CURRENT_CTX = ctx
    fn = _ACTIONS[action]
    args["_drone_id"] = drone_id
    try:
        out = fn(page, args)
    except Exception as e:
        # On error, discard the drone's page so a fresh one is created next call.
        _DRONE_PAGES.pop(drone_id, None)
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
    finally:
        _CURRENT_CTX = None

    # For close action, the page was already closed in the handler.
    # Remove it from our tracking so a new page is created next call.
    if action == "close":
        _DRONE_PAGES.pop(drone_id, None)

    # NOTE: For non-close actions the page stays alive in _DRONE_PAGES
    # so the drone can continue multi-step workflows. The page is only
    # destroyed when the drone calls "close" or when cleanup_for_drone
    # runs on drone exit.

    return ToolResult(content=out)


# ---- Conditional registration -----------------------------------------------
# cm_browser is registered ONLY when a Chrome profile is configured in
# settings.json.  If no profile is configured, the tool simply doesn't exist
# — drones cannot use any browser at all.  No fallback, no workaround.
#
# This check runs at module import time.  If the operator configures the
# profile later, they must restart the swarm for the tool to appear.
# ------------------------------------------------------------------------


def _ensure_registered() -> None:
    """Register ``cm_browser`` unconditionally.

    Runtime checks inside ``cm_browser`` produce hard terminal errors
    (``outcome="fail"`` with a ``terminal_finding``) if no Chrome profile
    is configured or if the browser cannot start, so the drone is
    terminated immediately — no fallback, no workaround.
    """
    register_tool("cm_browser", _DESCRIPTION, _INPUT_SCHEMA)(cm_browser)


_ensure_registered()
