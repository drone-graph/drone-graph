from __future__ import annotations

import signal
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from drone_graph.drones import DroneResult, Provider, make_client, run_drone
from drone_graph.gaps import GapStatus, GapStore
from drone_graph.orchestrator.tape import EventTape, default_tape_path
from drone_graph.substrate import Substrate

RunDroneFn = Callable[..., DroneResult]


def run_once(
    substrate: Substrate,
    *,
    provider: Provider = Provider.anthropic,
    model: str = "claude-sonnet-4-6",
    tape: EventTape | None = None,
    run_drone_fn: RunDroneFn = run_drone,
    make_client_fn: Callable[[Provider, str], Any] = make_client,
) -> DroneResult | None:
    store = GapStore(substrate)
    gap = store.claim_next_ready()
    if gap is None:
        return None

    if tape is not None:
        tape.emit(
            "orchestrator.claim",
            gap_id=gap.id,
            attempt=gap.attempts,
        )

    client = make_client_fn(provider, model)
    result = run_drone_fn(gap.id, substrate=substrate, client=client, tape=tape)

    if result.status is GapStatus.failed:
        if store.should_retry(gap):
            store.mark_open(gap.id)
            if tape is not None:
                tape.emit(
                    "orchestrator.retry",
                    gap_id=gap.id,
                    attempts=gap.attempts,
                    error=result.error,
                )
        else:
            reason = result.error or "max retries exceeded"
            descendants = store.mark_failed(gap.id, reason=reason)
            if tape is not None:
                tape.emit(
                    "orchestrator.terminal_failure",
                    gap_id=gap.id,
                    reason=reason,
                    propagated=descendants,
                )

    return result


def run_forever(
    substrate: Substrate,
    *,
    provider: Provider = Provider.anthropic,
    model: str = "claude-sonnet-4-6",
    poll_interval_s: float = 2.0,
    tape: EventTape | None = None,
    run_drone_fn: RunDroneFn = run_drone,
) -> None:
    if tape is None:
        tape = EventTape(default_tape_path())

    stop = False

    def _handler(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    tape.emit("orchestrator.start", provider=provider.value, model=model)
    try:
        idle_ticks = 0
        while not stop:
            result = run_once(
                substrate,
                provider=provider,
                model=model,
                tape=tape,
                run_drone_fn=run_drone_fn,
            )
            if result is None:
                idle_ticks += 1
                ts = datetime.now(UTC).strftime("%H:%M:%S")
                print(
                    f"[{ts}] waiting for gaps… (tick {idle_ticks})",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(poll_interval_s)
            else:
                idle_ticks = 0
    finally:
        tape.emit("orchestrator.stop")
