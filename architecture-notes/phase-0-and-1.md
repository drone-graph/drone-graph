# Phase 0 & Phase 1 — what's built

Consolidated notes for the substrate that Phase 2 will build on.
Status as of 2026-04-21: Phase 0 and Phase 1 both demo green end-to-end.

## What ships

| Path | Purpose |
|---|---|
| `src/drone_graph/terminal/shell.py` | Per-drone persistent `bash --noprofile --norc`. Marker-based stdout framing, per-terminal stderr file tail-read, `cd`/`export` persist across calls, Python-side wall-clock timeout. Exceptions: `TerminalTimeout`, `TerminalDead`. |
| `src/drone_graph/drones/providers.py` | `AnthropicClient` with normalized `ChatResponse`. Hardcoded pricing table for sonnet-4-6 / opus-4-7 / haiku-4-5 + `cost_usd()`. OpenAI slot raises `NotImplementedError`. |
| `src/drone_graph/drones/runtime.py` | Tool loop exposing `terminal_run`, `cm_read_gap`, `cm_write_finding`. Visible turn budget. `kind="close"` → `CLOSED_WITH` + status `closed`; `kind="abandon"` → `PRODUCED_BY` only, status back to `open`; ending without either → `failed`. All graph writes bundled at drone exit. |
| `src/drone_graph/gaps/records.py` | `Gap`, `Finding`, `GapStatus`, `ModelTier` pydantic models. `Gap` tracks `attempts`, `failure_reason`, `in_progress_at`, `failed_at`. |
| `src/drone_graph/gaps/store.py` | `GapStore` — typed API for the Gap subgraph. `create`, `get`, `open_gaps`, `ready_gaps`, `claim_next_ready` (atomic topological pick + attempts increment), `mark_open`, `mark_failed` (terminal + transitive propagation along `BLOCKED_BY*`), `add_blocker`, `by_tier`, `by_age`, `blockers_of`, `all_gaps`. |
| `src/drone_graph/orchestrator/loop.py` | FIFO `run_once` / `run_forever` via `GapStore.claim_next_ready`. On failed result: resets to `open` if `attempts < 2`, otherwise calls `mark_failed` which cascades failure to descendants. SIGINT/SIGTERM clean shutdown; idle-tick log line. |
| `src/drone_graph/orchestrator/tape.py` | JSONL event tape at `var/tapes/orchestrator-<ts>.jsonl`. Events: `orchestrator.start/claim/retry/terminal_failure/stop`, `drone.spawn/turn/die`, `tool.terminal_run`, `tool.write_finding`. |
| `src/drone_graph/cli.py` | `submit-gap` (`--criteria`, `--blocked-by` repeatable, `--tier`), `run-orchestrator`, `reset-db`, `gap list` (`--status`, `--tier`), `gap show <id>`. |
| `tests/test_terminal.py` | 6 real-bash tests (no mocks). |
| `tests/test_phase1_topological.py` | 6 integration tests against real Neo4j: topological A→B→C regardless of `created_at` order, retry-once-then-fail, retry-recovers, failure propagation down the DAG, `ready_gaps` excludes blocked, forward-ref `blocked_by` raises. |

## Confirmed design decisions

Not up for re-litigation without a specific reason:

1. **Terminal is persistent, not one-shot.** `cd` / `export` state must carry across calls.
2. **Drone exits on a closing finding.** A `stop_reason=end_turn` with no `close`/`abandon` finding marks the gap `failed`.
3. **Pricing lives in `providers.py`** as a hardcoded table, not in config.
4. **`payload_ref` is an optional filesystem path.** Null when a bare summary suffices.
5. **Hard turn cap + visible budget.** Default 20 turns. Every tool-result user turn appends `[turns remaining: N]`.
6. **Default model is `claude-sonnet-4-6`, provider `anthropic`.**
7. **Retry policy: 1 retry on `failed`, then stay failed.** `attempts` is incremented at claim time (every dispatch == one attempt) so the policy is observable.
8. **Blocker failure propagates.** When a gap fails terminally, every open/in-progress descendant along `BLOCKED_BY*` is flipped to `failed` with `failure_reason = "blocker_failed:<root_id>"`.
9. **`blocked_by` ids must exist at `create()` time.** Forward references raise. Use `GapStore.add_blocker` to wire dependencies between pre-existing gaps.
10. **Integration tests hit real Neo4j.** No mocks for the substrate. Acceptance tests may inject a stub drone to skip the LLM call.

## Environment on disk

- **Colima** provides the docker daemon (installed via `brew install colima docker docker-compose`; Apple VZ hypervisor, 2 CPU / 4 GB / 20 GB). Start with `colima start`.
- **Neo4j** 5-community runs in docker (`docker compose up -d neo4j`). Bolt at `bolt://localhost:7687`, Browser at `http://localhost:7474`, auth `neo4j` / `drone-graph-dev`. Data volume: `./var/neo4j/` (gitignored).
- **`.venv/`** at repo root (stdlib venv, Python 3.14). Project is installed editable with dev extras (`pytest`, `ruff`, `mypy`).
- **`~/.docker/config.json`** was edited to add `/opt/homebrew/lib/docker/cli-plugins` so `docker compose` resolves without Docker Desktop.
- **Tapes** accumulate under `var/tapes/` (gitignored).

## Security note

Never paste an API key in chat. Set it in your own shell:

```sh
export ANTHROPIC_API_KEY=...
```

## How to run the demos

See the "Running the demos" section in `README.md`.
