# Phase 0 & Phase 1 — what's built

Consolidated notes for the substrate that Phase 2+ now builds on. Status as
of 2026-04-24: Phase 0 and (a unified, decomposition-driven) Phase 1+2 demo
green end-to-end against `coffee-pivot-b2b` with real workers on Haiku.

The original `BLOCKED_BY` DAG model from Phase 1 has been **superseded** by
the gap-tree model: Gap Finding mints children via `decompose`, the
substrate auto-rolls a parent up when all its non-retired children are
filled. Sequencing is emergent from the tree and worker attempt order, not
explicit dependency edges.

## What ships

| Path | Purpose |
|---|---|
| `src/drone_graph/terminal/shell.py` | Per-drone persistent `bash --noprofile --norc`. Marker-based stdout framing, per-terminal stderr file tail-read, `cd`/`export` persist across calls, Python wall-clock timeout. Exceptions: `TerminalTimeout`, `TerminalDead`. The runtime wraps it in a `_TerminalBox` that respawns the shell on death so a single bad command doesn't kill the drone. |
| `src/drone_graph/drones/providers.py` | `AnthropicClient` and `OpenAIClient`, normalized `ChatResponse`. `make_client(provider, model)` wraps `chat()` in bounded exponential backoff for transient network / rate-limit errors. Hardcoded pricing table + `cost_usd()`. |
| `src/drone_graph/drones/runtime.py` | **The unified drone runtime.** One `run_drone(gap, *, store, tool_store, client, tick, max_turns, ...)` for every drone, preset or emergent. Loads `hivemind.md` as system prompt, reads the gap's `intent` + `criteria` + `tool_loadout` + `tool_suggestions` + `context_preload`, computes the active tool set (gap loadout ∪ universal cm_* queries), runs a multi-turn message loop, terminates on a `fill` / `fail` (emergent), preset_done, max_turns, or client error. Empty commands rejected at the dispatcher level so they never reach bash. |
| `src/drone_graph/tools/` | **Graph-backed tool registry.** `:Tool` nodes alongside gaps and findings, with `(:Tool)-[:USED_BY]->(:Gap)` and `(:Tool)-[:DEPENDS_ON]->(:Tool)` edges. Builtins are mirrored at substrate init from a Python `@register_tool` decorator registry; installed tools are added at runtime by drones via `cm_register_tool` (kind=installed, recording usage + install_commands). 21 builtins ship: 9 universal cm_* queries, 6 Gap Finding structural verbs, 1 alignment finding writer, 5 worker tools (`terminal_run`, `cm_read_gap`, `cm_write_finding`, `cm_register_tool`, `cm_request_tool`). |
| `src/drone_graph/gaps/records.py` | `Gap`, `Finding`, enums. Gap fields include `tool_loadout`, `tool_suggestions`, `context_preload`, `preset_kind`. Finding fields include `artefact_paths` for on-disk references. |
| `src/drone_graph/gaps/store.py` | `GapStore` — full read + write API over the gap subgraph. Reads: `get` (prefix-tolerant), `all_gaps`, `roots`, `leaves` (excludes preset gaps), `children_of`, `parent_of`, `recent_findings`, `all_findings`, `get_preset`. Writes: `apply_decompose` (additive — appends children to already-decomposed parents, dropping duplicates by intent), `apply_create`, `apply_retire` (cascade-retires descendants), `apply_reopen`, `apply_rewrite_intent` (with old-text-preserved audit finding), `apply_noop`, `apply_fill` (with `_propagate_fill_upwards` auto-rollup; preset gaps excluded), `apply_fail`, `append_finding`, `create_root`, `upsert_preset` (idempotent), `reset_all`. |
| `src/drone_graph/orchestrator/bootstrap.py` | `init_collective_mind(substrate)` — schema + builtin tools mirrored to graph + preset gaps minted. Idempotent. Holds the preset gaps' intent text + tool loadouts (`PRESET_GAP_FINDING`, `PRESET_ALIGNMENT`). |
| `src/drone_graph/orchestrator/preload.py` | Context preloaders the runtime injects into preset drones' initial user message: `recent_findings`, `leaves`, `tree_shape`. |
| `src/drone_graph/orchestrator/loop.py` | **The unified combined loop.** Each cycle: inject scheduled scenario events → optionally dispatch the Alignment preset drone (every N cycles) → dispatch the Gap Finding preset drone → optionally dispatch a worker drone against the oldest unattempted active leaf. All four phases go through `run_drone(gap)`. Stops on max cycles, 3 consecutive noops with no work pending, or 3 consecutive client errors. |
| `src/drone_graph/orchestrator/tape.py` | JSONL event tape at `var/tapes/orchestrator-<ts>.jsonl`. Events: `scenario.inject`, `drone.spawn/turn/die`, `tool.terminal_run/respawn`, `tool.write_finding`, `tool.register`, `gap_finding.edit`, `alignment.finding`. |
| `src/drone_graph/prompts/hivemind.md` | The single shared system prompt for every drone. Per-role guidance lives in preset gap intent text in `bootstrap.py`. |
| `src/drone_graph/cli.py` | `reset-db` (re-mints preset gaps + tool registry), `gap list/show/tree/create`, `finding list/show`, `drone run [GAP_ID]`, `model-registry fresh/update/sync`. |
| `experiments/rollup_check.py` | Targeted regression suite against real Neo4j: 5 cases for auto-rollup, retired siblings, all-retired-no-rollup, nested chain, and `rewrite_intent` guardrails. The current authoritative invariants check. |

## Confirmed design decisions

Not up for re-litigation without a specific reason:

1. **One drone class, one system prompt.** The gap a drone is dispatched
   against — and that gap's `tool_loadout` — determines what the drone
   does. There is no per-role drone module.
2. **Preset gaps are real `:Gap` nodes** with stable ids `preset:<kind>`,
   minted at substrate init. They never close, never retire. Today's
   presets: `gap_finding`, `alignment`.
3. **Tool registry is graph-backed.** `:Tool` nodes; builtins mirrored at
   init, installed tools added at runtime by drones.
4. **No drone gets a pre-rendered tree.** Preset gaps declare a
   `context_preload` (recent findings, leaves, tree shape) the runtime
   injects at dispatch; drones pull more via `cm_*` queries on demand.
5. **Batched edits.** Gap Finding and Alignment each emit up to 5 edits /
   findings per invocation, dispatched in priority order through the
   model's parallel-tool-use channel.
6. **Auto-rollup is deterministic substrate behavior.** `apply_fill`
   walks up; preset gaps are excluded; each rollup emits a separate
   `system`-author finding so Alignment can contest.
7. **Terminal is persistent, not one-shot.** `cd` / `export` state must
   carry across calls. The shell respawns on death.
8. **Drone exits on a terminal finding (emergent) or preset_done /
   max_turns (preset).** A turn that ends with no tool calls is an error
   for emergent drones.
9. **Pricing lives in `providers.py`** as a hardcoded table, not in
   config.
10. **`Finding.artefact_paths` is the substantive-output channel.**
    Findings stay terse; long output goes to disk and is referenced by
    path.
11. **Hard turn cap + visible budget.** Default 20 for emergent workers, 6
    for preset drones. Every tool-result user turn appends `[turns
    remaining: N]`.
12. **Default model is `claude-sonnet-4-6`, provider `anthropic`.** Most
    experiments to date run on `claude-haiku-4-5-20251001` for cost.
13. **Integration tests hit real Neo4j.** No mocks for the substrate.
    `tests/test_phase1_topological.py` is currently broken against the
    new API and pending replacement; `experiments/rollup_check.py` is
    the live regression check.

## Superseded (kept for reference)

- **`BLOCKED_BY` DAG model.** Replaced by gap decomposition + auto-rollup.
  `Gap.attempts`, `failure_reason`, `in_progress_at`, `failed_at` and the
  associated retry-once-then-fail orchestrator logic are gone. Worker
  fail/fill is now recorded as a `Finding` (kind `fail`/`fill`); Gap
  Finding decides what to do next.
- **`tests/test_phase1_topological.py`** — written against the old API;
  imports symbols that no longer exist. Will be replaced once the
  unified runtime stabilizes.

## Environment on disk

- **Colima** provides the docker daemon (installed via `brew install
  colima docker docker-compose`; Apple VZ hypervisor, 2 CPU / 4 GB /
  20 GB). Start with `colima start`.
- **Neo4j** 5-community runs in docker (`docker compose up -d neo4j`).
  Bolt at `bolt://localhost:7687`, Browser at `http://localhost:7474`,
  auth `neo4j` / `drone-graph-dev`. Data volume: `./var/neo4j/`
  (gitignored).
- **`.venv/`** at repo root (stdlib venv, Python 3.14). Project is
  installed editable with dev extras (`pytest`, `ruff`, `mypy`).
- **`~/.docker/config.json`** was edited to add
  `/opt/homebrew/lib/docker/cli-plugins` so `docker compose` resolves
  without Docker Desktop.
- **Tapes** accumulate under `var/tapes/` (gitignored). Per-run artefacts
  under `var/runs/<scenario>-<ts>/`.

## Security note

Never paste an API key in chat. Set it in your own shell or `.env` file
(loaded by `cli.main` via `python-dotenv`):

```sh
export ANTHROPIC_API_KEY=...
```

## How to run the demos

See the "Running the demos" section in `README.md`.
