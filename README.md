# Drone Graph

An execution substrate for AI swarms organized as a hivemind, not a corporate org chart.

Read in order:

1. [`core-idea/drone-theory.md`](core-idea/drone-theory.md) — the thesis
2. [`core-idea/architectural_overview.md`](core-idea/architectural_overview.md) — consolidated architecture
3. [`core-idea/decomposition.md`](core-idea/decomposition.md) — gap / finding mechanics + Gap Finding + Alignment behavior
4. [`architecture-notes/modules.md`](architecture-notes/modules.md) — per-module intent and current CLI surface
5. [`ROADMAP.md`](ROADMAP.md) — build plan
6. [`architecture-notes/phase-0-and-1.md`](architecture-notes/phase-0-and-1.md) — what's built so far
7. [`architecture-notes/model-registry.md`](architecture-notes/model-registry.md) — registry JSON, CLI, and enrichment (when you maintain or extend the catalog)

## Status

**Unified drone runtime running end-to-end.** One drone class, one system prompt (`hivemind.md`); the gap it's dispatched against — and that gap's `tool_loadout` — determines what it does. Two preset gaps minted at substrate init: `preset:gap_finding` (structural author; batches up to 5 edits per invocation including `decompose`, `create`, `retire`, `reopen`, `rewrite_intent`, `noop`) and `preset:alignment` (observational; batches up to 5 findings). Emergent gaps minted by Gap Finding get a default loadout (a persistent bash shell + `cm_read_gap` + `cm_write_finding` with on-disk `artefact_paths` + `cm_register_tool` + `cm_request_tool`). Every drone gets universal `cm_*` query tools — no pre-rendered tree, drones query what they need. Tools live as `:Tool` nodes in the graph; drones can install new tools at runtime and register them for future drones. The substrate auto-fills an emergent parent gap when all its non-retired children are filled, emitting a `system`-authored finding. The combined orchestrator loop runs the whole thing against any of the packaged root seeds or synthetic gaps. No concurrency yet. See [`ROADMAP.md`](ROADMAP.md) for what comes next.

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

### Single drone, single gap

```sh
drone-graph reset-db                # also re-mints preset gaps + tool registry
GAP=$(drone-graph gap create \
  --intent "Create /tmp/hello.txt containing exactly 'hi from the swarm' plus a trailing newline." \
  --criteria "/tmp/hello.txt exists with the exact required content.")
drone-graph drone run "$GAP"        # spawns one drone, runs to fill or fail
cat /tmp/hello.txt
drone-graph gap show "$GAP"         # status: filled
```

### Combined orchestrator loop (Gap Finding + Alignment + workers)

Run the unified loop against a packaged scenario. Real workers spawn on emergent
leaves every N Gap Finding cycles when `--worker-every` is set.

```sh
python -m drone_graph.orchestrator.loop \
  --scenario coffee-pivot-b2b \
  --model claude-haiku-4-5-20251001 \
  --worker-every 2 \
  --worker-max-turns 8 \
  --out var/runs/coffee-demo
```

Per-run artefacts land under `var/runs/<scenario>-<ts>/`: `events.jsonl`,
`tape.jsonl`, `timeline.md`, `tree.md`, `summary.md`. Inspect the substrate
mid-run from another shell:

```sh
drone-graph gap tree
drone-graph gap list --status unfilled
drone-graph finding list --author worker -n 20
drone-graph finding show <id-prefix>
```

In Neo4j Browser ([http://localhost:7474](http://localhost:7474)) you can see the full graph: gaps,
findings, tools, and the relationships between them.

```cypher
MATCH (t:Tool) RETURN t.name, t.kind ORDER BY t.kind, t.name
```

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

`tests/test_phase1_topological.py` was written against an earlier API and is
currently broken; it'll be replaced as the unified runtime stabilizes. For
substrate invariants (auto-rollup, `rewrite_intent` guardrails, etc.) see
`experiments/rollup_check.py`, which exercises the live store against real
Neo4j and is the authoritative regression check today.

## License

MIT — see [`LICENSE`](LICENSE).
