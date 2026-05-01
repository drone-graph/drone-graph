# Phase 3 — concurrency & signal protocol (plan)

Status as of 2026-04-27: not started. Phases 0–2 land in the unified runtime;
the orchestrator loop is single-threaded. This document is the build plan
agreed for Phase 3.

## Goal

Multiple drones run concurrently against the same collective mind without
double-work, lost findings, duplicate package installs, or two drones writing
the same file. A drone whose gap is retired mid-flight cancels cleanly and
leaves a `cancelled` finding rather than vanishing.

## Confirmed design decisions

Locked in conversation 2026-04-27. Not up for re-litigation without a specific
reason.

1. **Sidecar substrate.** Claims, leases, install registry, token buckets, and
   the cost meter live in a SQLite file at `var/signals.db` — *not* in Neo4j.
   Wrapped behind a `SignalStore` Protocol so a Redis-backed implementation
   can drop in for Phase 6+ multi-host. The graph stays the source of truth
   for gaps / findings / tools; the sidecar is the source of truth for
   transient coordination state.
2. **Subprocess-per-drone.** Drones run as `python -m
   drone_graph.drones.runner --gap-id <id>` subprocesses. The scheduler is the
   parent; it spawns, waits, reaps, SIGKILLs. Crash isolation comes for free;
   asyncio + Anthropic streaming + Neo4j driver entanglement is sidestepped;
   each drone already owns a real bash, so a process boundary is natural.
3. **Three claim kinds for v1: `gap`, `file`, `install`.** Ports / daemons /
   login sessions grow on demand later.
4. **Preset slot serializes between presets, parallel with workers.** At most
   one preset drone runs at a time (so GF can't fight Alignment for the same
   findings stream); workers run concurrently with the preset slot up to
   `--max-workers`.
5. **Soft-cancel at turn boundary, hard-kill after grace.** When GF retires a
   gap a worker holds, the scheduler sets a `cancelled` flag on the claim.
   The worker's heartbeat thread checks the flag at every turn boundary; on
   cancel it writes one `cancelled`-kind finding documenting state-of-the-
   world, releases the claim, and exits. If the worker doesn't release within
   90s of the cancel signal, the scheduler `SIGKILL`s the subprocess and the
   lease expires naturally.
6. **Findings are append-only even on cancel.** The substrate invariant
   stands: a worker's findings on a now-retired gap are kept as audit. The
   gap remains in the graph as a tombstone with the findings attached.
7. **Filled-then-retired is allowed.** If a worker writes `fill` just before
   the cancel signal arrives, auto-rollup may have already propagated. The
   rollup stands; Alignment can contest if the pivot invalidates it.
8. **Single-host for v1, multi-host-ready primitives.** SQLite + filesystem
   for v1. Sidecar interface and (optional) sha256-keyed artefact paths are
   designed so Phase 6/7 doesn't require a rewrite.
9. **Per-provider global token bucket + swarm cost ceiling, both in sidecar.**
   Drones acquire tokens before each turn (block up to N seconds). A swarm-
   wide `--max-cost-usd` ceiling refuses turns once crossed; the offending
   drone writes a `budget_exceeded` finding and exits.
10. **Per-drone tape files.** `var/tapes/<run_id>/<drone_id>.jsonl`. A merger
    renders the unified `timeline.md` at end of run. Avoids cross-process
    append contention without inventing a tape locking story.

## Sub-phase plan

| # | Ships | Verifies |
|---|---|---|
| 3.0 | Sidecar substrate (`SignalStore` + SQLite impl) + `reset-signals` CLI | Unit tests on the store |
| 3.1 | Gap claim / heartbeat / cancel-check wired into `run_drone` | Single drone still runs end-to-end; killed drone's claim expires; another drone can re-acquire |
| 3.2 | `python -m drone_graph.drones.runner` subprocess entry | Drone runs in isolation as a subprocess; tape lines emitted with pid |
| 3.3 | Scheduler replaces the single-threaded loop | ≥3 workers run concurrently against a hand-built scenario |
| 3.4 | Cancellation flow wired end-to-end | Worker cancelled mid-fill writes a `cancelled` finding, exits cleanly; orphan SIGKILLed after grace |
| 3.5 | `cm_acquire_file`, `cm_release_file`, `cm_install_package` builtin tools | File write-mode is exclusive; first install wins, second reads registered tool |
| 3.6 | Per-provider token bucket + swarm cost ceiling | 5 Sonnet drones don't trip rate limit; ceiling enforces refusal + `budget_exceeded` finding |
| 3.7 | `parallel-stress` scenario + `tests/test_signal_store.py` cross-process cancel test | Every Phase-3 acceptance bullet asserted |

Build in order. Each sub-phase leaves the system green; no half-finished
substrate hanging across sub-phases.

## New module: `src/drone_graph/signals/`

```
signals/
  __init__.py
  store.py        # SignalStore Protocol; ClaimRecord, InstallRecord dataclasses
  sqlite.py       # SQLiteSignalStore — default
  schema.sql      # DDL
```

Stdlib-only (no new dep). DB at `var/signals.db` by default; CLI flag `--signal-db` overrides.

### SQLite schema

```sql
CREATE TABLE claims (
  kind         TEXT NOT NULL,           -- 'gap' | 'file' | 'install'
  key          TEXT NOT NULL,           -- gap_id | abs path | install_key
  drone_id     TEXT NOT NULL,
  acquired_at  TEXT NOT NULL,           -- ISO8601 UTC
  expires_at   TEXT NOT NULL,           -- heartbeat updates
  cancelled    INTEGER NOT NULL DEFAULT 0,
  metadata     TEXT,                    -- JSON: install_commands, mode, etc
  PRIMARY KEY (kind, key)
);
CREATE INDEX idx_claims_drone ON claims(drone_id);
CREATE INDEX idx_claims_expires ON claims(expires_at);

CREATE TABLE provider_buckets (
  provider          TEXT PRIMARY KEY,
  capacity_tokens   INTEGER NOT NULL,
  tokens_remaining  INTEGER NOT NULL,
  refill_per_sec    REAL NOT NULL,
  last_refill_at    TEXT NOT NULL
);

CREATE TABLE cost_meter (
  run_id       TEXT PRIMARY KEY,
  ceiling_usd  REAL,
  spent_usd    REAL NOT NULL DEFAULT 0,
  started_at   TEXT NOT NULL
);
```

WAL mode + `BEGIN IMMEDIATE` for CAS writes; `busy_timeout 5000` is enough at
Phase-3 volumes.

### `SignalStore` interface (sketch)

```python
class SignalStore(Protocol):
    # Claims
    def try_acquire(self, kind: str, key: str, drone_id: str,
                    ttl_s: float, metadata: dict | None = None) -> bool: ...
    def heartbeat(self, kind: str, key: str, drone_id: str,
                  ttl_s: float) -> bool: ...      # False if reaped
    def release(self, kind: str, key: str, drone_id: str) -> None: ...
    def is_cancelled(self, kind: str, key: str) -> bool: ...
    def signal_cancel(self, kind: str, key: str) -> None: ...
    def reap_expired(self, now: datetime | None = None) -> list[ClaimRecord]: ...

    # Install registry — first claimer wins
    def install_lookup(self, key: str) -> InstallRecord | None: ...
    def install_register(self, key: str, drone_id: str,
                         install_commands: list[str], usage: str) -> bool: ...

    # Rate limit
    def take_tokens(self, provider: str, count: int,
                    timeout_s: float = 0.0) -> bool: ...
    def configure_bucket(self, provider: str, capacity: int,
                         refill_per_sec: float) -> None: ...

    # Cost meter
    def init_run(self, run_id: str, ceiling_usd: float | None) -> None: ...
    def add_cost(self, run_id: str, usd: float) -> bool: ...   # False if would exceed
    def spent(self, run_id: str) -> float: ...
```

## Cancellation flow

1. GF calls `apply_retire(gap_id, ...)` in [gaps/store.py](../src/drone_graph/gaps/store.py). The retire path additionally invokes `signals.signal_cancel("gap", gap_id)` for the gap itself and any cascade-retired descendants.
2. The worker's heartbeat thread (started in [drones/runtime.py](../src/drone_graph/drones/runtime.py)) does `signals.is_cancelled("gap", gap_id)` on its tick (every 20s) and the runtime checks at every turn boundary.
3. On cancel: runtime stops the message loop, writes a `cancelled`-kind finding (`summary` = "was attempting X, got as far as Y"), releases the claim, exits with status code 2.
4. If the worker doesn't release within 90s of cancel, the scheduler SIGKILLs the subprocess and lets the lease expire on its own.

New `FindingKind.CANCELLED` in [gaps/records.py](../src/drone_graph/gaps/records.py).

## Process model

- One scheduler process running `orchestrator.scheduler.run()`.
- Drones spawned as `subprocess.Popen([sys.executable, "-m", "drone_graph.drones.runner", "--gap-id", id, "--signal-db", path, "--tape-path", path, ...])`.
- Drones write to per-drone tape files. The current single-file
  [orchestrator/tape.py](../src/drone_graph/orchestrator/tape.py) `EventTape`
  is reshaped to per-drone files; a merger renders the unified
  `timeline.md` at end of run (or on demand).
- Drone exit codes:
  - `0` — fill or preset_done
  - `1` — fail
  - `2` — cancelled
  - `3` — max_turns
  - `4` — error (unhandled exception, client failure)

## Scheduler

```
orchestrator/scheduler.py    # ~400-500 lines
```

```python
class Scheduler:
    max_workers: int = 4
    tick_s: float = 1.0
    align_every: int = 3
    preset_slot: Popen | None
    workers: dict[str, Popen]   # gap_id -> subprocess

    def run(self) -> None:
        while self._has_work():
            self._tick()
            time.sleep(self.tick_s)

    def _tick(self) -> None:
        self._reap_finished()
        self._reap_expired_claims()
        self._inject_pending_scenario_events()
        if self._preset_slot_free() and (gap := self._next_preset()):
            self._spawn_preset(gap)
        while len(self.workers) < self.max_workers:
            target = self._pick_next_worker_target()
            if target is None: break
            self._spawn_worker(target)
```

`_pick_next_worker_target` reuses the priority logic from [`_pick_worker_target` in loop.py](../src/drone_graph/orchestrator/loop.py) but filters out gaps with a live claim.

The existing [`run_combined_loop`](../src/drone_graph/orchestrator/loop.py)
becomes a thin shim over `Scheduler(max_workers=1, preset_inline=True)` to
preserve the single-threaded behavior for back-compat and debugging.

## File map

**New**

- `src/drone_graph/signals/{__init__,store,sqlite}.py`, `schema.sql`
- `src/drone_graph/drones/runner.py` (subprocess entry)
- `src/drone_graph/orchestrator/scheduler.py`
- `src/drone_graph/tools/builtins/concurrency.py` — `cm_acquire_file`, `cm_release_file`, `cm_install_package`
- `src/drone_graph/seeds/scenarios/parallel-stress.json`
- `tests/test_signal_store.py`, `tests/test_concurrency_tools.py`

**Modified**

- `drones/runtime.py` — claim acquire on entry, heartbeat thread, cancel-check at turn boundary, token-bucket gate before each API call, cost-meter add after each turn, claim release on exit, `cancelled` finding on cancel
- `gaps/records.py` — add `CANCELLED` to `FindingKind`
- `gaps/store.py` — `apply_retire` walks descendants and signals cancel on each held claim; `apply_fill` is a no-op if a `cancelled` finding for the same drone+gap already exists in the same tick (race protection)
- `orchestrator/tape.py` — per-drone tape files; new `merge_tapes()` helper
- `orchestrator/loop.py` — thin shim over `Scheduler(max_workers=1)`
- `cli.py` — `reset-signals` command; `--max-workers`, `--max-cost-usd`, `--signal-db` flags
- `tools/builtins/__init__.py` — register the 3 concurrency tools

## `parallel-stress` scenario

Designed to hit every claim type at once. Sketch:

- Root: "Build a small report comparing N CSV files against an HTTP API." Decomposes into ~7 leaves:
  - 4 leaves each fetch + transform a CSV (overlapping write to `var/runs/<run>/report.md` — file contention)
  - 2 leaves need `requests` and `pandas` installed (install-race contention)
  - 1 leaf needs a long bash run (>60s — exercises heartbeat / lease renewal)
- Scheduled mid-run: GF retires one of the in-flight leaves (exercises cancellation).

## Acceptance criteria

`tests/test_signal_store.py` + `tests/test_concurrency_tools.py` assert:

1. ≥5 workers run truly in parallel (overlap windows in tape).
2. No gap fills twice.
3. Two workers racing `cm_install_package("playwright")` produce one `:Tool` node, both succeed.
4. Two workers racing `cm_acquire_file(p, "write")` — exactly one gets it; the other blocks/fails per timeout.
5. SIGKILL a running drone; its claim expires; scheduler re-spawns a fresh drone for the gap; no lost-update on findings (CAS).
6. GF retires gap with held claim → worker writes `cancelled`, exits cleanly within grace; if worker hangs, SIGKILL fires after 90s; lease released.
7. `--max-cost-usd 0.10` causes a `budget_exceeded` finding when ceiling crossed; no further drones spawn.
8. Replay determinism: same scenario, fixed seed → same gap-tree shape (modulo timing-sensitive scheduler ordering, which is logged but not asserted).

## Defaults locked at implementation time

- Lease TTL: **60s**; heartbeat every **20s**; hard-kill grace after cancel: **90s**.
- Scheduler tick: **1s**.
- `max_workers` default: **4**; CLI override.
- Token bucket: capacity + refill from `model_registry` rate-limit fields when present, fallback to provider-published limits.
- Per-drone tape files in `var/tapes/<run_id>/<drone_id>.jsonl`; merger renders `timeline.md`.

## Out of scope (deferred per roadmap)

Skill authoring, vector search over tools, memory pruning preset, Secret
Store, `HumanActionRequired` gaps, multi-host (Redis sidecar swap-in is
*designed for* but not *built*).
