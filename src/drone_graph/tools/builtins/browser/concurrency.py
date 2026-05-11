"""Browser concurrency — bounded simultaneous Chromium windows.

Drones with browser sessions hold a ``browser_slot:<i>`` claim in the
signals sidecar. Up to ``max_slots`` (default 4, configurable) at once.
A drone that needs a slot and finds none free **waits** rather than
giving up — it blocks at the start of the tool call, heart-beating the
gap claim and polling for cancel signals while it waits.

This is the same try_acquire / claim pattern the scheduler already uses
for gap claims and the install registry. No new substrate.
"""

from __future__ import annotations

import os
import time
from typing import Any

DEFAULT_MAX_SLOTS = 4
DEFAULT_SLOT_TTL_S = 600.0   # 10 minutes — heartbeat every minute or so
POLL_INTERVAL_S = 1.5


def max_slots_from_env() -> int:
    """Honor an env override (set by the controller from Settings).

    Falls back to ``DEFAULT_MAX_SLOTS`` when unset or invalid. Drones run
    as subprocesses spawned by the scheduler with ``env=os.environ.copy()``
    so any change here in the parent flows to children at spawn time.
    """
    raw = os.environ.get("DRONE_GRAPH_MAX_BROWSER_SLOTS", "").strip()
    if not raw:
        return DEFAULT_MAX_SLOTS
    try:
        n = int(raw)
        return n if n > 0 else DEFAULT_MAX_SLOTS
    except ValueError:
        return DEFAULT_MAX_SLOTS


def acquire_slot(
    signals: Any,
    drone_id: str,
    *,
    cancel_check: Any = None,
    max_slots: int | None = None,
) -> int | None:
    """Block until a browser slot is available; return its index.

    Returns ``None`` if ``cancel_check`` (callable → bool) ever fires
    True — caller should return an error from the tool.

    The TTL is a safety net for crashed drones: if a drone holds a slot
    and dies without releasing, the slot expires after ``DEFAULT_SLOT_TTL_S``
    and is reclaimable by ``try_acquire``. Long-running browsers must
    call ``heartbeat_slot`` periodically.
    """
    n = max_slots if max_slots is not None else max_slots_from_env()
    while True:
        for i in range(n):
            if signals.try_acquire(
                "browser_slot",
                str(i),
                drone_id,
                DEFAULT_SLOT_TTL_S,
            ):
                return i
        if cancel_check is not None and cancel_check():
            return None
        time.sleep(POLL_INTERVAL_S)


def heartbeat_slot(signals: Any, slot: int, drone_id: str) -> None:
    """Renew the slot's lease so a long browser session doesn't get reaped."""
    signals.heartbeat("browser_slot", str(slot), drone_id, DEFAULT_SLOT_TTL_S)


def release_slot(signals: Any, slot: int, drone_id: str) -> None:
    """Free the slot when the browser closes."""
    signals.release("browser_slot", str(slot), drone_id)
