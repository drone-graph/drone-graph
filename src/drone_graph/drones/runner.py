"""Subprocess entry point for a single drone run.

The Phase 3 scheduler spawns one of these per gap:

    python -m drone_graph.drones.runner \\
        --gap-id <id> \\
        --signal-db var/signals.db \\
        --tape-path var/tapes/<run_id>/<drone_id>.jsonl \\
        --provider anthropic --model claude-sonnet-4-6 \\
        --tick 7

Exit codes (read by the scheduler to classify outcome):

    0  fill or preset_done
    1  fail
    2  cancelled or claim_lost
    3  max_turns
    4  error (unhandled exception, client failure, missing gap)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from drone_graph.drones.providers import (
    Provider,
    make_client,
    resolve_orchestrator_provider_model,
)
from drone_graph.drones.runtime import (
    DEFAULT_CLAIM_TTL_S,
    DEFAULT_COMMAND_TIMEOUT_S,
    DEFAULT_HEARTBEAT_PERIOD_S,
    DEFAULT_MAX_TURNS,
    DroneResult,
    run_drone,
)
from drone_graph.gaps import GapStore
from drone_graph.orchestrator.tape import EventTape
from drone_graph.signals import SQLiteSignalStore, default_db_path
from drone_graph.substrate import Substrate
from drone_graph.tools import ToolStore

_OUTCOME_EXIT_CODES: dict[str, int] = {
    "fill": 0,
    "preset_done": 0,
    "fail": 1,
    "cancelled": 2,
    "claim_lost": 2,
    "max_turns": 3,
    "error": 4,
    "budget_exceeded": 5,
}


def _substrate_from_env() -> Substrate:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "drone-graph-dev")
    return Substrate(uri, user, password)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m drone_graph.drones.runner",
        description="Run one drone against one gap as a subprocess.",
    )
    p.add_argument("--gap-id", required=True, help="Gap id to work on.")
    p.add_argument(
        "--provider", default=None, help="anthropic|openai (default: env)."
    )
    p.add_argument("--model", default=None, help="Vendor model id.")
    p.add_argument(
        "--max-turns", type=int, default=DEFAULT_MAX_TURNS,
        help="Hard cap on model turns.",
    )
    p.add_argument(
        "--command-timeout", type=float, default=DEFAULT_COMMAND_TIMEOUT_S,
        help="Per terminal command timeout, seconds.",
    )
    p.add_argument(
        "--tick", type=int, default=0,
        help="Tick value stamped on any finding the drone writes.",
    )
    p.add_argument(
        "--tape-path", type=Path, default=None,
        help="JSONL tape file for drone lifecycle events.",
    )
    p.add_argument(
        "--signal-db", type=Path, default=None,
        help="Sidecar SQLite path. Default: var/signals.db.",
    )
    p.add_argument(
        "--no-signals", action="store_true",
        help="Disable claim/heartbeat (for testing the legacy single-drone path).",
    )
    p.add_argument(
        "--run-id", default=None,
        help="Run id for the swarm cost meter. If unset, cost is not enforced.",
    )
    p.add_argument(
        "--claim-ttl", type=float, default=DEFAULT_CLAIM_TTL_S,
        help="Claim TTL in seconds.",
    )
    p.add_argument(
        "--heartbeat-period", type=float, default=DEFAULT_HEARTBEAT_PERIOD_S,
        help="Heartbeat renewal period in seconds.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    # override=True so an empty ANTHROPIC_API_KEY in the parent shell
    # (common in nested venvs) doesn't shadow a real value in .env.
    load_dotenv(override=True)
    args = _build_parser().parse_args(argv)

    substrate = _substrate_from_env()
    store = GapStore(substrate)
    tool_store = ToolStore(substrate)

    target = store.get(args.gap_id)
    if target is None:
        print(f"runner: no such gap: {args.gap_id}", file=sys.stderr)
        return _OUTCOME_EXIT_CODES["error"]

    prov = Provider(args.provider) if args.provider else None
    resolved_provider, resolved_model = resolve_orchestrator_provider_model(
        prov, args.model
    )
    client = make_client(resolved_provider, resolved_model)

    tape = EventTape(args.tape_path) if args.tape_path is not None else None

    signals: SQLiteSignalStore | None = None
    if not args.no_signals:
        db = args.signal_db if args.signal_db is not None else default_db_path()
        signals = SQLiteSignalStore(db)

    try:
        result: DroneResult = run_drone(
            target,
            store=store,
            tool_store=tool_store,
            client=client,
            tick=args.tick,
            max_turns=args.max_turns,
            command_timeout_s=args.command_timeout,
            tape=tape,
            signals=signals,
            claim_ttl_s=args.claim_ttl,
            heartbeat_period_s=args.heartbeat_period,
            run_id=args.run_id,
        )
    finally:
        if signals is not None:
            signals.close()
        substrate.close()

    print(
        f"runner: outcome={result.outcome} drone={result.drone_id} "
        f"gap={result.gap_id} turns={result.turns_used} "
        f"findings={result.findings_written} cost=${result.cost_usd:.4f} "
        f"finding_id={result.finding_id or '-'}"
        + (f" error={result.error}" if result.error else ""),
        file=sys.stderr,
    )

    return _OUTCOME_EXIT_CODES.get(result.outcome, _OUTCOME_EXIT_CODES["error"])


if __name__ == "__main__":
    sys.exit(main())
