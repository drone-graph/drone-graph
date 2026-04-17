# Drone Graph

An execution substrate for AI swarms organized as a hivemind, not a corporate org chart.

Read in order:

1. [`core-idea/drone-theory.md`](core-idea/drone-theory.md) — the thesis
2. [`architecture/synthesis.md`](architecture/synthesis.md) — architecture overview
3. [`ROADMAP.md`](ROADMAP.md) — build plan
4. [`architecture/phase-0.md`](architecture/phase-0.md) — current phase

## Status

**Phase 0** — scaffolding plus a walking skeleton for a single drone closing a single hand-written gap. No gap finder, no concurrency, no skills yet. See [`ROADMAP.md`](ROADMAP.md) for what comes next.

## Running locally

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Docker.

```sh
uv sync --all-extras
docker compose up -d
export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY

uv run drone-graph submit-gap "Create /tmp/hello.txt containing 'hi from the swarm'"
uv run drone-graph run-orchestrator
```

Neo4j Browser: http://localhost:7474 (user `neo4j`, password `drone-graph-dev`).

## License

MIT — see [`LICENSE`](LICENSE).
