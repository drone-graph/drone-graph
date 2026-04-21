# Drone Graph

An execution substrate for AI swarms organized as a hivemind, not a corporate org chart.

Read in order:

1. [`core-idea/drone-theory.md`](core-idea/drone-theory.md) — the thesis
2. [`architecture-notes/synthesis.md`](architecture-notes/synthesis.md) — architecture overview
3. [`ROADMAP.md`](ROADMAP.md) — build plan
4. [`architecture-notes/phase-0-and-1.md`](architecture-notes/phase-0-and-1.md) — what's built so far

## Status

**Phase 0 + Phase 1.** A single drone closes a single hand-written gap end-to-end; the orchestrator honours `BLOCKED_BY` dependencies, executes a gap DAG in topological order, retries once on failure, and propagates terminal failure to descendants. No gap finder, no concurrency, no skills yet. See [`ROADMAP.md`](ROADMAP.md) for what comes next.

## Setup

Requires Python 3.12+, [Colima](https://github.com/abiosoft/colima) (or Docker Desktop), and an Anthropic API key.

```sh
colima start
docker compose up -d neo4j
source .venv/bin/activate
export ANTHROPIC_API_KEY=...
```

Neo4j Browser: http://localhost:7474 (user `neo4j`, password `drone-graph-dev`).

Reset state between demos:

```sh
drone-graph reset-db
```

## Running the demos

### Phase 0 — single gap, single drone

```sh
drone-graph reset-db
drone-graph submit-gap "Create /tmp/hello.txt containing 'hi from the swarm'"
drone-graph run-orchestrator        # Ctrl+C after the idle log appears
cat /tmp/hello.txt
drone-graph gap list                # should show status=closed, attempts=1
```

In Neo4j Browser, confirm the shape:

```cypher
MATCH (g:Gap)-[:CLOSED_WITH]->(f:Finding)-[:PRODUCED_BY]->(d:Drone) RETURN g, f, d
```

### Phase 1 — gap DAG with topological execution

Three gaps where B depends on A and C depends on B. Submitted in dependency order (blockers must exist at submit-time).

```sh
drone-graph reset-db

A_ID=$(drone-graph submit-gap \
  "Create /tmp/swarm-demo.txt containing the single line 'A'" \
  | awk '{print $3}')

B_ID=$(drone-graph submit-gap \
  "Append a second line 'B' to /tmp/swarm-demo.txt" \
  --blocked-by "$A_ID" | awk '{print $3}')

C_ID=$(drone-graph submit-gap \
  "Append a third line 'C' to /tmp/swarm-demo.txt" \
  --blocked-by "$B_ID" | awk '{print $3}')

drone-graph gap list                # all three open, attempts=0
drone-graph run-orchestrator        # Ctrl+C after the idle log appears

cat /tmp/swarm-demo.txt             # A / B / C in order
drone-graph gap list                # all three closed, attempts=1
drone-graph gap show "$C_ID"        # shows blocked_by=$B_ID
```

Verify execution order matches dependency order (not insertion order) in Neo4j Browser:

```cypher
MATCH (d:Drone) RETURN d.gap_id, d.spawned_at ORDER BY d.spawned_at
```

Orchestrator tapes accumulate under `var/tapes/` as JSONL.

## Running tests

```sh
pytest                              # all tests; integration tests skip if Neo4j is down
pytest -m 'not integration'         # unit tests only
```

The Phase 1 acceptance tests in `tests/test_phase1_topological.py` hit real Neo4j but inject a stub drone, so they're free and deterministic.

## License

MIT — see [`LICENSE`](LICENSE).
