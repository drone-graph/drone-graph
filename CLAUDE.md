# Drone Graph

A design-stage project for an agent orchestration system organized as a swarm of
ephemeral drones sharing a single collective mind, rather than as a simulated
corporate org chart. No implementation yet — just the theory, architecture notes,
and a landing page.

## What this project is

The thesis: frontier models already have all the skills, so division of labor
and hierarchy are artefacts of human constraints, not requirements of AI. A more
egalitarian, swarm-style architecture — interchangeable drones, one shared
knowledge graph, gaps as the atomic unit of work — should outperform
corporate-mimicking multi-agent frameworks.

The operative metaphor is a hivemind: drones are disposable, the collective
mind persists.

## Repo layout

- `core-idea/` — the seed theory. Start here: `drone-theory.md`.
- `architecture/` — current thinking on how the system would actually work.
  - `synthesis.md` — the consolidated architecture overview. Read this second.
  - `notes-0*.md` — the raw notes `synthesis.md` was built from.
  - `notes-04-architecture-diagram.svg` — a rendered overview diagram.
- `landing/` — standalone marketing site (`index.html` + `styles.css`). Has its
  own `.git`. The copy is more polished and uses slightly different vocabulary
  than the architecture notes (e.g. "Findings Graph / Knowledge Graph" on the
  site vs. "collective mind" in the notes) — the architecture notes are the
  source of truth; the landing copy can drift and should be reconciled before
  launch.
- `defunct-ideas-ignore/` — dead drafts. Ignored via `.claudeignore`. Do not read.

## Stage of the project

Very early. The architecture is a sketch, not a spec. There is no code, no
schema, no runtime. Everything is open to revision. When working on this
project, treat the documents as thinking-in-progress — not as a contract to
conform to.

## Working conventions

- Keep writing and code spare, clean, short, elegant. Dan dislikes bloat.
- When architecture notes and landing-page copy disagree, the architecture
  notes win. Flag the drift rather than silently reconciling.
- `synthesis.md` supersedes the raw `notes-0*.md` files where they overlap, but
  the raw notes sometimes contain details (e.g. the signal protocol specifics)
  that haven't made it into synthesis yet — worth checking both.
- Absolute dates in any notes (not "next week").

## Key vocabulary

- **Drone** — an ephemeral agent instance. Spawned for a gap, dissolves when done.
- **Gap** — the atomic unit of work. Defined by absence, not assignment.
- **Preset gap** — a persistent gap that is never closed, only continually
  worked (gap finding, memory management, research, testing).
- **Collective mind** — the shared persistent substrate (skills, tool registry,
  findings, user uploads, gaps).
- **Signal protocol** — the mechanical coordination layer that keeps drones from
  conflicting (e.g. don't open a file another drone has open).
- **The terminal** — the single real tool; all drones act through it.
