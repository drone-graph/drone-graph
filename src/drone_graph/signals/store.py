"""SignalStore contract + record types.

The Protocol intentionally stays narrow. Every method is a single round-trip
to the sidecar and is safe to call from any process or thread that opened the
backing store.

Time is unix epoch seconds (float) throughout. ISO conversion is a display
concern that lives in the caller, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


def default_db_path() -> Path:
    """Default sidecar location: ``var/signals.db`` relative to cwd."""
    return Path("var") / "signals.db"


@dataclass(frozen=True)
class ClaimRecord:
    kind: str            # 'gap' | 'file' | 'install'
    key: str             # gap_id | abs path | install_key
    drone_id: str
    acquired_at: float   # unix epoch seconds
    expires_at: float
    cancelled: bool
    metadata: dict[str, Any] | None


@dataclass(frozen=True)
class InstallRecord:
    key: str
    installed_by: str        # drone_id of the installer
    installed_at: float
    install_commands: list[str]
    usage: str | None


class SignalStore(Protocol):
    """Cross-process coordination primitives.

    Implementations must be safe for concurrent use across processes opening
    the same backing store.
    """

    # ---- Claims (gap | file | install-in-progress) ------------------------

    def try_acquire(
        self,
        kind: str,
        key: str,
        drone_id: str,
        ttl_s: float,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Atomically acquire a claim. Returns False if another drone holds it
        and the existing claim is not yet expired."""
        ...

    def heartbeat(
        self, kind: str, key: str, drone_id: str, ttl_s: float
    ) -> bool:
        """Renew the lease on an owned claim. Returns False if the claim was
        reaped or now belongs to a different drone."""
        ...

    def release(self, kind: str, key: str, drone_id: str) -> None:
        """Release a claim this drone holds. No-op if not held."""
        ...

    def is_cancelled(self, kind: str, key: str) -> bool:
        """True if a soft-cancel signal has been raised on this claim."""
        ...

    def signal_cancel(self, kind: str, key: str) -> None:
        """Set the cancelled flag on a claim. No-op if no claim exists."""
        ...

    def reap_expired(self, now: float | None = None) -> list[ClaimRecord]:
        """Delete and return claims whose ``expires_at`` is in the past."""
        ...

    def get_claim(self, kind: str, key: str) -> ClaimRecord | None:
        """Read the current claim row, if any."""
        ...

    def claims_by_drone(self, drone_id: str) -> list[ClaimRecord]:
        """All live claims held by ``drone_id``."""
        ...

    def release_all_for_drone(self, drone_id: str) -> int:
        """Release every claim ``drone_id`` holds. Returns rows deleted."""
        ...

    # ---- Install registry (separate from in-progress install claims) ------

    def install_lookup(self, key: str) -> InstallRecord | None:
        """Return the completed install record for ``key`` if one exists."""
        ...

    def install_register(
        self,
        key: str,
        drone_id: str,
        install_commands: list[str],
        usage: str | None = None,
    ) -> bool:
        """Mark ``key`` as installed. Releases the in-progress claim if held.
        Returns False if ``key`` was already registered by someone else."""
        ...

    # ---- Per-provider token bucket ----------------------------------------

    def configure_bucket(
        self, provider: str, capacity_tokens: int, refill_per_sec: float
    ) -> None:
        """Create or update the bucket for ``provider``. Idempotent."""
        ...

    def take_tokens(
        self, provider: str, count: int, timeout_s: float = 0.0
    ) -> bool:
        """Consume ``count`` tokens, blocking up to ``timeout_s`` for refill.
        Returns False on timeout or True on success. Returns True immediately
        if no bucket is configured for the provider."""
        ...

    # ---- Swarm-wide cost meter --------------------------------------------

    def init_run(self, run_id: str, ceiling_usd: float | None) -> None:
        """Open a per-run cost meter. Idempotent."""
        ...

    def add_cost(self, run_id: str, usd: float) -> bool:
        """Add ``usd`` to the run's spend (always recorded). Returns False
        once the ceiling has been crossed so the caller stops spawning
        further work. Returns True if no meter exists for ``run_id``."""
        ...

    def spent(self, run_id: str) -> float:
        """Total spend recorded against ``run_id``. 0.0 if no meter."""
        ...

    # ---- Lifecycle --------------------------------------------------------

    def reset_all(self) -> None:
        """Wipe every table. Dev convenience; not safe in shared contexts."""
        ...

    def close(self) -> None:
        """Release the underlying connection."""
        ...
