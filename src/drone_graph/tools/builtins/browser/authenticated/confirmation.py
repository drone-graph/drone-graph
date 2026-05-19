"""Confirmation gate for authenticated browser actions.

Before ANY action in the authenticated Chrome profile, the drone must pause
and get explicit operator approval via the existing SignalStore permissions
mechanism. This uses a dedicated ``category="authenticated_browser"`` so the
UI can distinguish these prompts from regular permission prompts.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT_S = 300.0  # 5 minutes
POLL_INTERVAL_S = 0.5


@dataclass
class ConfirmationDecision:
    approved: bool
    reason: str | None = None
    timed_out: bool = False


def require_confirmation(
    *,
    action: str,
    url: str | None,
    description: str,
    drone_id: str,
    gap_id: str,
    signals: Any | None = None,
    tape: Any | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ConfirmationDecision:
    """Block until the operator confirms or denies via the permissions system.

    Uses the same ``SignalStore.request_permission`` / ``get_permission``
    mechanism as ``check_or_wait`` in ``permissions/gate.py``, but with a
    dedicated ``category="authenticated_browser"`` so the UI can style
    authenticated-browser prompts distinctly.

    Parameters
    ----------
    action : str
        The browser verb the drone intends to run (e.g. ``open_url``, ``click``).
    url : str | None
        Target URL for the action, if applicable.
    description : str
        Human-readable one-liner describing what the drone wants to do.
    drone_id : str
        The drone requesting the action.
    gap_id : str
        The gap this drone is working on.
    signals : SignalStore | None
        Cross-process coordination store. If ``None``, approval is assumed
        (safe for headless / testing).
    tape : Any | None
        Optional tape for event emission.
    timeout_s : float
        How long to wait before giving up (default 5 minutes).

    Returns
    -------
    ConfirmationDecision
    """
    if signals is None:
        # No signals sidecar — assume approved (safe for headless / unit tests).
        return ConfirmationDecision(approved=True)

    request_id = uuid.uuid4().hex

    summary_parts = [f"[authenticated_browser] {description}"]
    if action:
        summary_parts.append(f"action={action}")
    if url:
        summary_parts.append(f"url={url}")
    summary = " · ".join(summary_parts)

    signals.request_permission(
        request_id=request_id,
        drone_id=drone_id,
        gap_id=gap_id,
        tier="ask_external",
        tool_name="cm_authenticated_browser",
        category="authenticated_browser",
        summary=summary,
    )

    if tape is not None:
        try:
            tape.emit(
                "authenticated_browser.confirmation_request",
                request_id=request_id,
                drone_id=drone_id,
                gap_id=gap_id,
                action=action,
                url=url,
                description=description,
            )
        except Exception:
            pass

    deadline = time.monotonic() + max(1.0, timeout_s)
    while True:
        rec = signals.get_permission(request_id)
        if rec is None:
            return ConfirmationDecision(
                approved=False,
                reason="permission row vanished before operator answered",
            )
        if rec.status == "granted":
            note = rec.resolver_note
            signals.consume_permission(request_id)
            if tape is not None:
                try:
                    tape.emit(
                        "authenticated_browser.confirmation_resolved",
                        request_id=request_id,
                        gap_id=gap_id,
                        outcome="approved",
                    )
                except Exception:
                    pass
            return ConfirmationDecision(approved=True, reason=note)
        if rec.status == "denied":
            note = rec.resolver_note
            signals.consume_permission(request_id)
            if tape is not None:
                try:
                    tape.emit(
                        "authenticated_browser.confirmation_resolved",
                        request_id=request_id,
                        gap_id=gap_id,
                        outcome="denied",
                    )
                except Exception:
                    pass
            return ConfirmationDecision(approved=False, reason=note)
        if time.monotonic() >= deadline:
            signals.consume_permission(request_id)
            if tape is not None:
                try:
                    tape.emit(
                        "authenticated_browser.confirmation_resolved",
                        request_id=request_id,
                        gap_id=gap_id,
                        outcome="timeout",
                    )
                except Exception:
                    pass
            return ConfirmationDecision(
                approved=False,
                reason=f"operator did not answer within {int(timeout_s)}s",
                timed_out=True,
            )
        time.sleep(POLL_INTERVAL_S)
