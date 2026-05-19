"""``cm_browser`` — single multiplexed tool for headed-Chromium computer-use.

A drone calls ``cm_browser(action=..., profile=..., ...)`` to drive a real
Chromium window. One tool with an ``action`` dispatcher (not many small
tools) so the model picks the right verb from a single description and the
schema stays compact.

Actions
-------

* ``open_url`` — navigate the profile's page to ``url``.
* ``screenshot`` — capture current page; result is an absolute file path.
* ``click`` — click an element by ``selector`` (CSS).
* ``type`` — type ``text`` into ``selector``. Use ``submit=true`` to press
  Enter after.
* ``press`` — press a single ``key`` (``Enter``, ``Tab``, ``Escape``…).
* ``fill_form`` — bulk fill: ``fields`` is ``[{selector, value}]``.
* ``select_option`` — pick ``value`` (or ``label``) in a ``<select>``.
* ``scroll`` — by ``dx``/``dy`` pixels or to ``selector``.
* ``navigate_back`` — page.go_back().
* ``wait_for`` — wait for ``selector`` to appear, or ``url`` to match.
* ``extract_text`` — return ``innerText`` of ``selector`` (or whole page).
* ``await_operator`` — pause the drone until the operator types into the
  drone-attached chat panel, or signals cancel. Returns the operator's
  message as the tool result so the drone can proceed.
* ``register_profile`` — declare that this profile now holds a usable
  capability (e.g. logged-in LinkedIn). Future drones discover via
  ``cm_list_tools``.
* ``evaluate`` — run arbitrary JavaScript on the page and return the result.
* ``close`` — close this profile's window. Releases the browser slot.

All actions are blocking. The slot is acquired on the first action of a
drone session and released on ``close`` or drone exit.
"""

from __future__ import annotations

import json
import time
from typing import Any

from drone_graph.gaps.records import FindingAuthor, FindingKind
from drone_graph.tools.builtins.browser.concurrency import (
    acquire_slot,
    heartbeat_slot,
    release_slot,
)
from drone_graph.tools.builtins.browser.notifications import notify
from drone_graph.tools.builtins.browser.profiles import (
    get_profile_services,
    profile_dir,
    registered_tool_name,
    set_profile_services,
)
from drone_graph.tools.builtins.browser.session import manager_for_drone
from drone_graph.tools.records import Tool, ToolKind
from drone_graph.tools.registry import DroneContext, ToolResult, register_tool

DEFAULT_WAIT_TIMEOUT_S = 15.0
DEFAULT_AWAIT_OPERATOR_TIMEOUT_S = 1800.0  # 30 min — long enough for OAuth + coffee
AWAIT_POLL_S = 1.5
SLOT_HEARTBEAT_EVERY_S = 60.0


# A per-drone slot ledger. Keyed by drone_id so a drone reuses the same slot
# across many tool calls (one slot per drone, not one per call). Lives in the
# drone subprocess; the underlying signals row outlives it via the TTL.
_DRONE_SLOT: dict[str, int] = {}
# Last time we heartbeat the slot for this drone. The signals layer reaps
# slots whose lease has expired; we renew lazily on each tool call.
_LAST_HEARTBEAT: dict[str, float] = {}


def _ensure_slot(ctx: DroneContext) -> int | str:
    """Return this drone's slot index, blocking to acquire if needed.

    Returns a string error message if signals is unavailable or the wait
    was cancelled. The caller propagates the message to the model.
    """
    if ctx.drone_id in _DRONE_SLOT:
        # Renew lease cheaply if it's been a while.
        now = time.monotonic()
        if now - _LAST_HEARTBEAT.get(ctx.drone_id, 0) > SLOT_HEARTBEAT_EVERY_S:
            if ctx.signals is not None:
                heartbeat_slot(ctx.signals, _DRONE_SLOT[ctx.drone_id], ctx.drone_id)
            _LAST_HEARTBEAT[ctx.drone_id] = now
        return _DRONE_SLOT[ctx.drone_id]
    if ctx.signals is None:
        # Single-process / unsupervised — no slot bookkeeping needed.
        _DRONE_SLOT[ctx.drone_id] = -1
        _LAST_HEARTBEAT[ctx.drone_id] = time.monotonic()
        return -1

    def cancelled() -> bool:
        return ctx.signals.is_cancelled("gap", ctx.gap_id)

    slot = acquire_slot(ctx.signals, ctx.drone_id, cancel_check=cancelled)
    if slot is None:
        return "ERROR: cancelled while waiting for a browser slot."
    if ctx.tape is not None:
        ctx.tape.emit(
            "browser.slot_acquired",
            drone_id=ctx.drone_id,
            gap_id=ctx.gap_id,
            slot=slot,
        )
    _DRONE_SLOT[ctx.drone_id] = slot
    _LAST_HEARTBEAT[ctx.drone_id] = time.monotonic()
    return slot


def _release_drone_slot(ctx: DroneContext) -> None:
    slot = _DRONE_SLOT.pop(ctx.drone_id, None)
    _LAST_HEARTBEAT.pop(ctx.drone_id, None)
    if slot is None or slot < 0 or ctx.signals is None:
        return
    release_slot(ctx.signals, slot, ctx.drone_id)
    if ctx.tape is not None:
        ctx.tape.emit(
            "browser.slot_released",
            drone_id=ctx.drone_id,
            gap_id=ctx.gap_id,
            slot=slot,
        )


# ---- Per-action helpers --------------------------------------------------


def _emit_state(ctx: DroneContext, profile: str, action: str, **extra: Any) -> None:
    """Push a ``browser.state`` event with a screenshot so the UI can keep
    the drone-attached chat panel showing the live page."""
    if ctx.tape is None:
        return
    try:
        mgr = manager_for_drone(ctx.drone_id)
        page = mgr.page(profile)
        path = mgr.screenshot(profile, label=action)
        ctx.tape.emit(
            "browser.state",
            drone_id=ctx.drone_id,
            gap_id=ctx.gap_id,
            profile=profile,
            url=page.url,
            title=page.title() or "",
            action=action,
            screenshot_path=str(path),
            **extra,
        )
    except Exception:
        pass


def _result(ok: bool, **fields: Any) -> str:
    payload: dict[str, Any] = {"ok": ok, **fields}
    return json.dumps(payload)


def _action_open_url(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        return _result(False, error="url required")
    mgr = manager_for_drone(ctx.drone_id)
    page = mgr.page(profile)
    page.goto(url, wait_until="domcontentloaded")
    _emit_state(ctx, profile, "open_url", url=url)
    return _result(True, url=page.url, title=page.title() or "")


def _action_screenshot(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    label = str(args.get("label", "screenshot"))
    mgr = manager_for_drone(ctx.drone_id)
    path = mgr.screenshot(profile, label=label)
    page = mgr.page(profile)
    _emit_state(ctx, profile, "screenshot")
    return _result(True, path=str(path), url=page.url, title=page.title() or "")


def _action_click(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    selector = str(args.get("selector", "")).strip()
    if not selector:
        return _result(False, error="selector required")
    mgr = manager_for_drone(ctx.drone_id)
    page = mgr.page(profile)
    page.click(selector, timeout=DEFAULT_WAIT_TIMEOUT_S * 1000)
    _emit_state(ctx, profile, "click", selector=selector)
    return _result(True)


def _action_type(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    selector = str(args.get("selector", "")).strip()
    text = args.get("text", "")
    if not selector:
        return _result(False, error="selector required")
    mgr = manager_for_drone(ctx.drone_id)
    page = mgr.page(profile)
    page.fill(selector, str(text), timeout=DEFAULT_WAIT_TIMEOUT_S * 1000)
    if args.get("submit"):
        page.press(selector, "Enter")
    _emit_state(ctx, profile, "type", selector=selector)
    return _result(True)


def _action_press(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    key = str(args.get("key", "")).strip()
    if not key:
        return _result(False, error="key required")
    mgr = manager_for_drone(ctx.drone_id)
    page = mgr.page(profile)
    selector = args.get("selector")
    if selector:
        page.press(str(selector), key)
    else:
        page.keyboard.press(key)
    _emit_state(ctx, profile, "press", key=key)
    return _result(True)


def _action_fill_form(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    raw_fields = args.get("fields") or []
    if not isinstance(raw_fields, list) or not raw_fields:
        return _result(False, error="fields[] required")
    mgr = manager_for_drone(ctx.drone_id)
    page = mgr.page(profile)
    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        sel = str(f.get("selector", "")).strip()
        val = str(f.get("value", ""))
        if not sel:
            continue
        page.fill(sel, val, timeout=DEFAULT_WAIT_TIMEOUT_S * 1000)
    if args.get("submit_selector"):
        page.click(str(args["submit_selector"]), timeout=DEFAULT_WAIT_TIMEOUT_S * 1000)
    _emit_state(ctx, profile, "fill_form")
    return _result(True, fields_filled=len(raw_fields))


def _action_select_option(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    selector = str(args.get("selector", "")).strip()
    if not selector:
        return _result(False, error="selector required")
    mgr = manager_for_drone(ctx.drone_id)
    page = mgr.page(profile)
    value = args.get("value")
    label = args.get("label")
    if value is not None:
        page.select_option(selector, value=str(value))
    elif label is not None:
        page.select_option(selector, label=str(label))
    else:
        return _result(False, error="value or label required")
    _emit_state(ctx, profile, "select_option", selector=selector)
    return _result(True)


def _action_scroll(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    mgr = manager_for_drone(ctx.drone_id)
    page = mgr.page(profile)
    selector = args.get("selector")
    if selector:
        page.locator(str(selector)).scroll_into_view_if_needed(
            timeout=DEFAULT_WAIT_TIMEOUT_S * 1000
        )
    else:
        dx = int(args.get("dx", 0) or 0)
        dy = int(args.get("dy", 0) or 0)
        page.mouse.wheel(dx, dy)
    _emit_state(ctx, profile, "scroll")
    return _result(True)


def _action_navigate_back(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    mgr = manager_for_drone(ctx.drone_id)
    page = mgr.page(profile)
    page.go_back(wait_until="domcontentloaded")
    _emit_state(ctx, profile, "navigate_back")
    return _result(True, url=page.url, title=page.title() or "")


def _action_wait_for(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    mgr = manager_for_drone(ctx.drone_id)
    page = mgr.page(profile)
    timeout = float(args.get("timeout_s", DEFAULT_WAIT_TIMEOUT_S)) * 1000
    if "selector" in args and args["selector"]:
        page.wait_for_selector(str(args["selector"]), timeout=timeout)
        _emit_state(ctx, profile, "wait_for", selector=args["selector"])
        return _result(True, matched="selector")
    if "url" in args and args["url"]:
        page.wait_for_url(str(args["url"]), timeout=timeout)
        _emit_state(ctx, profile, "wait_for", url=args["url"])
        return _result(True, matched="url")
    return _result(False, error="selector or url required")


def _action_extract_text(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    mgr = manager_for_drone(ctx.drone_id)
    page = mgr.page(profile)
    selector = args.get("selector")
    if selector:
        text = page.locator(str(selector)).inner_text(
            timeout=DEFAULT_WAIT_TIMEOUT_S * 1000
        )
    else:
        text = page.evaluate("() => document.body.innerText")
    # Cap so a single extraction can't blow the context window.
    text = str(text or "")[:8000]
    return _result(True, text=text, url=page.url, title=page.title() or "")


def _action_evaluate(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    """Run arbitrary JavaScript on the page and return the result. Use to
    inspect elements, extract structured data, or drive custom interactions
    that don't fit the standard action set."""
    script = str(args.get("script", "")).strip()
    if not script:
        return _result(False, error="script required")
    mgr = manager_for_drone(ctx.drone_id)
    page = mgr.page(profile)
    # page.evaluate returns a JSON-serialisable value automatically.
    result = page.evaluate(script)
    # Cap so a single huge DOM dump can't blow the context window.
    if isinstance(result, str):
        result = result[:12000]
    elif isinstance(result, list):
        result = result[:500]
    _emit_state(ctx, profile, "evaluate")
    return _result(True, result=result)


def _action_register_profile(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    """Advertise that this profile now has a usable capability — e.g. logged
    into LinkedIn, paired with a GitHub session. The tool is registered in
    the graph so future drones can discover it via cm_list_tools."""
    summary = str(args.get("summary", "")).strip()
    description = (
        str(args.get("description", "")).strip()
        or f"Headed Chromium browser session with profile {profile!r}. "
        "Use cm_browser(profile=...) to drive."
    )
    # Capture service tags if the drone provided them, and persist to metadata.json.
    services_raw = args.get("services")
    if services_raw and isinstance(services_raw, list):
        services = [str(s).strip() for s in services_raw if s and str(s).strip()]
        if services:
            set_profile_services(profile, services)
    else:
        services = get_profile_services(profile)
    # Append service tags to the description so other drones see what this
    # profile is good for.
    if services:
        svc = ", ".join(services)
        description += f" [services: {svc}]"
    name = registered_tool_name(profile)
    try:
        rec = Tool(
            name=name,
            description=description,
            input_schema_json=json.dumps({"type": "object", "properties": {}}),
            kind=ToolKind.installed,
            usage=f'cm_browser(action="open_url", profile="{profile}", url="…")',
            install_commands=[],
            depends_on=["cm_browser"],
            installed_by_drone_id=ctx.drone_id,
        )
        ctx.tool_store.register_installed(rec)
    except (ValueError, KeyError, TypeError) as e:
        return _result(False, error=f"{type(e).__name__}: {e}")
    if ctx.tape is not None:
        ctx.tape.emit(
            "browser.profile_registered",
            drone_id=ctx.drone_id,
            gap_id=ctx.gap_id,
            profile=profile,
            tool_name=name,
            summary=summary,
            services=services,
        )
    return _result(True, tool_name=name)


def _poll_chat_with_drone(
    ctx: DroneContext, *, since_tick: int
) -> tuple[str, str] | None:
    """Return ``(finding_id, text)`` of the most recent unread operator chat
    addressed to this drone, or ``None`` if there are none."""
    try:
        # ``recent_findings`` is bounded; we look at the latest 50 which
        # easily covers a multi-drone swarm. A chat from the operator is a
        # finding kind=chat_with_drone, author=user, with this gap in
        # affected_gap_ids.
        findings = ctx.store.recent_findings(limit=50)
    except Exception:
        return None
    target = ctx.gap_id
    for f in reversed(findings):  # newest first
        if f.tick <= since_tick:
            continue
        if f.kind != FindingKind.chat_with_drone:
            continue
        if f.author != FindingAuthor.user:
            continue
        if target not in (f.affected_gap_ids or []):
            continue
        return f.id, f.summary
    return None


def _action_await_operator(
    args: dict[str, Any], ctx: DroneContext, profile: str
) -> str:
    """Block until the operator chats with this drone, or signals cancel.

    Writes an ``await_operator`` event and a ``chat_with_drone`` finding so
    the operator's UI knows what the drone is asking for. Returns the
    operator's reply as the tool result. The drone *does not* exit — it
    resumes its message loop with the operator's input as context.
    """
    prompt = str(args.get("prompt", "")).strip() or "(awaiting operator input)"
    timeout = float(args.get("timeout_s", DEFAULT_AWAIT_OPERATOR_TIMEOUT_S))
    # Post a finding asking for input. The operator's UI surfaces this in
    # the drone's chat panel and the Action Inbox.
    ask = ctx.store.append_finding(
        tick=ctx.tick,
        author=FindingAuthor.worker,
        kind=FindingKind.chat_with_drone,
        summary=prompt,
        affected_gap_ids=[ctx.gap_id],
    )
    _emit_state(ctx, profile, "await_operator", prompt=prompt, ask_finding_id=ask.id)
    notify("Drone needs you", prompt[:140])
    if ctx.tape is not None:
        ctx.tape.emit(
            "browser.await_operator",
            drone_id=ctx.drone_id,
            gap_id=ctx.gap_id,
            profile=profile,
            prompt=prompt,
            ask_finding_id=ask.id,
        )
    deadline = time.monotonic() + timeout
    since = ctx.tick
    while time.monotonic() < deadline:
        # Cancellation: gap retired / swarm paused, etc.
        if ctx.signals is not None and ctx.signals.is_cancelled("gap", ctx.gap_id):
            return _result(False, cancelled=True)
        # Renew the browser slot so we don't get reaped while idle.
        if ctx.signals is not None and ctx.drone_id in _DRONE_SLOT:
            slot = _DRONE_SLOT[ctx.drone_id]
            if slot >= 0:
                heartbeat_slot(ctx.signals, slot, ctx.drone_id)
        # Renew the gap claim too — the runtime's background heartbeat
        # thread also does this, but we keep things resilient.
        hit = _poll_chat_with_drone(ctx, since_tick=since)
        if hit is not None:
            finding_id, text = hit
            if ctx.tape is not None:
                ctx.tape.emit(
                    "browser.operator_replied",
                    drone_id=ctx.drone_id,
                    gap_id=ctx.gap_id,
                    profile=profile,
                    finding_id=finding_id,
                    text=text[:500],
                )
            return _result(True, message=text, finding_id=finding_id)
        time.sleep(AWAIT_POLL_S)
    return _result(False, timed_out=True)


def _action_close(args: dict[str, Any], ctx: DroneContext, profile: str) -> str:
    mgr = manager_for_drone(ctx.drone_id)
    closed = mgr.close_profile(profile)
    # When the drone has no more open profiles, release its slot so other
    # drones waiting in the queue can come up.
    if not mgr.active_profiles():
        _release_drone_slot(ctx)
    if ctx.tape is not None:
        ctx.tape.emit(
            "browser.close",
            drone_id=ctx.drone_id,
            gap_id=ctx.gap_id,
            profile=profile,
            closed=closed,
        )
    return _result(True, closed=closed)


# ---- Dispatcher table ----------------------------------------------------


_ACTIONS = {
    "open_url": _action_open_url,
    "screenshot": _action_screenshot,
    "click": _action_click,
    "type": _action_type,
    "press": _action_press,
    "fill_form": _action_fill_form,
    "select_option": _action_select_option,
    "scroll": _action_scroll,
    "navigate_back": _action_navigate_back,
    "wait_for": _action_wait_for,
    "extract_text": _action_extract_text,
    "evaluate": _action_evaluate,
    "register_profile": _action_register_profile,
    "await_operator": _action_await_operator,
    "close": _action_close,
}


@register_tool(
    "cm_browser",
    (
        "Drive a real headed Chromium window. Use to fill out a form, sign "
        "in to a service, scrape a page that needs JS, or hand-off to the "
        "operator for a step you can't do (OAuth, MFA, payment). One "
        "browser per `profile` name (slug). Profiles persist across drones "
        "— if a previous drone logged in, you stay logged in. "
        "Actions: open_url, screenshot, click, type, press, fill_form, "
        "select_option, scroll, navigate_back, wait_for, extract_text, "
        "evaluate, "
        "register_profile, await_operator, close. "
        "When stuck or facing a sign-in / OAuth / MFA challenge, call "
        "await_operator with a one-sentence `prompt` describing what you "
        "need the human to do — the human drives the page in the live "
        "window or types a reply in the drone-attached chat panel, and "
        "this call returns their message. When you've established a "
        "working session that future drones should reuse, call "
        "register_profile to advertise the capability. "
        "Do NOT use for Google/Gmail/YouTube tasks — use "
        "cm_authenticated_browser instead."
    ),
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_ACTIONS.keys()),
                "description": "Which browser verb to run.",
            },
            "profile": {
                "type": "string",
                "description": (
                    "Profile name (slug, [A-Za-z0-9_-]). Choose a stable "
                    "name per service identity, e.g. 'linkedin-main'."
                ),
            },
            "url": {"type": "string", "description": "open_url / wait_for(url) target."},
            "selector": {
                "type": "string",
                "description": "CSS selector for click/type/wait_for/extract_text/scroll.",
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
                "description": "fill_form: optional submit button selector to click after.",
            },
            "value": {"type": "string", "description": "select_option: option value."},
            "label": {
                "type": "string",
                "description": (
                    "select_option: option label. screenshot: filename label."
                ),
            },
            "dx": {"type": "integer"},
            "dy": {"type": "integer"},
            "timeout_s": {
                "type": "number",
                "description": "wait_for: max seconds; await_operator: max idle seconds.",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "await_operator: one-sentence ask shown in the drone's "
                    "chat panel and OS notification."
                ),
            },
            "summary": {
                "type": "string",
                "description": (
                    "register_profile: one-line description of the "
                    "capability this profile now holds."
                ),
            },
            "description": {
                "type": "string",
                "description": "register_profile: optional longer description.",
            },
            "services": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "register_profile: list of service names this profile "
                    "has active sessions for, e.g. [\"google\", \"reddit\", \"x\"]. "
                    "Persisted to the profile's metadata for later discovery."
                ),
            },
            "script": {
                "type": "string",
                "description": (
                    "evaluate: JavaScript expression or function body to run "
                    "in the page context. Return value must be JSON-serialisable."
                ),
            },
        },
        "required": ["action", "profile"],
    },
)
def cm_browser(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    action = str(args.get("action", "")).strip()
    profile = str(args.get("profile", "")).strip()
    # Log every browser action with key params for debugging sign-in issues.
    _log_browser_action(action, profile, args)
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
    if not profile:
        return ToolResult(content=_result(False, error="profile required"))
    # Validate the profile name (raises ValueError otherwise).
    try:
        profile_dir(profile)
    except ValueError as e:
        return ToolResult(content=_result(False, error=str(e)))
    # Acquire a slot before any Playwright work. ``close`` still goes
    # through the slot machinery because a misbehaving drone might call
    # close on a profile it never opened — we need a consistent path.
    slot = _ensure_slot(ctx)
    if isinstance(slot, str):
        return ToolResult(content=slot)
    fn = _ACTIONS[action]
    try:
        out = fn(args, ctx, profile)
    except Exception as e:  # noqa: BLE001 - surface Playwright errors to the model
        if ctx.tape is not None:
            ctx.tape.emit(
                "browser.error",
                drone_id=ctx.drone_id,
                gap_id=ctx.gap_id,
                profile=profile,
                action=action,
                error=f"{type(e).__name__}: {e}",
            )
        return ToolResult(content=_result(False, error=f"{type(e).__name__}: {e}"))
    return ToolResult(content=out)


def _log_browser_action(action: str, profile: str, args: dict[str, Any]) -> None:
    """Print a debug line to stderr showing the browser action and its key params."""
    import sys
    parts = [f"[cm_browser] action={action!r} profile={profile!r}"]
    if action == "type":
        sel = args.get("selector", "")
        submit = args.get("submit", False)
        parts.append(f"selector={sel!r} submit={submit!r}")
    elif action == "click":
        parts.append(f"selector={args.get('selector', '')!r}")
    elif action == "press":
        parts.append(f"key={args.get('key', '')!r} selector={args.get('selector', '')!r}")
    elif action == "fill_form":
        fields = args.get("fields", [])
        submit_sel = args.get("submit_selector", "")
        parts.append(f"fields={len(fields)} submit_selector={submit_sel!r}")
    elif action == "await_operator":
        parts.append(f"prompt={args.get('prompt', '')!r}")
    elif action == "open_url":
        parts.append(f"url={args.get('url', '')!r}")
    print(*parts, file=sys.stderr, flush=True)


# ---- Drone-exit cleanup hook --------------------------------------------


def cleanup_for_drone(drone_id: str, signals: Any | None = None) -> None:
    """Called by the runtime when a drone exits. Closes any open contexts
    and releases its browser slot. Safe to call multiple times."""
    from drone_graph.tools.builtins.browser.session import (
        _DRONE_MANAGER,
        has_active_manager,
    )

    if has_active_manager(drone_id):
        try:
            _DRONE_MANAGER[drone_id].stop()
        except Exception:
            pass
        _DRONE_MANAGER.pop(drone_id, None)
    slot = _DRONE_SLOT.pop(drone_id, None)
    _LAST_HEARTBEAT.pop(drone_id, None)
    if slot is not None and slot >= 0 and signals is not None:
        try:
            release_slot(signals, slot, drone_id)
        except Exception:
            pass
