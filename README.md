# Drone Graph

An execution substrate for AI swarms organized as a hivemind, not a corporate org chart.

Read in order:

1. [`core-idea/drone-theory.md`](core-idea/drone-theory.md) — the thesis
2. [`architecture-notes/synthesis.md`](architecture-notes/synthesis.md) — architecture overview
3. [`ROADMAP.md`](ROADMAP.md) — build plan
4. [`architecture-notes/phase-0-and-1.md`](architecture-notes/phase-0-and-1.md) — what's built so far
5. [`architecture-notes/model-registry.md`](architecture-notes/model-registry.md) — registry JSON, CLI, and enrichment (when you maintain or extend the catalog)

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

### Windows

- Use **Docker Desktop** instead of Colima to run `docker compose up -d neo4j`.
- **Bash is required** for the Phase 0/1 demos: each drone uses a persistent **Bash** session for `terminal_run` (not PowerShell or cmd). Install **[Git for Windows](https://git-scm.com/download/win)** so `bash.exe` exists; the project prefers Git’s Bash under `Program Files\Git\bin` when present.
- After a successful run, paths like `/tmp/hello.txt` are interpreted by **that** Bash environment. Check them from Git Bash, or e.g. `bash -lc "cat /tmp/hello.txt"` — they are not the same as `%TEMP%` in Command Prompt.

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

## Model registry

The **model registry** is the JSON source of truth for which models exist in the system, how they are priced, what they support, and how **`Gap.model_tier`** maps to a concrete **`provider`** + **`vendor_model_id`** for routing. The packaged file is [`src/drone_graph/model_registry/model_registry.json`](src/drone_graph/model_registry/model_registry.json). Deeper schema, resolution rules, and enrichment behavior live in [`architecture-notes/model-registry.md`](architecture-notes/model-registry.md).

**Prerequisites:** set **`OPENAI_API_KEY`** and/or **`ANTHROPIC_API_KEY`** (at least one vendor key is required to list models from APIs; both keys unlock the richer doc-enrichment path in that note).

```sh
export OPENAI_API_KEY=...          # and/or ANTHROPIC_API_KEY=...

# Clean build: list models from vendor APIs, run doc enrichment, overwrite the registry JSON
# (default path: packaged model_registry.json).
uv run drone-graph model-registry fresh

# Same as fresh, but write elsewhere and print verbose enrichment logs (-v or DRONE_GRAPH_REGISTRY_VERBOSE=1).
uv run drone-graph model-registry fresh -o ./my-registry.json -v

# Doc enrichment only on the existing file — no vendor list refetch; fails if the file is missing.
uv run drone-graph model-registry update

# Merge newly discovered vendor model ids into the current file, then enrich the full set.
uv run drone-graph model-registry sync
```

At runtime, **`ModelRegistry.load_auto()`** reads the packaged JSON unless **`DRONE_GRAPH_MODEL_REGISTRY_PATH`** points at another file (see the architecture note for other env vars such as vendor doc cache).

## Running tests

```sh
pytest                              # all tests; integration tests skip if Neo4j is down
pytest -m 'not integration'         # unit tests only
```

The Phase 1 acceptance tests in `tests/test_phase1_topological.py` hit real Neo4j but inject a stub drone, so they're free and deterministic.

## License

MIT — see [`LICENSE`](LICENSE).
