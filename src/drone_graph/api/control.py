"""Swarm lifecycle controller.

The mission-control server owns exactly one ``Scheduler`` instance, running
in a daemon thread. The controller wraps it with:

  * pause / resume (workers stop spawning; in-flight drones finish)
  * cost ceiling (mutable from the API; the scheduler reads it on each tick)
  * paranoid install mode (installs queued for user approval instead of
    landing automatically)
  * infinite mode (substrate stays warm forever — when work runs dry the
    scheduler downshifts tick cadence and goes "resting", waiting on the
    next user_input finding rather than exiting)

The internal stop conditions of the scheduler still apply when not in
infinite mode (mostly for tests). The mission-control flow always runs in
infinite mode.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from drone_graph.api.events import DroneTapeTailer, EventBus
from drone_graph.gaps import FindingAuthor, FindingKind, GapStore
from drone_graph.orchestrator.tape import EventTape
from drone_graph.signals import SQLiteSignalStore
from drone_graph.substrate import Substrate

ACTIVE_TICK_S = 1.0
RESTING_TICK_S = 8.0


@dataclass
class PendingInstall:
    """An install request held for user approval in paranoid mode."""

    id: str
    tool_name: str
    description: str
    install_commands: list[str]
    usage: str
    requested_by_drone_id: str
    requested_at: datetime


class SchedulerControl:
    """Thread-safe knobs the running ``Scheduler`` polls each tick.

    Created by the controller; passed into the scheduler. The scheduler reads
    ``is_paused``, ``cost_ceiling_usd``, ``infinite_mode`` etc. and respects
    them. See ``Scheduler.__init__`` for wiring.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._paused = threading.Event()
        self._stop_requested = threading.Event()
        self._cost_ceiling_usd: float | None = None
        self._paranoid_install: bool = False
        self._infinite_mode: bool = True
        self._tick_s: float = ACTIVE_TICK_S
        # Wake event: scheduler waits on this instead of plain ``time.sleep``
        # so a force-tick or settings change can short-circuit the cadence.
        self._wake = threading.Event()
        # When set, the scheduler's next preset slot spawns the requested
        # role (gap_finding | alignment) regardless of cadence. Cleared on
        # consumption.
        self._force_role: str | None = None

    # ---- Pause ------------------------------------------------------------

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()
        # Wake the scheduler if it's in the 8s resting sleep so the first
        # prompt acts immediately instead of waiting out the cadence.
        self._wake.set()

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    # ---- Stop ------------------------------------------------------------

    def request_stop(self) -> None:
        self._stop_requested.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    # ---- Cost ceiling ---------------------------------------------------

    def set_cost_ceiling(self, usd: float | None) -> None:
        with self._lock:
            self._cost_ceiling_usd = usd

    @property
    def cost_ceiling_usd(self) -> float | None:
        with self._lock:
            return self._cost_ceiling_usd

    # ---- Paranoid mode --------------------------------------------------

    def set_paranoid_install(self, on: bool) -> None:
        import os as _os

        with self._lock:
            self._paranoid_install = on
            # Mirror to env so drone subprocesses (which inherit os.environ
            # via the scheduler's spawn) see the flag. cm_register_tool reads
            # this and downgrades new installs to trust_tier=low + flagged,
            # plus emits a requires_user_action finding for the inbox.
            if on:
                _os.environ["DRONE_GRAPH_PARANOID_INSTALL"] = "1"
            else:
                _os.environ.pop("DRONE_GRAPH_PARANOID_INSTALL", None)

    @property
    def paranoid_install(self) -> bool:
        with self._lock:
            return self._paranoid_install

    # ---- Infinite mode ---------------------------------------------------

    @property
    def infinite_mode(self) -> bool:
        return self._infinite_mode

    def set_infinite_mode(self, on: bool) -> None:
        self._infinite_mode = on

    # ---- Tick cadence ---------------------------------------------------

    def set_tick_s(self, t: float) -> None:
        with self._lock:
            self._tick_s = max(0.1, t)

    @property
    def tick_s(self) -> float:
        with self._lock:
            return self._tick_s

    # ---- Wake / force-tick ----------------------------------------------

    def wake(self) -> None:
        """Wake the scheduler immediately if it's mid-sleep. Idempotent."""
        self._wake.set()

    def sleep_for(self, seconds: float) -> None:
        """Replacement for ``time.sleep`` that the scheduler uses inside its
        run loop. Returns early if ``wake()`` is called or stop is requested."""
        self._wake.wait(timeout=max(0.0, seconds))
        self._wake.clear()

    def request_force_tick(self, role: str) -> None:
        """Operator asked for an immediate GF / Alignment tick. Stores the
        requested role and wakes the scheduler so it spawns it now."""
        with self._lock:
            self._force_role = role
        self.wake()

    def take_force_role(self) -> str | None:
        """Atomic claim-and-clear. The scheduler calls this when picking the
        next preset and uses the returned role (if any) instead of the
        cadence-based selection."""
        with self._lock:
            r = self._force_role
            self._force_role = None
            return r


class SwarmController:
    """Owns the scheduler thread + tape tailer + pending-install queue.

    Lifecycle:
      * ``__init__`` builds the substrate, ``GapStore``, ``ToolStore``,
        ``SignalStore``, and the scheduler thread (paused at boot).
      * ``submit_prompt`` writes a ``user_input`` finding (root prompt or
        per-gap message) and resumes the scheduler.
      * ``pause`` / ``resume`` / ``set_ceiling`` / ``set_paranoid`` are the
        operator surface.
      * ``shutdown`` cooperatively stops the scheduler and closes resources.
    """

    def __init__(
        self,
        *,
        substrate: Substrate,
        store: GapStore,
        tool_store: Any,
        signals: SQLiteSignalStore,
        provider: Any,
        model: str,
        event_bus: EventBus,
        tier_overrides: dict[str, dict[str, str]] | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self.substrate = substrate
        self.store = store
        self.tool_store = tool_store
        self.signals = signals
        self.provider = provider
        self.model = model
        self.event_bus = event_bus

        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + str(uuid4())[:8]
        self.started_at = datetime.now(UTC)
        self.out_dir = Path("var") / "runs" / f"mission-control-{self.run_id}"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.tape_path = self.out_dir / "scheduler-tape.jsonl"
        self.tape = EventTape(self.tape_path)
        self.event_bus.set_tape_path(self.tape_path)
        self.drone_tape_tailer = DroneTapeTailer(self.run_id, bus=self.event_bus)
        self.control = SchedulerControl()
        # Start paused — the substrate is dormant until the user sends a
        # first prompt. This is the "cobalt dot in the dark" empty state.
        self.control.pause()
        self.control.set_infinite_mode(True)
        self.signals.init_run(self.run_id, ceiling_usd=None)

        self._pending_installs: dict[str, PendingInstall] = {}
        self._pending_lock = threading.Lock()

        self._tier_overrides = dict(tier_overrides or {})
        self._workspace_dir = workspace_dir
        self._scheduler: Any | None = None  # drone_graph.orchestrator.scheduler.Scheduler
        self._sched_thread: threading.Thread | None = None
        self._start_scheduler_thread()

    # ---- Scheduler thread lifecycle --------------------------------------

    def _start_scheduler_thread(self) -> None:
        # Lazy import to avoid module-load-time cycle with the scheduler.
        from drone_graph.orchestrator.scheduler import Scheduler

        sched = Scheduler(
            substrate=self.substrate,
            signals=self.signals,
            store=self.store,
            provider=self.provider,
            model=self.model,
            run_id=self.run_id,
            out_dir=self.out_dir,
            tape=self.tape,
            max_workers=20,
            tick_s=ACTIVE_TICK_S,
            align_every=3,
            max_gf=1_000_000,  # effectively unbounded in infinite mode
            worker_max_turns=20,
            preset_max_turns=6,
            scenario_events=None,
            cost_ceiling_usd=None,
            control=self.control,
            tier_overrides=self._tier_overrides or None,
            workspace_dir=self._workspace_dir,
        )
        self._scheduler = sched

        def _run() -> None:
            try:
                sched.run()
            except Exception as e:  # noqa: BLE001 - surface to the API tape
                self.event_bus.publish("scheduler.error", error=str(e))
            # If the loop exited despite the operator NOT having requested
            # stop, that's an unintended death. Surface it on the event
            # bus + the swarm state ("stopped") so the operator sees a
            # restart prompt instead of staring at a dead "active" UI.
            try:
                requested = bool(self.control.stop_requested)
            except Exception:  # noqa: BLE001
                requested = False
            if not requested:
                self.event_bus.publish(
                    "scheduler.died",
                    run_id=self.run_id,
                    stop_reason=getattr(sched, "stop_reason", "") or "unknown",
                )

        self._sched_thread = threading.Thread(
            target=_run, name="swarm-scheduler", daemon=True
        )
        self._sched_thread.start()
        self.event_bus.publish(
            "controller.started",
            run_id=self.run_id,
            provider=self.provider.value if hasattr(self.provider, "value") else str(self.provider),
            model=self.model,
        )

    # ---- Public operator surface ----------------------------------------

    def submit_prompt(self, message: str, *, target_gap_id: str | None = None) -> str:
        """Write a ``user_input`` finding for the swarm to react to.

        Per the architecture decision: a root prompt does NOT directly mint a
        top-level gap. It lands as a finding on ``preset:gap_finding``, and
        Gap Finding decides how to frame it next tick.
        """
        affected = [target_gap_id] if target_gap_id else []
        if not target_gap_id:
            preset = self.store.get_preset("gap_finding")
            if preset is not None:
                affected = [preset.id]
        tick = self._next_tick()
        finding = self.store.append_finding(
            tick=tick,
            author=FindingAuthor.user,
            kind=FindingKind.user_input,
            summary=message,
            affected_gap_ids=affected,
        )
        self.event_bus.publish(
            "user.prompt",
            tick=tick,
            finding_id=finding.id,
            affected_gap_ids=affected,
            summary=message,
        )
        # Wake the swarm — first prompt transitions empty → active.
        if self.control.is_paused:
            self.control.resume()
        return finding.id

    def pause(self) -> None:
        self.control.pause()
        self.event_bus.publish("controller.paused")

    def resume(self) -> None:
        self.control.resume()
        self.event_bus.publish("controller.resumed")

    def set_cost_ceiling(self, usd: float | None) -> None:
        self.control.set_cost_ceiling(usd)
        self.event_bus.publish("controller.cost_ceiling_set", ceiling_usd=usd)

    def set_paranoid_install(self, on: bool) -> None:
        self.control.set_paranoid_install(on)
        self.event_bus.publish("controller.paranoid_install_set", enabled=on)

    def request_force_tick(self, role: str) -> None:
        """User asked for an immediate GF or Alignment tick. Flips a flag on
        the control object AND wakes the scheduler so it picks it up on the
        next iteration (no waiting for the 8s resting tick)."""
        self.control.request_force_tick(role)
        self.event_bus.publish("controller.force_tick", role=role)

    def cancel_drone(self, gap_id: str, *, reason: str = "user_cancelled") -> None:
        self.signals.signal_cancel("gap", gap_id)
        self.event_bus.publish(
            "controller.drone_cancel_requested",
            gap_id=gap_id,
            reason=reason,
        )

    # ---- Pending installs (paranoid mode) -------------------------------

    def queue_install(
        self,
        *,
        tool_name: str,
        description: str,
        install_commands: list[str],
        usage: str,
        requested_by_drone_id: str,
    ) -> PendingInstall:
        pi = PendingInstall(
            id=str(uuid4()),
            tool_name=tool_name,
            description=description,
            install_commands=list(install_commands),
            usage=usage,
            requested_by_drone_id=requested_by_drone_id,
            requested_at=datetime.now(UTC),
        )
        with self._pending_lock:
            self._pending_installs[pi.id] = pi
        self.event_bus.publish(
            "install.pending",
            install_id=pi.id,
            tool_name=tool_name,
            requested_by_drone_id=requested_by_drone_id,
        )
        return pi

    def list_pending_installs(self) -> list[PendingInstall]:
        with self._pending_lock:
            return list(self._pending_installs.values())

    def resolve_install(self, install_id: str, *, approve: bool) -> bool:
        with self._pending_lock:
            pi = self._pending_installs.pop(install_id, None)
        if pi is None:
            return False
        self.event_bus.publish(
            "install.resolved",
            install_id=install_id,
            tool_name=pi.tool_name,
            approved=approve,
        )
        return True

    # ---- Read helpers ---------------------------------------------------

    def active_drones(self) -> list[dict[str, Any]]:
        """Snapshot of in-flight scheduler processes, enriched with vitals
        from the per-drone tape tail."""
        sched = self._scheduler
        if sched is None:
            return []
        out: list[dict[str, Any]] = []
        in_flight = list(getattr(sched, "workers", {}).values())
        preset = getattr(sched, "preset_slot", None)
        if preset is not None:
            in_flight.append(preset)
        for proc in in_flight:
            vitals = self.drone_tape_tailer.vitals_for(proc.gap_id)
            out.append(
                {
                    "drone_id": _pid_to_drone_id(proc),
                    "role": proc.role,
                    "gap_id": proc.gap_id,
                    "tick": proc.tick,
                    "spawned_at": _epoch_to_iso(proc.spawned_at),
                    "cancel_signaled": proc.cancel_signaled_at is not None,
                    "tape_path": str(proc.tape_path) if proc.tape_path else None,
                    "turn": getattr(vitals, "turn", None) if vitals else None,
                    "max_turns": getattr(vitals, "max_turns", None) if vitals else None,
                    "last_command": getattr(vitals, "last_command", None) if vitals else None,
                    "tail_lines": list(getattr(vitals, "tail", []) or []) if vitals else [],
                    "last_tool_calls": list(getattr(vitals, "last_tool_calls", []) or []) if vitals else [],
                    "cost_usd": getattr(vitals, "cost_usd", None) if vitals else None,
                    "tokens_in": getattr(vitals, "tokens_in", None) if vitals else None,
                    "tokens_out": getattr(vitals, "tokens_out", None) if vitals else None,
                    "provider": getattr(proc, "provider", "") or None,
                    "model": getattr(proc, "model", "") or None,
                    "model_tier": getattr(proc, "model_tier", "") or None,
                }
            )
        return out

    def swarm_state(self) -> str:
        """One of: idle | active | paused | cost_locked | resting | stopped.

        ``stopped`` means the scheduler thread is no longer alive — it
        died on an uncaught exception or a stop signal but the
        controller never got restarted. Surfaces clearly in the UI so
        the operator knows to hit Restart instead of staring at a swarm
        that looks 'active' but isn't dispatching anything.
        """
        sched = self._scheduler
        if sched is None:
            return "idle"
        # Detect a dead scheduler thread before reporting active state.
        # Without this, /api/status reports state=active forever even
        # though no ticks are firing — exactly the trap we just hit.
        t = self._sched_thread
        if t is not None and not t.is_alive():
            return "stopped"
        if self._budget_blown():
            return "cost_locked"
        if self.control.is_paused:
            # Distinguish a deliberately-paused swarm from a resting one.
            in_flight = (
                len(getattr(sched, "workers", {})) + (1 if getattr(sched, "preset_slot", None) else 0)
            )
            if in_flight == 0 and getattr(sched, "counters", None) and getattr(sched.counters, "consecutive_noops", 0) >= 3:
                return "resting"
            return "paused"
        return "active"

    def _budget_blown(self) -> bool:
        ceiling = self.control.cost_ceiling_usd
        if ceiling is None:
            return False
        return self.signals.spent(self.run_id) >= ceiling

    def cost_spent_usd(self) -> float:
        return self.signals.spent(self.run_id)

    def counters_snapshot(self) -> dict[str, int]:
        sched = self._scheduler
        if sched is None or not hasattr(sched, "counters"):
            return {
                "gf_count": 0,
                "align_count": 0,
                "worker_count": 0,
                "consecutive_noops": 0,
                "active_drones": 0,
            }
        c = sched.counters
        return {
            "gf_count": int(c.gf_count),
            "align_count": int(c.align_count),
            "worker_count": int(c.worker_count),
            "consecutive_noops": int(c.consecutive_noops),
            "active_drones": len(getattr(sched, "workers", {})) + (
                1 if getattr(sched, "preset_slot", None) else 0
            ),
        }

    # ---- Internal --------------------------------------------------------

    def _next_tick(self) -> int:
        sched = self._scheduler
        if sched is None:
            return 1
        sched.tick += 1
        return int(sched.tick)

    def current_tick(self) -> int:
        """Read-only accessor for the running scheduler's tick — used by
        endpoints that need to stamp a finding without advancing the clock
        (e.g. operator chat to a drone)."""
        sched = self._scheduler
        if sched is None:
            return 0
        return int(getattr(sched, "tick", 0))

    def shutdown(self) -> None:
        self.control.request_stop()
        self.control.resume()  # let the scheduler observe stop_requested
        if self._sched_thread is not None:
            self._sched_thread.join(timeout=10.0)
        self.drone_tape_tailer.stop()
        self.event_bus.stop()
        # Don't close substrate / signals here — the app owns them.


def _pid_to_drone_id(proc: Any) -> str:
    """Best-effort id for an in-flight subprocess. The scheduler doesn't
    record the runner's chosen drone id, but the pid + gap is unique."""
    pid = getattr(getattr(proc, "proc", None), "pid", None) or 0
    return f"d-{int(pid):x}-{proc.gap_id[:6]}"


def _epoch_to_iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=UTC).isoformat()
