from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from drone_graph.gaps.records import Gap, GapStatus, ModelTier
from drone_graph.substrate import Substrate

_MAX_ATTEMPTS = 2


def _to_native(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if hasattr(v, "to_native"):
        return v.to_native()
    return v


def _gap_from_node(node: Any) -> Gap:
    data: dict[str, Any] = {k: _to_native(v) for k, v in dict(node).items()}
    return Gap.model_validate(data)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class GapStore:
    """Typed access to the Gap subgraph.

    All inline gap Cypher lives here — callers speak in Gap objects and ids.
    """

    def __init__(self, substrate: Substrate) -> None:
        self.substrate = substrate

    def create(self, gap: Gap, blocked_by: list[str] | None = None) -> None:
        self.substrate.execute_write(
            "CREATE (g:Gap {"
            "  id: $id, description: $description, status: $status,"
            "  nl_criteria: $nl_criteria, model_tier: $model_tier,"
            "  created_at: datetime($created_at), attempts: $attempts"
            "})",
            id=gap.id,
            description=gap.description,
            status=gap.status.value,
            nl_criteria=gap.nl_criteria,
            model_tier=gap.model_tier.value,
            created_at=gap.created_at.isoformat(),
            attempts=gap.attempts,
        )
        if blocked_by:
            rows = self.substrate.execute_write(
                "MATCH (g:Gap {id: $id}) "
                "UNWIND $blockers AS bid "
                "MATCH (b:Gap {id: bid}) "
                "CREATE (g)-[:BLOCKED_BY]->(b) "
                "RETURN collect(b.id) AS linked",
                id=gap.id,
                blockers=blocked_by,
            )
            linked = set(rows[0]["linked"]) if rows else set()
            missing = [bid for bid in blocked_by if bid not in linked]
            if missing:
                raise ValueError(
                    f"cannot create gap {gap.id}: unknown blocker id(s) {missing}"
                )

    def add_blocker(self, gap_id: str, blocker_id: str) -> None:
        """Add a BLOCKED_BY edge between two existing gaps. Idempotent."""
        rows = self.substrate.execute_write(
            "MATCH (g:Gap {id: $gid}), (b:Gap {id: $bid}) "
            "MERGE (g)-[:BLOCKED_BY]->(b) "
            "RETURN g.id AS gid",
            gid=gap_id,
            bid=blocker_id,
        )
        if not rows:
            raise ValueError(f"add_blocker: unknown gap id(s) {gap_id!r}, {blocker_id!r}")

    def get(self, gap_id: str) -> Gap | None:
        rows = self.substrate.execute_read(
            "MATCH (g:Gap {id: $id}) RETURN g",
            id=gap_id,
        )
        return _gap_from_node(rows[0]["g"]) if rows else None

    def blockers_of(self, gap_id: str) -> list[str]:
        rows = self.substrate.execute_read(
            "MATCH (:Gap {id: $id})-[:BLOCKED_BY]->(b:Gap) RETURN b.id AS id",
            id=gap_id,
        )
        return [r["id"] for r in rows]

    def open_gaps(self) -> list[Gap]:
        rows = self.substrate.execute_read(
            "MATCH (g:Gap {status: $status}) RETURN g ORDER BY g.created_at ASC",
            status=GapStatus.open.value,
        )
        return [_gap_from_node(r["g"]) for r in rows]

    def ready_gaps(self) -> list[Gap]:
        rows = self.substrate.execute_read(
            "MATCH (g:Gap {status: $open}) "
            "WHERE NOT EXISTS { "
            "  MATCH (g)-[:BLOCKED_BY]->(b:Gap) WHERE b.status <> $closed "
            "} "
            "RETURN g ORDER BY g.created_at ASC",
            open=GapStatus.open.value,
            closed=GapStatus.closed.value,
        )
        return [_gap_from_node(r["g"]) for r in rows]

    def claim_next_ready(self) -> Gap | None:
        """Topological pick of the oldest ready gap, atomically flipped to in_progress.

        Ready = status=open AND every BLOCKED_BY target is closed.
        Increments `attempts` at claim time so the retry budget is visible on the node.
        """
        rows = self.substrate.execute_write(
            "MATCH (g:Gap {status: $open}) "
            "WHERE NOT EXISTS { "
            "  MATCH (g)-[:BLOCKED_BY]->(b:Gap) WHERE b.status <> $closed "
            "} "
            "WITH g ORDER BY g.created_at ASC LIMIT 1 "
            "SET g.status = $in_progress, "
            "    g.in_progress_at = datetime($now), "
            "    g.attempts = coalesce(g.attempts, 0) + 1 "
            "RETURN g",
            open=GapStatus.open.value,
            closed=GapStatus.closed.value,
            in_progress=GapStatus.in_progress.value,
            now=_now_iso(),
        )
        return _gap_from_node(rows[0]["g"]) if rows else None

    def mark_open(self, gap_id: str) -> None:
        """Reset a gap to open. Used for retries after a recoverable failure."""
        self.substrate.execute_write(
            "MATCH (g:Gap {id: $id}) SET g.status = $open",
            id=gap_id,
            open=GapStatus.open.value,
        )

    def mark_failed(self, gap_id: str, reason: str) -> list[str]:
        """Terminally fail a gap and cascade the failure to all dependents.

        The gap's own status is already `failed` (written by the drone runtime); this
        call records the reason and propagates failure transitively along BLOCKED_BY.
        Returns the ids of descendants that were flipped.
        """
        now = _now_iso()
        self.substrate.execute_write(
            "MATCH (g:Gap {id: $id}) "
            "SET g.status = $failed, "
            "    g.failure_reason = coalesce(g.failure_reason, $reason), "
            "    g.failed_at = datetime($now)",
            id=gap_id,
            failed=GapStatus.failed.value,
            reason=reason,
            now=now,
        )
        rows = self.substrate.execute_write(
            "MATCH (desc:Gap)-[:BLOCKED_BY*1..]->(root:Gap {id: $id}) "
            "WHERE desc.status IN [$open, $in_progress] "
            "SET desc.status = $failed, "
            "    desc.failure_reason = $desc_reason, "
            "    desc.failed_at = datetime($now) "
            "RETURN collect(DISTINCT desc.id) AS ids",
            id=gap_id,
            open=GapStatus.open.value,
            in_progress=GapStatus.in_progress.value,
            failed=GapStatus.failed.value,
            desc_reason=f"blocker_failed:{gap_id}",
            now=now,
        )
        return list(rows[0]["ids"]) if rows else []

    def should_retry(self, gap: Gap) -> bool:
        return gap.attempts < _MAX_ATTEMPTS

    def by_tier(self, tier: ModelTier) -> list[Gap]:
        rows = self.substrate.execute_read(
            "MATCH (g:Gap {model_tier: $tier}) RETURN g ORDER BY g.created_at ASC",
            tier=tier.value,
        )
        return [_gap_from_node(r["g"]) for r in rows]

    def by_age(self, older_than: datetime) -> list[Gap]:
        """Gaps whose created_at is strictly older than the cutoff."""
        rows = self.substrate.execute_read(
            "MATCH (g:Gap) WHERE g.created_at < datetime($cutoff) "
            "RETURN g ORDER BY g.created_at ASC",
            cutoff=older_than.isoformat(),
        )
        return [_gap_from_node(r["g"]) for r in rows]

    def all_gaps(self) -> list[Gap]:
        rows = self.substrate.execute_read(
            "MATCH (g:Gap) RETURN g ORDER BY g.created_at ASC",
        )
        return [_gap_from_node(r["g"]) for r in rows]
