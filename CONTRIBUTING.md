# Contributing to Drone Graph

Thanks for your interest. Drone Graph is early — architecture is moving
theory, not a spec — so a few notes about how to land changes without
wasted work.

## Before you write code

**Open an issue first** for anything non-trivial. A non-trivial change is
anything beyond a typo, a one-line bug fix, or a doc clarification. Spec
the intent and what "filled" looks like; we'll confirm scope before you
spend time on a PR. This protects you from building against a design
that's about to change.

For bigger ideas (new primitives, architectural shifts, alternate
substrates), describe the idea in an issue and let it breathe for a bit
before opening a PR.

## Workflow

1. Fork the repo.
2. Create a branch off `main`: `git checkout -b your-name/short-topic`.
3. Make changes. Keep diffs focused — one PR per concern.
4. Run the checks locally (see below).
5. Open a PR against `drone-graph/drone-graph:main`.
6. CI will run ruff + mypy + pytest. Address any failures.

## Local setup

You need Python 3.12+, [Colima](https://github.com/abiosoft/colima) (or
Docker Desktop) for Neo4j.

```sh
# Install with dev extras
pip install -e ".[dev]"
# Or with uv
uv sync --extra dev

# Neo4j (only needed for integration tests)
colima start
docker compose up -d neo4j
```

Tests that need Neo4j auto-skip if it's not running. Tests that need
`ANTHROPIC_API_KEY` or `OPENAI_API_KEY` likewise skip without them. You
can iterate on most of the codebase with neither.

## Checks

Before opening a PR, run:

```sh
ruff check .
ruff format --check .
mypy
pytest
```

CI runs the same. If you're touching a Neo4j-backed path, run the
integration tests against a local Neo4j too:

```sh
DRONE_GRAPH_TESTS_ALLOW_WIPE=1 pytest -m integration
```

`DRONE_GRAPH_TESTS_ALLOW_WIPE=1` is required — without it the conftest
refuses to run against a DB that looks like a real session, to protect
your mission-control state.

## Conventions

These come from [`CLAUDE.md`](CLAUDE.md) and apply equally to humans:

- **Spare, clean, short, elegant.** No bloat. Three similar lines beats a
  premature abstraction. Don't add error handling, fallbacks, or
  backwards-compatibility shims for scenarios that can't happen.
- **No speculative features.** Don't design for hypothetical future
  requirements. A bug fix doesn't need surrounding cleanup; a one-shot
  operation doesn't need a helper.
- **Comments only when the *why* is non-obvious.** Don't explain what
  the code does — names should. Don't reference the current task or
  fix; that belongs in the PR description.
- **Absolute dates** in any notes ("2026-05-19", not "next week").
- **Flag drift, don't silently reconcile.** When architecture notes and
  landing-page copy disagree, notes win. When notes and code disagree,
  code wins for mechanics, notes win for intent. In both cases, flag the
  drift in your PR rather than quietly fixing one side.

## Where things live

- [`core-idea/`](core-idea) — seed theory. Start here if you're new.
- [`architecture-notes/`](architecture-notes) — current architectural
  thinking. `modules.md` is the per-module map.
- [`src/drone_graph/`](src/drone_graph) — implementation.
- [`tests/`](tests) — pytest suite. Tests that need Neo4j use the
  `substrate` fixture and auto-skip if unavailable.
- [`ROADMAP.md`](ROADMAP.md) — phase-by-phase plan.

## What's out of scope

- New "role-shaped" agent classes (researcher / writer / planner). The
  thesis is one drone class, one prompt. If you want role behavior,
  encode it in the gap's `tool_loadout`, not in the runtime.
- Per-drone memory beyond the substrate. Drones are ephemeral by design;
  persistent state goes through `Gap` + `Finding` + `Tool` nodes.
- Vendor lock-in. The provider layer abstracts Anthropic and OpenAI;
  changes that hardcode one are non-starters.

## License

By contributing, you agree your contributions are licensed under the
project's [MIT license](LICENSE).
