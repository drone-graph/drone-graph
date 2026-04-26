# Decomposition

How the gap tree grows, shrinks, and stays honest.

## The tree

The collective mind holds a tree of gaps. The user writes the root. Every node below the root is produced by a single structural edit. The tree is the swarm's only representation of work in flight.

## The one writer

Exactly one drone at a time may edit the tree: the drone working the
**Gap Finding** preset gap. Gap Finding is always open and is worked one
invocation at a time. Per invocation, the drone may emit **up to 5 batched
edits** in priority order — a dense signal (e.g. a user pivot) can be
absorbed in a single drone turn instead of stretched across many. Because
the drone is still single-threaded, deduplication is trivial — there is
never a race against another writer.

Gap Finding has six verbs:

- **Decompose** — attach children to a gap whose intent cannot be filled in
  one pass. Decomposition is **additive**: if the parent already has
  active children, new ones are appended (duplicate intents are silently
  dropped). Use to add missing work to an already-decomposed parent
  instead of orphaning it as a top-level sibling.
- **Create** — add a new top-level gap in response to new signal (user
  message, findings that imply adjacent work, Alignment findings).
- **Retire** — close off an open subtree whose premise has been
  invalidated. Children retire too. Retired gaps stay in the graph with
  findings intact; only new claims are blocked. Retiring the root is
  legitimate when a pivot supersedes the entire framing — the old work
  remains as historical record.
- **Reopen** — mark a filled gap unfilled again when findings in the graph
  show its intent wasn't actually met. Children are preserved.
- **Rewrite intent** — rewrite an unfilled gap's `intent` and `criteria` in
  place when a prior signal explicitly reframes it AND the existing
  descendants stay coherent under the new intent. Emits an audit finding
  with both old and new text. Most useful for partial pivots; for total
  pivots, retire + create is cleaner.
- **Noop** — when the leaf buffer is at target and no finding warrants an
  edit. Three consecutive noops with no work pending is one of the loop's
  stop conditions.

## How Gap Finding runs

Gap Finding runs continuously, staying ahead of the workers. Its job is to keep **N unfilled leaves ready for workers to claim**, where N tracks the user's max concurrency. Depth is a side effect — however deep Gap Finding needed to go to produce N viable leaves. The tree's shape is emergent, not designed.

At the start of a goal, Gap Finding decomposes the root several layers before any worker runs. This isn't a planning phase; workers simply have nothing to claim until leaves exist.

When the leaf buffer is full, Gap Finding isn't idle — it reads findings, creates research gaps that will add new findings to find future gaps, and acts on Alignment's findings. There is always something to do. The process is continuous by construction.

## Intent

Decomposition splits intent. Each child gets its own intent, written in plain natural language as acceptance criteria. A child's intent is a *fragment* of the parent's, not a copy. The invariant is that the children's intents, taken together, fulfill the parent's intent — and each child's intent stays aligned with the directionality of the parent's.

The criteria are clear enough that a future drone can judge whether the gap has been filled. Nothing formal.

## Findings are the only output

Every drone — workers, Alignment, Gap Finding — produces findings. The findings stream is the substrate; the gap tree is the structure built on top of it.

A worker drone has two outcomes: **fill** or **fail**, both recorded as findings.

- **Fill** — the worker succeeds and produces a finding that satisfies the gap's intent.
- **Fail** — the worker cannot fill the gap and produces a finding explaining why (e.g. "too large," "blocked on missing context").

Workers only ever claim leaves. A gap with children is not workable; its
children are. A non-leaf is **auto-filled** by the substrate when all its
non-retired children are filled — this rollup happens deterministically
inside `apply_fill` and emits a separate finding authored by `system` so
Alignment can contest and Gap Finding can reopen. The root is filled when
the whole tree is filled. Preset gaps are excluded from auto-rollup —
they're persistent by design.

Gap Finding reads findings — including fail findings — and acts. A fail finding typically becomes a decompose; sometimes a create or a retire. Gap Finding's prospective guess at what is leaf-ready is the primary mechanism; worker fail findings are the retrospective backstop when that guess was wrong.

## Gap Finding is a structural author, not an orchestrator

Gap Finding writes nodes. The orchestrator dispatches drones to them. The two are kept strictly separate.

- The **orchestrator** is mechanical: topological order, claim-next-ready, retry-once.
- **Gap Finding** is epistemic: what structural change does the collective mind need.

Gap Finding never assigns work, prioritizes work, or holds a global view of the tree. It reads local state and writes one edit.

## Alignment

**Alignment** is another always-open preset gap, highly related to
decomposition. Per invocation, the drone working it may emit **up to 5
batched findings** — concurrent issues are surfaced together rather than
serialized across cadence windows. It reads findings against root intent
and any subsequent user inputs and watches for:

- `alignment_invalidated_premise` — a subtree assumes something a recent
  finding disproves (e.g. a user pivot makes a DTC subtree wrong-direction).
- `alignment_unmet_intent` — a gap is marked filled but the intent isn't
  actually satisfied.
- `alignment_missing_subtree` — the root or a current branch implies work
  that no gap covers.
- `alignment_no_issue` — the tree is sound; emit alone.

Gap Finding reads those findings on a later pass and either retires a
subtree, reopens a gap, rewrites an intent, or creates new gaps and
subtrees.

Alignment never edits structure. Its output is epistemic — a finding, the
same primitive every drone produces. The one-writer rule for Gap Finding
holds absolutely.

There is no pre-hoc check on decompositions. Bad decompositions are
corrected the same way missing work is corrected: Alignment notices,
writes a finding, Gap Finding acts. The cost is a bounded lag across two
invocations — accepted deliberately.

## Edge cases

- **Retired children.** When all of a gap's children are retired, the gap becomes a leaf again. It carries metadata pointing to the findings that explain why its previous children didn't work, so Gap Finding doesn't make the same mistake twice.
- **Reopened gaps.** Children are preserved. The existing findings are real work; they just didn't add up to the parent's intent. Gap Finding decides on a later pass whether to add new children or revise the decomposition.

Retirement is not deletion — retired gaps and their findings stay in the graph. Pruning and compression of the collective mind is handled by separate mechanisms outside the decomposition process.

## Flow

- **Intent** flows down through decomposition, splitting at each level.
- **Findings** flow up through filling and sideways from Alignment.
- **Structure** flows only through Gap Finding — no planner, no ratifier, no backstop. Every structural decision is one drone, one verb, one gap, visible to all.
