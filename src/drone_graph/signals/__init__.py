"""Sidecar substrate for transient coordination state.

Phase 3 keeps gap/finding/tool data in Neo4j (the source of truth) and moves
the high-rate, short-lived coordination state — claims, leases, install
registry, token buckets, the swarm cost meter — into a separate sidecar so
sub-second writes don't punish the graph.

The default implementation is a single SQLite file at ``var/signals.db``
(stdlib only). The ``SignalStore`` Protocol is the contract a future Redis
implementation drops into for multi-host (Phase 6+).
"""

from drone_graph.signals.sqlite import SQLiteSignalStore
from drone_graph.signals.store import (
    ClaimRecord,
    InstallRecord,
    SignalStore,
    default_db_path,
)

__all__ = [
    "ClaimRecord",
    "InstallRecord",
    "SQLiteSignalStore",
    "SignalStore",
    "default_db_path",
]
