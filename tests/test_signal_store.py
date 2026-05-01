from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from drone_graph.signals import SQLiteSignalStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteSignalStore]:
    s = SQLiteSignalStore(tmp_path / "signals.db")
    try:
        yield s
    finally:
        s.close()


# ---- Claims ---------------------------------------------------------------


def test_acquire_and_release(store: SQLiteSignalStore) -> None:
    assert store.try_acquire("gap", "g1", "drone-a", ttl_s=60) is True
    held = store.get_claim("gap", "g1")
    assert held is not None and held.drone_id == "drone-a"
    store.release("gap", "g1", "drone-a")
    assert store.get_claim("gap", "g1") is None


def test_acquire_blocks_second_drone_until_release(
    store: SQLiteSignalStore,
) -> None:
    assert store.try_acquire("gap", "g1", "drone-a", ttl_s=60) is True
    assert store.try_acquire("gap", "g1", "drone-b", ttl_s=60) is False
    store.release("gap", "g1", "drone-a")
    assert store.try_acquire("gap", "g1", "drone-b", ttl_s=60) is True


def test_metadata_round_trips(store: SQLiteSignalStore) -> None:
    meta = {"mode": "write", "purpose": "report"}
    store.try_acquire("file", "/tmp/x", "drone-a", ttl_s=60, metadata=meta)
    held = store.get_claim("file", "/tmp/x")
    assert held is not None
    assert held.metadata == meta


def test_heartbeat_extends_ttl(store: SQLiteSignalStore) -> None:
    store.try_acquire("gap", "g1", "drone-a", ttl_s=0.5)
    before = store.get_claim("gap", "g1")
    assert before is not None
    time.sleep(0.05)
    assert store.heartbeat("gap", "g1", "drone-a", ttl_s=60) is True
    after = store.get_claim("gap", "g1")
    assert after is not None
    assert after.expires_at > before.expires_at


def test_heartbeat_by_wrong_drone_fails(store: SQLiteSignalStore) -> None:
    store.try_acquire("gap", "g1", "drone-a", ttl_s=60)
    assert store.heartbeat("gap", "g1", "drone-b", ttl_s=60) is False


def test_heartbeat_after_expiry_fails(store: SQLiteSignalStore) -> None:
    store.try_acquire("gap", "g1", "drone-a", ttl_s=0.05)
    time.sleep(0.1)
    assert store.heartbeat("gap", "g1", "drone-a", ttl_s=60) is False


def test_expired_claim_can_be_reacquired(store: SQLiteSignalStore) -> None:
    store.try_acquire("gap", "g1", "drone-a", ttl_s=0.05)
    time.sleep(0.1)
    assert store.try_acquire("gap", "g1", "drone-b", ttl_s=60) is True
    held = store.get_claim("gap", "g1")
    assert held is not None and held.drone_id == "drone-b"


def test_reap_expired_returns_and_deletes(store: SQLiteSignalStore) -> None:
    store.try_acquire("gap", "g1", "drone-a", ttl_s=0.05)
    store.try_acquire("gap", "g2", "drone-b", ttl_s=60)
    time.sleep(0.1)
    reaped = store.reap_expired()
    assert {c.key for c in reaped} == {"g1"}
    assert store.get_claim("gap", "g1") is None
    assert store.get_claim("gap", "g2") is not None


def test_signal_cancel_and_check(store: SQLiteSignalStore) -> None:
    store.try_acquire("gap", "g1", "drone-a", ttl_s=60)
    assert store.is_cancelled("gap", "g1") is False
    store.signal_cancel("gap", "g1")
    assert store.is_cancelled("gap", "g1") is True


def test_signal_cancel_no_claim_is_noop(store: SQLiteSignalStore) -> None:
    store.signal_cancel("gap", "missing")
    assert store.is_cancelled("gap", "missing") is False


def test_claims_by_drone(store: SQLiteSignalStore) -> None:
    store.try_acquire("gap", "g1", "drone-a", ttl_s=60)
    store.try_acquire("file", "/tmp/x", "drone-a", ttl_s=60)
    store.try_acquire("gap", "g2", "drone-b", ttl_s=60)
    held = store.claims_by_drone("drone-a")
    assert {(c.kind, c.key) for c in held} == {("gap", "g1"), ("file", "/tmp/x")}


# ---- Install registry -----------------------------------------------------


def test_install_lookup_miss_then_register_then_hit(
    store: SQLiteSignalStore,
) -> None:
    assert store.install_lookup("playwright") is None
    ok = store.install_register(
        "playwright",
        "drone-a",
        ["pip install playwright", "playwright install chromium"],
        usage="from playwright.sync_api import sync_playwright",
    )
    assert ok is True
    rec = store.install_lookup("playwright")
    assert rec is not None
    assert rec.installed_by == "drone-a"
    assert rec.install_commands[0] == "pip install playwright"
    assert rec.usage and "playwright" in rec.usage


def test_install_register_second_caller_loses(
    store: SQLiteSignalStore,
) -> None:
    assert store.install_register("pandas", "drone-a", ["pip install pandas"])
    assert store.install_register("pandas", "drone-b", ["pip install pandas"]) is False
    rec = store.install_lookup("pandas")
    assert rec is not None and rec.installed_by == "drone-a"


def test_install_register_releases_in_progress_claim(
    store: SQLiteSignalStore,
) -> None:
    store.try_acquire("install", "pandas", "drone-a", ttl_s=600)
    assert store.get_claim("install", "pandas") is not None
    store.install_register("pandas", "drone-a", ["pip install pandas"])
    assert store.get_claim("install", "pandas") is None


# ---- Token bucket ---------------------------------------------------------


def test_take_tokens_passes_through_when_no_bucket(
    store: SQLiteSignalStore,
) -> None:
    assert store.take_tokens("anthropic", 1000) is True


def test_take_tokens_respects_capacity(store: SQLiteSignalStore) -> None:
    store.configure_bucket("anthropic", capacity_tokens=100, refill_per_sec=0.0)
    assert store.take_tokens("anthropic", 80) is True
    assert store.take_tokens("anthropic", 30, timeout_s=0.0) is False
    assert store.take_tokens("anthropic", 20) is True


def test_take_tokens_blocks_then_succeeds_with_refill(
    store: SQLiteSignalStore,
) -> None:
    store.configure_bucket("anthropic", capacity_tokens=10, refill_per_sec=200.0)
    assert store.take_tokens("anthropic", 10) is True
    t0 = time.monotonic()
    assert store.take_tokens("anthropic", 10, timeout_s=2.0) is True
    elapsed = time.monotonic() - t0
    assert 0.02 < elapsed < 1.5  # refill of 10 at 200/s ~= 50ms


def test_take_tokens_timeout(store: SQLiteSignalStore) -> None:
    store.configure_bucket("anthropic", capacity_tokens=10, refill_per_sec=1.0)
    store.take_tokens("anthropic", 10)
    t0 = time.monotonic()
    assert store.take_tokens("anthropic", 100, timeout_s=0.3) is False
    elapsed = time.monotonic() - t0
    assert 0.25 < elapsed < 1.0


# ---- Cost meter -----------------------------------------------------------


def test_cost_meter_passes_through_when_uninitialized(
    store: SQLiteSignalStore,
) -> None:
    assert store.add_cost("run-x", 1.23) is True
    assert store.spent("run-x") == 0.0


def test_cost_meter_accumulates(store: SQLiteSignalStore) -> None:
    store.init_run("run-x", ceiling_usd=None)
    assert store.add_cost("run-x", 0.40)
    assert store.add_cost("run-x", 0.10)
    assert store.spent("run-x") == pytest.approx(0.50)


def test_cost_meter_ceiling_signals_overspend_but_records(
    store: SQLiteSignalStore,
) -> None:
    """Spend is always recorded (the API call already happened); add_cost
    returns False once the cumulative spend has crossed the ceiling so the
    scheduler can stop spawning further work."""
    store.init_run("run-x", ceiling_usd=1.00)
    assert store.add_cost("run-x", 0.80) is True
    assert store.add_cost("run-x", 0.30) is False  # crossed
    assert store.spent("run-x") == pytest.approx(1.10)
    assert store.add_cost("run-x", 0.15) is False  # still over
    assert store.spent("run-x") == pytest.approx(1.25)


# ---- Concurrency ----------------------------------------------------------


def test_concurrent_acquire_only_one_wins(store: SQLiteSignalStore) -> None:
    """Drive the SignalStore from many threads racing for the same claim."""
    winners: list[str] = []
    barrier = threading.Barrier(8)

    def worker(name: str) -> None:
        barrier.wait()
        if store.try_acquire("gap", "contested", name, ttl_s=60):
            winners.append(name)

    threads = [
        threading.Thread(target=worker, args=(f"drone-{i}",)) for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winners) == 1


def test_cross_process_via_separate_connections(tmp_path: Path) -> None:
    """Open two independent SQLiteSignalStore instances on the same file —
    proxy for two subprocess drones — and verify exclusion."""
    db = tmp_path / "signals.db"
    a = SQLiteSignalStore(db)
    b = SQLiteSignalStore(db)
    try:
        assert a.try_acquire("gap", "g1", "drone-a", ttl_s=60) is True
        assert b.try_acquire("gap", "g1", "drone-b", ttl_s=60) is False
        # Cancel from B is visible to A.
        b.signal_cancel("gap", "g1")
        assert a.is_cancelled("gap", "g1") is True
    finally:
        a.close()
        b.close()


def test_reset_all_wipes_everything(store: SQLiteSignalStore) -> None:
    store.try_acquire("gap", "g1", "drone-a", ttl_s=60)
    store.install_register("p", "drone-a", ["pip install p"])
    store.configure_bucket("anthropic", 10, 1.0)
    store.init_run("run-x", 1.0)
    store.reset_all()
    assert store.get_claim("gap", "g1") is None
    assert store.install_lookup("p") is None
    assert store.spent("run-x") == 0.0


# ---- Runtime heartbeat thread ---------------------------------------------


def test_heartbeat_thread_renews_lease(store: SQLiteSignalStore) -> None:
    from drone_graph.drones.runtime import _Heartbeat

    store.try_acquire("gap", "g1", "drone-a", ttl_s=0.5)
    hb = _Heartbeat(
        store, "gap", "g1", "drone-a", ttl_s=0.5, period_s=0.1
    )
    hb.start()
    try:
        time.sleep(0.6)  # would have expired without heartbeat
        held = store.get_claim("gap", "g1")
        assert held is not None
        assert held.drone_id == "drone-a"
        assert hb.lost is False
    finally:
        hb.stop()


def test_heartbeat_thread_detects_lost_lease(store: SQLiteSignalStore) -> None:
    from drone_graph.drones.runtime import _Heartbeat

    store.try_acquire("gap", "g1", "drone-a", ttl_s=0.05)
    hb = _Heartbeat(
        store, "gap", "g1", "drone-a", ttl_s=0.05, period_s=0.2
    )
    time.sleep(0.1)  # let claim expire before the heartbeat thread starts
    store.reap_expired()
    hb.start()
    try:
        time.sleep(0.4)
        assert hb.lost is True
    finally:
        hb.stop()


# ---- Real subprocess cancel propagation -----------------------------------


def test_cancel_propagates_to_real_subprocess(tmp_path: Path) -> None:
    """End-to-end: a cancel signal raised from this process must be visible
    to a separate Python subprocess holding the claim, and the subprocess
    must exit cleanly on its next poll."""
    import subprocess
    import sys
    import textwrap

    db = tmp_path / "signals.db"
    store = SQLiteSignalStore(db)
    try:
        worker_src = textwrap.dedent(
            f"""
            import sys, time
            sys.path.insert(0, {str(Path("src").resolve())!r})
            from drone_graph.signals import SQLiteSignalStore
            s = SQLiteSignalStore({str(db)!r})
            assert s.try_acquire("gap", "gC", "drone-soft", ttl_s=60)
            for _ in range(200):
                if s.is_cancelled("gap", "gC"):
                    s.release("gap", "gC", "drone-soft")
                    sys.exit(2)
                time.sleep(0.05)
            sys.exit(0)
            """
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", worker_src],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(50):
                if store.get_claim("gap", "gC") is not None:
                    break
                time.sleep(0.05)
            assert store.get_claim("gap", "gC") is not None
            store.signal_cancel("gap", "gC")
            rc = proc.wait(timeout=5.0)
            assert rc == 2
            assert store.get_claim("gap", "gC") is None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2.0)
    finally:
        store.close()
