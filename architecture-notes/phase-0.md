# Phase 0 — First drone, first gap

The minimum artifact that proves the core loop runs end to end.

## Goal

Hand-write a gap into Neo4j. The orchestrator notices. One drone spawns, reads the gap, calls a model, uses its terminal to do real work, writes a finding, dissolves. Verify the result in Neo4j Browser and on the filesystem.

## Success criteria

```
drone-graph submit-gap "Create /tmp/hello.txt containing 'hi from the swarm'"
drone-graph run-orchestrator
```

…yields all of:

1. `/tmp/hello.txt` exists with the expected content.
2. Neo4j contains a path `(:Gap)-[:CLOSED_WITH]->(:Finding)-[:PRODUCED_BY]->(:Drone)`.
3. The gap's `status` is `closed`.
4. The drone record has non-zero `tokens_in`, `tokens_out`, `cost_usd`.

## Explicitly out of scope

Gap finder · decomposition · concurrency · claim-and-lease · signal protocol · skills · tool registry · alignment preset · memory management · vector search · Secret Store · ontology bootstrap · budget enforcement · downloading anything from the internet.

Budget fields (`cost_max_usd`, `token_max`, `model_tier`) exist on gap records but are not enforced.

## Components

### `substrate/`

Everything that touches Neo4j.

- `client.py` — thin wrapper: context-managed session, `execute_read`, `execute_write`, idempotent schema init.
- `schema.py` — `CREATE CONSTRAINT` / `CREATE INDEX` statements for Phase 0 labels.

Later phases add a fog-of-war loader (`FindingHandle` with `.expand()` / `.neighbors()`), claim-and-lease helpers, and CAS writes.

### `gaps/`

- `records.py` — Pydantic `Gap`, `Finding`; enums for `GapStatus`, `ModelTier`.

No CRUD module — callers write Cypher directly against the substrate in Phase 0. Extracted into a gap-store module in Phase 1 once queries get non-trivial.

### `drones/`

- `runtime.py` — `run_drone(gap_id, substrate) -> DroneResult`. Blocking. Builds prompt, runs tool-use loop, writes finding on exit.
- `providers.py` — abstraction over Anthropic and OpenAI. Both expose `chat(system, messages, tools, model) -> Response`.

Tools exposed in Phase 0: `terminal.run`, `cm.write_finding`, `cm.read_gap`. Nothing else.

### `terminal/`

- `shell.py` — per-drone `subprocess.Popen('bash')`. `run(cmd, timeout) -> (stdout, stderr, exit_code)`. Dies with the drone.

No registration in the collective mind yet (no concurrency means nothing to coordinate).

### `orchestrator/`

- `loop.py` — polls Neo4j for `(:Gap {status: 'open'})`, picks oldest, spawns a drone, waits, updates status on exit.

Single-threaded. One drone at a time. Claim-and-lease is Phase 3.

### `prompts/`

- `hivemind.md` — v0 draft of the shared system prompt. Iterated against real runs, not in the abstract.

### `cli.py`

- `drone-graph submit-gap <description>` — insert a Gap node.
- `drone-graph run-orchestrator` — run the loop.
- `drone-graph reset-db` — dev convenience.

## Graph schema (Phase 0)

```cypher
// Constraints
CREATE CONSTRAINT gap_id_unique FOR (g:Gap) REQUIRE g.id IS UNIQUE;
CREATE CONSTRAINT finding_id_unique FOR (f:Finding) REQUIRE f.id IS UNIQUE;
CREATE CONSTRAINT drone_id_unique FOR (d:Drone) REQUIRE d.id IS UNIQUE;

// Indexes
CREATE INDEX gap_status FOR (g:Gap) ON (g.status);
CREATE INDEX gap_created_at FOR (g:Gap) ON (g.created_at);

// Node shapes (property names, types informal here)
(:Gap {
  id, description, status,
  nl_criteria, structured_check,
  cost_max_usd, token_max, model_tier,
  created_at, closed_at
})
(:Finding { id, kind, summary, payload_ref, created_at })
(:Drone {
  id, spawned_at, died_at,
  model, provider, gap_id,
  tokens_in, tokens_out, cost_usd
})

// Relations
(:Gap)-[:CLOSED_WITH]->(:Finding)
(:Drone)-[:WORKED]->(:Gap)
(:Finding)-[:PRODUCED_BY]->(:Drone)
```

`SkillInvocation` and `BLOCKED_BY` are introduced in Phase 1/4, not Phase 0.

## Dependencies

Python 3.12+ · [`uv`](https://docs.astral.sh/uv/) · Docker (Neo4j Community) · `neo4j` driver · `anthropic` + `openai` SDKs · `pydantic` · `typer` · `pytest` / `ruff` / `mypy`.

## Day-by-day estimate (solo, full focus)

| Day | Work |
|---|---|
| 1 | Scaffold, pyproject, docker-compose, Neo4j up, schema init |
| 2 | Substrate client + gap records + submit-gap / reset-db CLI |
| 3 | Drone runtime skeleton + provider abstraction (one provider working) |
| 4 | Terminal wrapper + integration with drone runtime |
| 5 | Orchestrator loop + end-to-end `/tmp/hello.txt` run |
| 6–7 | Second provider, polish, observability tape v0, failure-mode cleanup |

## Handoff to Phase 1

Phase 0 leaves in place a working core loop for one drone + one gap. Phase 1 adds gap-store query API and dependency resolution without touching the drone runtime or the terminal.

The single largest artifact generated in Phase 0 that will keep mattering: `prompts/hivemind.md`. Every later phase iterates on it. Treat it as a living document.
