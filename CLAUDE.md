# Drone Graph

An agent orchestration system organized as a swarm of ephemeral drones sharing
a single collective mind, rather than as a simulated corporate org chart.
Early implementation under `src/drone_graph/` — Neo4j-backed substrate
(gaps + findings + tools), one unified drone runtime that dispatches against
any gap, batched structural edits, deterministic rollup, and a CLI for driving
experiments.

## What this project is

The thesis: frontier models already have all the skills, so division of labor
and hierarchy are artefacts of human constraints, not requirements of AI. A more
egalitarian, swarm-style architecture — interchangeable drones, one shared
knowledge graph, gaps as the atomic unit of work — should outperform
corporate-mimicking multi-agent frameworks.

The operative metaphor is a hivemind: drones are disposable, the collective
mind persists.

## Repo layout

- `core-idea/` — the seed theory. Start here: `drone-theory.md`, then
  `architectural_overview.md` and `decomposition.md`.
- `architecture-notes/` — current thinking on how the system actually works.
  - `notes-0*.md` — raw conceptual notes.
  - `notes-04-architecture-diagram.svg` — a rendered overview diagram.
  - `modules.md` — per-module intent + current CLI surface.
  - `phase-0.md`, `phase-0-and-1.md` — phase acceptance notes and "what ships."
- `src/drone_graph/` — implementation.
  - `gaps/` — `GapStore` (Neo4j), `Gap`/`Finding` pydantic records. Gap
    fields include `tool_loadout`, `tool_suggestions`, `context_preload`,
    `preset_kind`.
  - `tools/` — graph-backed tool registry. `records.py` defines the `Tool`
    node, `store.py` is Neo4j CRUD, `registry.py` is the in-process
    builtins dispatch table, `builtins/` holds the builtin tool
    definitions (queries, structural verbs, alignment, worker tools).
  - `drones/` — `runtime.py` is the unified `run_drone(gap)` entry point;
    one drone class for every gap. `providers.py` is the LLM client layer.
  - `orchestrator/` — `loop.py` (dispatches preset and emergent drones via
    `run_drone`), `bootstrap.py` (substrate init: schema + builtin tools
    mirrored to graph + preset gaps minted), `preload.py` (pre-rendered
    substrate context for preset gaps), scenario loaders, event tape.
  - `terminal/` — persistent bash shell for workers (respawns on death).
  - `prompts/` — `hivemind.md` (the only role prompt; loaded as system
    prompt by every drone). Preset gap intent text lives in
    `orchestrator/bootstrap.py`, not here.
  - `seeds/` — root markdowns and scheduled-event scenarios.
  - `cli.py` — `drone-graph` entry point.
- `tests/`, `experiments/` — integration test harness + targeted verification
  scripts (e.g. `experiments/rollup_check.py` for the auto-rollup invariant).
- `var/runs/` — per-run artefacts from `orchestrator.loop` (events, timeline,
  tree, summary).
- `landing/` — standalone marketing site (`index.html` + `styles.css`). Has
  its own `.git`. Copy uses slightly different vocabulary than the architecture
  notes (e.g. "Findings Graph / Knowledge Graph" on the site vs. "collective
  mind" in the notes) — the architecture notes are the source of truth; the
  landing copy can drift and should be reconciled before launch.
- `defunct-ideas-ignore/` — dead drafts. Ignored via `.claudeignore`. Do not
  read.

## Stage of the project

Early implementation. Architecture is not a spec — it's working theory that
the code is catching up to and sometimes ahead of. When working on this
project, treat the documents as thinking-in-progress and expect drift between
notes and code; flag it rather than silently reconciling.

## Working conventions

- Keep writing and code spare, clean, short, elegant. Dan dislikes bloat.
- When architecture notes and landing-page copy disagree, the architecture
  notes win. Flag the drift rather than silently reconciling.
- When the architecture notes and the code disagree, the code wins for
  mechanics; the notes win for intent. Flag the drift.
- Raw `notes-0*.md` files are conceptual. `architectural_overview.md`,
  `decomposition.md`, and `modules.md` are the closest thing to current
  source-of-truth prose and should be updated alongside substantive code
  changes.
- Absolute dates in any notes (not "next week").

## Key vocabulary

- **Drone** — an ephemeral agent instance. Spawned for a gap, dissolves when
  done. There is **one drone class** with **one system prompt** (`hivemind.md`).
  The gap a drone is dispatched against — and that gap's `tool_loadout` —
  determines what the drone does. There is no per-role drone module.
- **Gap** — the atomic unit of work. Defined by absence, not assignment.
  Carries an `intent` (what must be true), `criteria` (how to check),
  `tool_loadout` (the explicit tool surface for any drone working it),
  `tool_suggestions` (optional pull-in via `cm_request_tool`),
  `context_preload` (preload queries the runtime injects at dispatch), and
  `preset_kind` (non-null on persistent preset gaps). Status:
  `unfilled | filled | retired`.
- **Preset gap** — a persistent gap minted at substrate init with a stable id
  `preset:<kind>`, never closed. Today: `preset:gap_finding`,
  `preset:alignment`. Memory management, testing, etc. would be future
  presets — each is one Gap node with a fixed `tool_loadout`.
- **Emergent gap** — minted by Gap Finding's decompose / create. Has a default
  emergent `tool_loadout` (terminal_run, cm_read_gap, cm_write_finding,
  cm_register_tool, cm_request_tool) unless GF specifies otherwise at
  creation time.
- **Collective mind** — the shared persistent substrate: `Gap` + `Finding` +
  `Tool` nodes in Neo4j, plus on-disk artefacts referenced by
  `Finding.artefact_paths`.
- **Finding** — a short post-it written by a drone. Has `author`
  (`gap_finding | alignment | worker | user | system`), `kind`, summary,
  optional `artefact_paths` to on-disk files, and `affected_gap_ids`. The
  substrate never mutates findings retroactively.
- **Tool** — a `:Tool` node in Neo4j carrying name, description, JSON input
  schema, kind (`builtin` or `installed`), and provenance. Builtins are
  mirrored to the graph at substrate init from a Python registry; installed
  tools are added at runtime by drones via `cm_register_tool` after
  `pip install` / `npm install` / etc, so future drones can discover them
  via `cm_list_tools` and pull them in via `cm_request_tool`.
- **Universal query tools** — `cm_get_gap`, `cm_list_gaps`, `cm_children_of`,
  `cm_parent_of`, `cm_leaves`, `cm_findings`, `cm_finding`, `cm_list_tools`,
  `cm_get_tool`. Available to every drone regardless of gap loadout. No
  drone receives a pre-rendered tree; each pulls what it needs.
- **Context preload** — preset gaps declare a list of preloaders
  (`recent_findings`, `leaves`, `tree_shape`) that the runtime renders at
  dispatch and injects into the drone's initial user message — saves the
  "obvious first query" turn for the common case.
- **Signal protocol** — the mechanical coordination layer that keeps drones
  from conflicting (planned; not yet implemented).
- **The terminal** — the persistent bash shell every emergent worker drone
  acts through. Dies with the drone; respawns on crash so one bad command
  doesn't kill the worker.
- **Batched edits** — Gap Finding and Alignment each emit up to 5 edits /
  findings per invocation. A dense signal (e.g. a user pivot) can trigger
  retire + create + decompose in one drone turn instead of spread across
  several.
- **Auto-rollup** — when all of an emergent parent gap's non-retired children
  are `filled`, the substrate automatically fills the parent and emits a
  `system`-author finding documenting the rollup. Preset gaps are excluded.
  Alignment can contest; Gap Finding can reopen.
- **`rewrite_intent`** — a Gap Finding verb that rewrites an unfilled gap's
  intent + criteria in place (for pivots where the existing subtree is still
  coherent under the new framing). Emits an audit finding capturing old + new.
