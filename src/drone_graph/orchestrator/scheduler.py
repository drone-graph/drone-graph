"""Concurrent drone scheduler — Phase 3.

Replaces the single-threaded ``run_combined_loop`` with a process-pool model:

  * One scheduler process owns the dispatch loop.
  * Each drone runs in its own ``python -m drone_graph.drones.runner`` subprocess.
  * Coordination state (gap claims, cancel signals, install registry, token
    bucket, cost meter) lives in a SQLite sidecar at ``var/signals.db``.
  * At most one preset drone runs at a time (the "preset slot"); workers run
    concurrently up to ``--max-workers``.

The scheduler is opt-in for now — the legacy ``run_combined_loop`` is still
available as a single-threaded shim.

Usage:
    python -m drone_graph.orchestrator.scheduler --scenario coffee-pivot-b2b \\
        --max-workers 4 --max-gf 15
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from drone_graph.drones import (
    Provider,
    resolve_orchestrator_provider_model,
)
from drone_graph.gaps import FindingAuthor, Gap, GapStore
from drone_graph.model_registry.registry import ModelRegistry
from drone_graph.orchestrator.bootstrap import (
    PRESET_ALIGNMENT,
    PRESET_GAP_FINDING,
    init_collective_mind,
)
from drone_graph.orchestrator.scenarios import (
    available_scenarios,
    inject_event,
    load_root_seed,
    load_scenario,
)
from drone_graph.orchestrator.tape import EventTape
from drone_graph.signals import SQLiteSignalStore, default_db_path
from drone_graph.substrate import Substrate

DEFAULT_MAX_WORKERS = 4
DEFAULT_TICK_S = 1.0
DEFAULT_ALIGNMENT_EVERY = 3
DEFAULT_MAX_GF = 15
DEFAULT_WORKER_MAX_TURNS = 20
DEFAULT_PRESET_MAX_TURNS = 6
DEFAULT_TARGET_LEAVES = 5
HARDKILL_GRACE_S = 90.0       # 3.4 will use this for cancelled workers
SOFTKILL_GRACE_S = 5.0
NOOP_STREAK_TO_STOP = 3
# When ``control.infinite_mode`` is on, the scheduler downshifts to this tick
# cadence once the swarm goes idle (3 GF noops, no work pending). Wakes back
# up on the next user_input finding or any structural change.
RESTING_TICK_S = 8.0

# Maps runner exit codes back to the textual outcome the scheduler logs.
_EXIT_TO_OUTCOME: dict[int, str] = {
    0: "fill_or_preset_done",
    1: "fail",
    2: "cancelled_or_claim_lost",
    3: "max_turns",
    4: "error",
    5: "budget_exceeded",
}


@dataclass
class _Process:
    """Tracking state for a spawned drone subprocess."""

    role: str                   # "preset:gap_finding" | "preset:alignment" | "worker"
    gap_id: str
    tick: int
    tape_path: Path
    proc: subprocess.Popen[bytes]
    spawned_at: float
    cancel_signaled_at: float | None = None
    findings_before: int = 0    # snapshot of total findings count at spawn
    # Per-drone routing — what the scheduler actually invoked, not the
    # operator's static default. Workers route by gap tier; presets pin to
    # the advanced tier. Surface this so the UI can show the real model
    # being charged per drone.
    provider: str = ""
    model: str = ""
    model_tier: str = ""


@dataclass
class _Counters:
    gf_count: int = 0
    align_count: int = 0
    worker_count: int = 0
    events_fired: int = 0
    consecutive_noops: int = 0
    consecutive_gf_errors: int = 0
    last_alignment_gf: int = -1
    worker_outcomes: Counter[str] = field(default_factory=Counter)
    gf_verbs: Counter[str] = field(default_factory=Counter)
    align_kinds: Counter[str] = field(default_factory=Counter)


class Scheduler:
    """Concurrent drone scheduler. See module docstring."""

    def __init__(
        self,
        *,
        substrate: Substrate,
        signals: SQLiteSignalStore,
        store: GapStore,
        provider: Provider,
        model: str,
        run_id: str,
        out_dir: Path | None = None,
        tape: EventTape | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        tick_s: float = DEFAULT_TICK_S,
        align_every: int = DEFAULT_ALIGNMENT_EVERY,
        max_gf: int = DEFAULT_MAX_GF,
        worker_max_turns: int = DEFAULT_WORKER_MAX_TURNS,
        preset_max_turns: int = DEFAULT_PRESET_MAX_TURNS,
        signal_db: Path | None = None,
        scenario_events: list[dict[str, Any]] | None = None,
        cost_ceiling_usd: float | None = None,
        control: Any | None = None,
        tier_overrides: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.substrate = substrate
        self.signals = signals
        self.store = store
        self.provider = provider
        self.model = model
        self.run_id = run_id
        self.out_dir = out_dir
        self.tape = tape
        self.max_workers = max_workers
        self.tick_s = tick_s
        self.align_every = align_every
        self.max_gf = max_gf
        self.worker_max_turns = worker_max_turns
        self.preset_max_turns = preset_max_turns
        self.signal_db = signal_db if signal_db is not None else default_db_path()
        self.tape_dir = Path("var") / "tapes" / run_id
        self.tape_dir.mkdir(parents=True, exist_ok=True)
        self.pending_events = list(scenario_events or [])
        self.cost_ceiling_usd = cost_ceiling_usd
        # When ``control`` is set, the mission-control API owns pause/resume,
        # cost-ceiling and infinite-mode for this scheduler. ``control`` is a
        # ``drone_graph.api.control.SchedulerControl`` but we keep the type
        # loose so the scheduler doesn't import the API package.
        self.control = control

        self.preset_slot: _Process | None = None
        self.workers: dict[str, _Process] = {}   # gap_id -> _Process
        self.attempted_gap_ids: set[str] = set()
        self.tick = 0
        self.counters = _Counters()
        self.stop_reason = ""
        # Model registry — used to resolve per-gap (provider, model) from
        # ``gap.model_tier`` for *workers*, plus to pin presets to the
        # ``frontier`` tier of the operator's selected provider.
        # ``tier_overrides`` (from Settings) takes precedence over the
        # registry's tier_defaults_by_provider.
        try:
            self._registry: ModelRegistry | None = ModelRegistry.load_auto()
            if not self._registry.is_populated:
                self._registry = None
        except Exception:
            self._registry = None
        self._tier_overrides = dict(tier_overrides or {})

    # ---- Top-level run ----------------------------------------------------

    def run(self) -> None:
        self._emit("scheduler.start", run_id=self.run_id, max_workers=self.max_workers)
        try:
            while True:
                self._tick()
                if self._should_stop():
                    break
                self._sleep_until_next_tick(self._effective_tick_s())
        finally:
            self._drain_inflight()
            self._emit(
                "scheduler.stop",
                run_id=self.run_id,
                stop_reason=self.stop_reason or "natural exit",
                gf=self.counters.gf_count,
                align=self.counters.align_count,
                workers=self.counters.worker_count,
            )

    # ---- Single tick ------------------------------------------------------

    def _tick(self) -> None:
        self._reap_finished()
        self._reap_expired_claims()
        self._check_cost_ceiling()
        self._signal_cancellations()
        self._hard_kill_overdue()
        self._inject_pending_events()
        if self._is_externally_paused():
            return
        if not self._budget_blown():
            self._maybe_spawn_preset()
            self._maybe_spawn_workers()

    def _is_externally_paused(self) -> bool:
        return bool(self.control is not None and getattr(self.control, "is_paused", False))

    def _effective_tick_s(self) -> float:
        """Slow tick when the controller asks for a different cadence (e.g.
        ``RESTING_TICK_S`` while the swarm is idle in infinite mode). Falls
        back to ``self.tick_s`` if no controller is wired."""
        if self.control is None:
            return self.tick_s
        external = getattr(self.control, "tick_s", None)
        if external is None:
            return self.tick_s
        return float(external)

    def _sleep_until_next_tick(self, seconds: float) -> None:
        """Interruptible sleep. When wired to a controller, an operator
        force-tick / settings change wakes the loop instead of waiting out
        the full resting cadence."""
        if self.control is not None and hasattr(self.control, "sleep_for"):
            self.control.sleep_for(seconds)
            return
        time.sleep(seconds)

    def _budget_blown(self) -> bool:
        ceiling = self._effective_ceiling()
        if ceiling is None:
            return False
        return self.signals.spent(self.run_id) >= ceiling

    def _effective_ceiling(self) -> float | None:
        """Prefer the controller's mutable ceiling over the construction-time
        value so the API can adjust it live."""
        if self.control is not None:
            ceiling = getattr(self.control, "cost_ceiling_usd", None)
            if ceiling is not None:
                return float(ceiling)
        return self.cost_ceiling_usd

    def _check_cost_ceiling(self) -> None:
        if not self._budget_blown():
            return
        spent = self.signals.spent(self.run_id)
        ceiling = self._effective_ceiling()
        # Cancel in-flight regardless of mode.
        for proc in self._all_inflight():
            if proc.cancel_signaled_at is None:
                self.signals.signal_cancel("gap", proc.gap_id)
                proc.cancel_signaled_at = time.time()
        # In externally-controlled (infinite) mode, don't stop — just pause
        # the swarm so the operator can raise the ceiling or resume.
        if self.control is not None and getattr(self.control, "infinite_mode", False):
            if not getattr(self.control, "is_paused", False):
                self._emit(
                    "scheduler.cost_locked",
                    spent_usd=round(spent, 4),
                    ceiling_usd=ceiling,
                )
                self.control.pause()
            return
        if self.stop_reason:
            return
        self._emit(
            "scheduler.budget_exceeded",
            spent_usd=round(spent, 4),
            ceiling_usd=ceiling,
        )
        self.stop_reason = (
            f"swarm cost ceiling reached "
            f"(${spent:.3f} >= ${(ceiling or 0):.3f})"
        )

    # ---- Reaping ----------------------------------------------------------

    def _reap_finished(self) -> None:
        for proc in list(self.workers.values()):
            if proc.proc.poll() is not None:
                self._on_drone_exit(proc)
                self.workers.pop(proc.gap_id, None)
        if self.preset_slot is not None and self.preset_slot.proc.poll() is not None:
            self._on_drone_exit(self.preset_slot)
            self.preset_slot = None

    def _reap_expired_claims(self) -> None:
        reaped = self.signals.reap_expired()
        for r in reaped:
            self._emit(
                "claim.reaped",
                kind=r.kind,
                key=r.key,
                drone_id=r.drone_id,
            )

    def _signal_cancellations(self) -> None:
        """Detect retired-while-held gaps and raise the cancel flag.

        Workers that hold a claim on a now-retired gap should exit cleanly
        with a ``cancelled`` finding. Preset drones are not retired (presets
        are persistent) so they're skipped here; budget-exceeded cancellation
        for presets lives in 3.6.
        """
        for proc in self.workers.values():
            if proc.cancel_signaled_at is not None:
                continue
            gap = self.store.get(proc.gap_id)
            if gap is None or gap.status.value == "retired":
                self.signals.signal_cancel("gap", proc.gap_id)
                proc.cancel_signaled_at = time.time()
                self._emit(
                    "drone.cancel_signaled",
                    role=proc.role,
                    gap_id=proc.gap_id,
                    reason="gap_retired",
                )

    def _hard_kill_overdue(self) -> None:
        """SIGKILL drones that haven't honored a cancel within the grace window."""
        now = time.time()
        for proc in self._all_inflight():
            if proc.cancel_signaled_at is None:
                continue
            if now - proc.cancel_signaled_at < HARDKILL_GRACE_S:
                continue
            if proc.proc.poll() is not None:
                continue
            proc.proc.kill()
            self._emit(
                "drone.hard_killed",
                role=proc.role,
                gap_id=proc.gap_id,
                pid=proc.proc.pid,
                grace_elapsed_s=round(now - proc.cancel_signaled_at, 2),
            )

    def _all_inflight(self) -> list[_Process]:
        out: list[_Process] = list(self.workers.values())
        if self.preset_slot is not None:
            out.append(self.preset_slot)
        return out

    def _on_drone_exit(self, proc: _Process) -> None:
        rc = proc.proc.returncode
        outcome = _EXIT_TO_OUTCOME.get(rc, f"exit:{rc}")
        latency = time.time() - proc.spawned_at
        if proc.role == "preset:gap_finding":
            self._on_gf_exit(proc, outcome)
        elif proc.role == "preset:alignment":
            self._on_alignment_exit(proc, outcome)
        else:
            self._on_worker_exit(proc, outcome)
        self._emit(
            "drone.reaped",
            role=proc.role,
            gap_id=proc.gap_id,
            tick=proc.tick,
            outcome=outcome,
            exit_code=rc,
            latency_s=round(latency, 2),
        )

    def _on_gf_exit(self, proc: _Process, outcome: str) -> None:
        self.counters.gf_count += 1
        if outcome == "error":
            self.counters.consecutive_gf_errors += 1
            return
        self.counters.consecutive_gf_errors = 0
        gf_findings = [
            f
            for f in self.store.all_findings()
            if f.tick == proc.tick and f.author is FindingAuthor.gap_finding
        ]
        non_noop = any(f.kind.value != "noop" for f in gf_findings)
        for f in gf_findings:
            self.counters.gf_verbs[f.kind.value] += 1
        if not gf_findings or not non_noop:
            self.counters.consecutive_noops += 1
        else:
            self.counters.consecutive_noops = 0

    def _on_alignment_exit(self, proc: _Process, outcome: str) -> None:
        self.counters.align_count += 1
        if outcome == "error":
            return
        align_findings = [
            f
            for f in self.store.all_findings()
            if f.tick == proc.tick and f.author is FindingAuthor.alignment
        ]
        for f in align_findings:
            self.counters.align_kinds[f.kind.value] += 1

    def _on_worker_exit(self, proc: _Process, outcome: str) -> None:
        self.counters.worker_count += 1
        self.counters.worker_outcomes[outcome] += 1

    # ---- Scenario events --------------------------------------------------

    def _inject_pending_events(self) -> None:
        gf = self.counters.gf_count
        while self.pending_events and self.pending_events[0]["at_gf_tick"] <= gf + 1:
            ev = self.pending_events.pop(0)
            self.tick += 1
            f = inject_event(self.store, ev, tick=self.tick)
            self.counters.events_fired += 1
            self._emit(
                "scenario.inject",
                tick=self.tick,
                author=ev["author"],
                kind=ev["kind"],
                finding_id=f.id,
            )

    # ---- Preset slot ------------------------------------------------------

    def _maybe_spawn_preset(self) -> None:
        if self.preset_slot is not None:
            return
        # Operator force-tick wins over cadence.
        forced = None
        if self.control is not None and hasattr(self.control, "take_force_role"):
            forced = self.control.take_force_role()
        if forced == "alignment":
            self._spawn_preset(PRESET_ALIGNMENT, role="preset:alignment")
            self.counters.last_alignment_gf = self.counters.gf_count
            return
        if forced == "gap_finding":
            self._spawn_preset(PRESET_GAP_FINDING, role="preset:gap_finding")
            return
        # In infinite mode we don't honor the max_gf bailout — the operator
        # tunes ceilings, not iteration count.
        if (
            (self.control is None or not getattr(self.control, "infinite_mode", False))
            and self.counters.gf_count >= self.max_gf
        ):
            return
        if self._alignment_due():
            self._spawn_preset(PRESET_ALIGNMENT, role="preset:alignment")
            self.counters.last_alignment_gf = self.counters.gf_count
        else:
            self._spawn_preset(PRESET_GAP_FINDING, role="preset:gap_finding")

    def _alignment_due(self) -> bool:
        gf = self.counters.gf_count
        return (
            gf > 0
            and gf % self.align_every == 0
            and self.counters.last_alignment_gf != gf
        )

    def _spawn_preset(self, preset_kind: str, *, role: str) -> None:
        preset = self.store.get_preset(preset_kind)
        if preset is None:
            raise RuntimeError(
                f"preset {preset_kind!r} not minted; run init_collective_mind"
            )
        self.tick += 1
        self.preset_slot = self._spawn(
            gap=preset,
            role=role,
            max_turns=self.preset_max_turns,
        )

    # ---- Workers ----------------------------------------------------------

    def _maybe_spawn_workers(self) -> None:
        while len(self.workers) < self.max_workers:
            target = self._pick_next_worker_target()
            if target is None:
                return
            self.tick += 1
            self.attempted_gap_ids.add(target.id)
            self.workers[target.id] = self._spawn(
                gap=target,
                role="worker",
                max_turns=self.worker_max_turns,
            )

    def _pick_next_worker_target(self) -> Gap | None:
        leaves = self.store.leaves()
        in_flight = set(self.workers.keys())
        candidates = [g for g in leaves if g.id not in in_flight]
        # Skip gaps whose claim is held by someone else (race protection;
        # try_acquire in the runner is the authoritative check).
        unclaimed = [
            g for g in candidates
            if self.signals.get_claim("gap", g.id) is None
        ]
        for g in unclaimed:
            if g.id not in self.attempted_gap_ids:
                return g
        for g in unclaimed:
            return g
        return None

    # ---- Subprocess plumbing ----------------------------------------------

    def _spawn(self, *, gap: Gap, role: str, max_turns: int) -> _Process:
        drone_id = str(uuid4())
        tape_path = self.tape_dir / f"{drone_id}.jsonl"
        # Tier routing.
        # - Workers: resolve gap.model_tier within the operator's provider
        #   (with overrides), so cheap gaps go to nano/mini and hard gaps
        #   go to advanced/frontier.
        # - Presets (GF, Alignment): always frontier of operator's provider —
        #   these are persistent and structural, worth the cost.
        # On any failure, fall back to the scheduler default (provider, model).
        worker_provider = self.provider
        worker_model = self.model
        if self._registry is not None:
            try:
                if role == "worker":
                    entry = self._registry.resolve_for_gap(
                        gap,
                        self.provider,
                        overrides=self._tier_overrides or None,
                    )
                else:
                    # Presets pin to ``advanced`` tier (a strong-but-not-
                    # flagship model — e.g. Sonnet on Anthropic, GPT-5-4 on
                    # OpenAI). Frontier-tier presets (Opus) burned cost
                    # disproportionately during structural steering, which
                    # is the wrong gold-plate for the operator default.
                    # Override the preset tier per-provider in Settings →
                    # tier overrides if you want flagship structural work.
                    from drone_graph.gaps.records import ModelTier as _MT
                    entry = self._registry.resolve_for_tier(
                        _MT.advanced,
                        self.provider,
                    )
                worker_provider = entry.provider
                worker_model = entry.vendor_model_id
            except Exception:
                pass
        cmd = [
            sys.executable,
            "-m",
            "drone_graph.drones.runner",
            "--gap-id", gap.id,
            "--provider", worker_provider.value,
            "--model", worker_model,
            "--max-turns", str(max_turns),
            "--tick", str(self.tick),
            "--tape-path", str(tape_path),
            "--signal-db", str(self.signal_db),
            "--run-id", self.run_id,
        ]
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        spawned_at = time.time()
        self._emit(
            "drone.spawn",
            role=role,
            gap_id=gap.id,
            tick=self.tick,
            pid=proc.pid,
            tape=str(tape_path),
            provider=worker_provider.value,
            model=worker_model,
            model_tier=gap.model_tier.value,
        )
        return _Process(
            role=role,
            gap_id=gap.id,
            tick=self.tick,
            tape_path=tape_path,
            proc=proc,
            spawned_at=spawned_at,
            findings_before=len(self.store.all_findings()),
            provider=worker_provider.value,
            model=worker_model,
            model_tier=gap.model_tier.value,
        )

    def _drain_inflight(self) -> None:
        """Cooperative shutdown: signal cancel, wait briefly, then SIGKILL."""
        in_flight = list(self.workers.values())
        if self.preset_slot is not None:
            in_flight.append(self.preset_slot)
        if not in_flight:
            return
        for proc in in_flight:
            self.signals.signal_cancel("gap", proc.gap_id)
            proc.cancel_signaled_at = time.time()
        self._wait_for_exits(in_flight, timeout_s=SOFTKILL_GRACE_S)
        for proc in in_flight:
            if proc.proc.poll() is None:
                proc.proc.send_signal(signal.SIGTERM)
        self._wait_for_exits(in_flight, timeout_s=SOFTKILL_GRACE_S)
        for proc in in_flight:
            if proc.proc.poll() is None:
                proc.proc.kill()
        for proc in in_flight:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.proc.wait(timeout=2.0)
            self._on_drone_exit(proc)
        self.workers.clear()
        self.preset_slot = None

    def _wait_for_exits(
        self, procs: list[_Process], timeout_s: float
    ) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if all(p.proc.poll() is not None for p in procs):
                return
            time.sleep(0.1)

    # ---- Stop predicate ---------------------------------------------------

    def _should_stop(self) -> bool:
        if self.stop_reason:
            return True
        # Externally-driven mission-control scheduler: the only way out is the
        # operator requesting stop. The legacy noop-streak and max-gf bailouts
        # are infinite-mode antipatterns — there's no "end state" in that mode.
        if self.control is not None and getattr(self.control, "stop_requested", False):
            self.stop_reason = "stop requested by controller"
            return True
        if self.control is not None and getattr(self.control, "infinite_mode", False):
            self._maybe_downshift_tick()
            return False
        if self.counters.consecutive_gf_errors >= 3:
            self.stop_reason = "3 consecutive gap finding errors"
            return True
        if (
            self.counters.gf_count >= self.max_gf
            and not self.workers
            and self.preset_slot is None
        ):
            self.stop_reason = f"max_gf_invocations ({self.max_gf})"
            return True
        if (
            self.counters.consecutive_noops >= NOOP_STREAK_TO_STOP
            and not self.workers
            and self.preset_slot is None
            and not self.pending_events
            and self._pick_next_worker_target() is None
        ):
            self.stop_reason = (
                f"{NOOP_STREAK_TO_STOP} consecutive GF noops, no work pending"
            )
            return True
        return False

    def _maybe_downshift_tick(self) -> None:
        """In infinite mode, slow the tick when the swarm has nothing to do —
        and snap back to active cadence the moment work or input lands."""
        if self.control is None:
            return
        resting = (
            self.counters.consecutive_noops >= NOOP_STREAK_TO_STOP
            and not self.workers
            and self.preset_slot is None
            and not self.pending_events
            and self._pick_next_worker_target() is None
        )
        target = RESTING_TICK_S if resting else self.tick_s
        current = getattr(self.control, "tick_s", target)
        if abs(current - target) > 1e-3 and hasattr(self.control, "set_tick_s"):
            self.control.set_tick_s(target)
            self._emit("scheduler.tick_cadence", tick_s=target, resting=resting)

    # ---- Tape -------------------------------------------------------------

    def _emit(self, event: str, **fields: Any) -> None:
        if self.tape is not None:
            self.tape.emit(event, **fields)


# ---- Module entry point ---------------------------------------------------


def _resolve_substrate() -> Substrate:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "drone-graph-dev")
    return Substrate(uri, user, password)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m drone_graph.orchestrator.scheduler",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--scenario",
        default=None,
        help=(
            "Scenario stem (e.g. coffee-pivot-b2b). Omit to run against the "
            "current persistent graph. Available: " + ", ".join(available_scenarios())
        ),
    )
    ap.add_argument("--model", default=None)
    ap.add_argument(
        "--provider", default=None, choices=[p.value for p in Provider]
    )
    ap.add_argument("--out", default=None, help="Output dir.")
    ap.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS)
    ap.add_argument("--max-gf", type=int, default=DEFAULT_MAX_GF)
    ap.add_argument("--align-every", type=int, default=DEFAULT_ALIGNMENT_EVERY)
    ap.add_argument("--target-leaves", type=int, default=DEFAULT_TARGET_LEAVES)
    ap.add_argument("--tick-s", type=float, default=DEFAULT_TICK_S)
    ap.add_argument(
        "--worker-max-turns", type=int, default=DEFAULT_WORKER_MAX_TURNS
    )
    ap.add_argument(
        "--preset-max-turns", type=int, default=DEFAULT_PRESET_MAX_TURNS
    )
    ap.add_argument(
        "--signal-db", type=Path, default=None,
        help="Sidecar SQLite path (default: var/signals.db).",
    )
    ap.add_argument(
        "--max-cost-usd", type=float, default=None,
        help="Refuse drone turns once total swarm spend would exceed this.",
    )
    ap.add_argument(
        "--reset-signals", action="store_true",
        help="Wipe the sidecar before starting.",
    )
    ap.add_argument(
        "--reset-graph", action="store_true",
        help="DETACH DELETE all nodes before starting (also implied by --scenario).",
    )
    return ap


def main(argv: list[str] | None = None) -> None:
    from dotenv import load_dotenv

    # override=True so an empty env var in the parent shell doesn't shadow
    # the real value sitting in .env.
    load_dotenv(override=True)
    args = _build_parser().parse_args(argv)

    provider = Provider(args.provider) if args.provider else None
    resolved_provider, resolved_model = resolve_orchestrator_provider_model(
        provider, args.model
    )

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + str(uuid4())[:8]
    if args.out is None:
        label = args.scenario or "persistent"
        out_dir = Path("var") / "runs" / f"{label}-{resolved_model}-{run_id}"
    else:
        out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tape = EventTape(out_dir / "scheduler-tape.jsonl")

    substrate = _resolve_substrate()
    if args.reset_graph or args.scenario is not None:
        substrate.execute_write("MATCH (n) DETACH DELETE n")

    target_leaves = args.target_leaves
    pending_events: list[dict[str, Any]] = []
    scenario_data: dict[str, Any] | None = None
    if args.scenario is not None:
        scenario_data = load_scenario(args.scenario)
        target_leaves = int(scenario_data.get("target_leaves", target_leaves))

    store, _tool_store = init_collective_mind(substrate, target_leaves=target_leaves)

    if scenario_data is not None:
        intent, criteria = load_root_seed(scenario_data["root"])
        store.create_root(intent=intent, criteria=criteria)
        pending_events = sorted(
            scenario_data.get("events", []),
            key=lambda e: e["at_gf_tick"],
        )

    signals = SQLiteSignalStore(
        args.signal_db if args.signal_db is not None else default_db_path()
    )
    if args.reset_signals:
        signals.reset_all()
    if args.max_cost_usd is not None:
        signals.init_run(run_id, ceiling_usd=args.max_cost_usd)

    sched = Scheduler(
        substrate=substrate,
        signals=signals,
        store=store,
        provider=resolved_provider,
        model=resolved_model,
        run_id=run_id,
        out_dir=out_dir,
        tape=tape,
        max_workers=args.max_workers,
        tick_s=args.tick_s,
        align_every=args.align_every,
        max_gf=args.max_gf,
        worker_max_turns=args.worker_max_turns,
        preset_max_turns=args.preset_max_turns,
        signal_db=args.signal_db,
        scenario_events=pending_events,
        cost_ceiling_usd=args.max_cost_usd,
    )

    print(
        f"scheduler: scenario={args.scenario or 'persistent'} "
        f"model={resolved_model} workers={args.max_workers} "
        f"out={out_dir}"
    )
    try:
        sched.run()
    finally:
        signals.close()
        substrate.close()

    spent_usd = SQLiteSignalStore(
        args.signal_db if args.signal_db is not None else default_db_path()
    ).spent(run_id)
    summary = {
        "run_id": run_id,
        "stop_reason": sched.stop_reason or "natural exit",
        "gf": sched.counters.gf_count,
        "align": sched.counters.align_count,
        "workers": sched.counters.worker_count,
        "events_fired": sched.counters.events_fired,
        "gf_verbs": dict(sched.counters.gf_verbs),
        "align_kinds": dict(sched.counters.align_kinds),
        "worker_outcomes": dict(sched.counters.worker_outcomes),
        "spent_usd": round(spent_usd, 4),
        "ceiling_usd": args.max_cost_usd,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"done. {summary}")


if __name__ == "__main__":
    main()
