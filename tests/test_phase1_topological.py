"""Phase 1 acceptance: topological ordering, retries, and failure propagation.

Uses a stub drone that skips model calls so the test is free and deterministic.
Still hits real Neo4j, per the roadmap ("integration tests hit real Neo4j, no mocks").
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from drone_graph.drones import DroneResult, Provider, Usage
from drone_graph.drones.runtime import _write_drone_and_finding
from drone_graph.gaps import Finding, Gap, GapStatus, GapStore
from drone_graph.orchestrator.loop import run_once
from drone_graph.substrate import Substrate

pytestmark = pytest.mark.integration


@dataclass
class StubDrone:
    """Injected in place of run_drone. Writes a close or failed outcome per behaviors."""

    behaviors: dict[str, list[str]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def __call__(
        self,
        gap_id: str,
        *,
        substrate: Substrate,
        client: Any,
        tape: Any = None,
        **_: Any,
    ) -> DroneResult:
        self.calls.append(gap_id)
        # Pop the next behavior for this gap; default to "close".
        steps = self.behaviors.get(gap_id, [])
        outcome = steps.pop(0) if steps else "close"

        drone_id = str(uuid4())
        now = datetime.now(UTC).isoformat()

        if outcome == "close":
            finding = Finding(kind="close", summary=f"stub close {gap_id}")
            _write_drone_and_finding(
                substrate=substrate,
                drone_id=drone_id,
                gap_id=gap_id,
                spawned_at=now,
                died_at=now,
                provider=Provider.anthropic,
                model="stub",
                usage=Usage(),
                cost=0.0,
                finding=finding,
                final_status=GapStatus.closed,
            )
            return DroneResult(
                drone_id=drone_id,
                gap_id=gap_id,
                status=GapStatus.closed,
                finding_id=finding.id,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                turns_used=1,
            )

        # outcome == "fail"
        _write_drone_and_finding(
            substrate=substrate,
            drone_id=drone_id,
            gap_id=gap_id,
            spawned_at=now,
            died_at=now,
            provider=Provider.anthropic,
            model="stub",
            usage=Usage(),
            cost=0.0,
            finding=None,
            final_status=GapStatus.failed,
        )
        return DroneResult(
            drone_id=drone_id,
            gap_id=gap_id,
            status=GapStatus.failed,
            finding_id=None,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            turns_used=1,
            error="stub failure",
        )


def _drive(substrate: Substrate, stub: StubDrone, max_iters: int = 50) -> None:
    """Run run_once until the orchestrator idles, faking make_client."""
    for _ in range(max_iters):
        result = run_once(
            substrate,
            run_drone_fn=stub,
            make_client_fn=lambda _p, _m: None,
        )
        if result is None:
            return
        # Ensure distinct spawned_at timestamps across drones.
        time.sleep(0.005)
    raise AssertionError("orchestrator did not drain within max_iters")


def _drone_order(substrate: Substrate) -> list[str]:
    """Return gap ids in the order drones were spawned against them."""
    rows = substrate.execute_read(
        "MATCH (d:Drone) RETURN d.gap_id AS gap_id ORDER BY d.spawned_at ASC",
    )
    return [r["gap_id"] for r in rows]


def test_topological_execution_order_regardless_of_insertion(substrate: Substrate) -> None:
    """A → B → C executes in that order even though C was created first.

    Without topology, `claim_next_ready` falls back to created_at ASC and would
    claim C first. The BLOCKED_BY wiring must force A → B → C.
    """
    store = GapStore(substrate)
    # Create in REVERSE dependency order so created_at(C) < created_at(B) < created_at(A).
    c = Gap(description="C")
    store.create(c)
    time.sleep(0.01)
    b = Gap(description="B")
    store.create(b)
    time.sleep(0.01)
    a = Gap(description="A")
    store.create(a)
    # Wire blockers after all gaps exist (forward refs aren't allowed at create-time).
    store.add_blocker(b.id, a.id)
    store.add_blocker(c.id, b.id)

    stub = StubDrone()
    _drive(substrate, stub)

    order = _drone_order(substrate)
    assert order == [a.id, b.id, c.id], f"execution order was {order}"
    assert stub.calls == [a.id, b.id, c.id]

    for g in (a, b, c):
        reloaded = store.get(g.id)
        assert reloaded is not None
        assert reloaded.status is GapStatus.closed
        assert reloaded.attempts == 1


def test_failure_retries_once_then_gives_up(substrate: Substrate) -> None:
    store = GapStore(substrate)
    g = Gap(description="flaky")
    store.create(g)

    # Two consecutive failures → terminal failure.
    stub = StubDrone(behaviors={g.id: ["fail", "fail"]})
    _drive(substrate, stub)

    reloaded = store.get(g.id)
    assert reloaded is not None
    assert reloaded.status is GapStatus.failed
    assert reloaded.attempts == 2
    assert reloaded.failure_reason == "stub failure"
    assert stub.calls == [g.id, g.id]


def test_failure_recovers_on_retry(substrate: Substrate) -> None:
    store = GapStore(substrate)
    g = Gap(description="transient")
    store.create(g)

    stub = StubDrone(behaviors={g.id: ["fail", "close"]})
    _drive(substrate, stub)

    reloaded = store.get(g.id)
    assert reloaded is not None
    assert reloaded.status is GapStatus.closed
    assert reloaded.attempts == 2


def test_failure_propagates_to_descendants(substrate: Substrate) -> None:
    store = GapStore(substrate)
    a = Gap(description="A")
    b = Gap(description="B")
    c = Gap(description="C")
    store.create(a)
    store.create(b, blocked_by=[a.id])
    store.create(c, blocked_by=[b.id])

    # A fails twice → terminal. B and C should be propagated-failed without dispatch.
    stub = StubDrone(behaviors={a.id: ["fail", "fail"]})
    _drive(substrate, stub)

    # Only A got drones.
    assert stub.calls == [a.id, a.id]

    a_reloaded = store.get(a.id)
    b_reloaded = store.get(b.id)
    c_reloaded = store.get(c.id)
    assert a_reloaded is not None and b_reloaded is not None and c_reloaded is not None
    assert a_reloaded.status is GapStatus.failed
    assert b_reloaded.status is GapStatus.failed
    assert c_reloaded.status is GapStatus.failed
    assert b_reloaded.failure_reason == f"blocker_failed:{a.id}"
    assert c_reloaded.failure_reason == f"blocker_failed:{a.id}"


def test_create_with_unknown_blocker_raises(substrate: Substrate) -> None:
    store = GapStore(substrate)
    g = Gap(description="dangling")
    with pytest.raises(ValueError, match="unknown blocker"):
        store.create(g, blocked_by=["does-not-exist"])


def test_ready_gaps_excludes_blocked(substrate: Substrate) -> None:
    store = GapStore(substrate)
    a = Gap(description="A")
    b = Gap(description="B")
    store.create(a)
    store.create(b, blocked_by=[a.id])

    ready = {g.id for g in store.ready_gaps()}
    assert ready == {a.id}
