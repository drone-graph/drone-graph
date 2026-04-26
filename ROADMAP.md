# Roadmap

Each phase produces a demoable artifact that stands on its own. Build order is blocking: phase N depends on phase N−1. Skipping ahead is not safe — later phases assume earlier-phase invariants.

This roadmap is living. As the architecture firms up under real runs, rewrite it. Absolute dates only — no "next week."

> **Status (2026-04):** Phases 0–2 effectively land in the unified runtime —
> one drone class, gaps as work units, a graph-backed `:Tool` registry, preset
> gaps for Gap Finding and Alignment, batched structural edits, deterministic
> auto-rollup, and `rewrite_intent`. Real worker drones close emergent leaves
> against scenarios like `coffee-pivot-b2b` end to end. Phase 4 has its
> *substrate* (Tool nodes + `cm_register_tool` + `cm_request_tool` + drone-
> installed tool nodes) but no skills marketplace yet. Phase 3 (concurrency
> + signal protocol) is unstarted: the loop is still single-threaded.

---

## Phase 0 — First drone, first gap

**Status.** Done. The unified `run_drone(gap)` runtime can be exercised on a
single hand-written gap via `drone-graph gap create` + `drone-graph drone run`.
Drone, Gap, Finding nodes all show up in Neo4j Browser as designed.

**Detailed spec.** [`architecture-notes/phase-0.md`](architecture-notes/phase-0.md).

---

## Phase 1 — Multi-gap orchestrator

**Status.** Subsumed by Phase 2. The original `BLOCKED_BY` DAG model was
replaced by gap **decomposition** (Gap Finding mints children when a gap is
too broad for one pass) plus auto-rollup (parent fills when all non-retired
children fill). Sequencing is now driven by the tree shape and worker
attempt order, not an explicit DAG.

---

## Phase 2 — Gap finder preset (the real thesis demo)

**Status.** Done. The Gap Finding preset gap is minted at substrate init with
a fixed `tool_loadout` (`decompose / create / retire / reopen / rewrite_intent
/ noop`) and a `context_preload` that pre-renders recent findings + leaves +
tree shape into the drone's initial message. Alignment is a peer preset
(`tool_loadout = [write_alignment_finding]`). Both preset drones can batch
up to 5 edits/findings per invocation. Real workers fill emergent leaves and
the substrate auto-rolls up filled subtrees.

**Demo.** Run `python -m drone_graph.orchestrator.loop --scenario
coffee-pivot-b2b --model claude-haiku-4-5-20251001 --worker-every 2 --out
var/runs/<dir>`. Gap Finding decomposes the root, alignment surfaces drift,
workers fill leaves, the substrate rolls up parents.

---

## Phase 3 — Concurrency & signal protocol

**Status.** Not started. The orchestrator loop is single-threaded and
dispatches one drone at a time. No claim-and-lease yet, no signal protocol.

**Demo.** Multiple drones run concurrently. No double-work. No corrupt findings. No duplicate package installs. No two drones editing the same file.

**Requires.** Gap claim + lease with TTL · heartbeat renewal · file open registry · package install check · optimistic locking on findings writes (CAS via version property) · detached-process / port registries · dead-lease reaper.

**Out of scope.** Skill authoring.

**Acceptance.** Run a goal that produces ≥5 concurrently-workable gaps. Verify: no gap worked twice, no lost-update on findings, no duplicate `pip install` races, expired leases get reclaimed.

**Blocks.** Phase 4+.

---

## Phase 4 — Skills & tool registry

**Status (partial).** Substrate is in: tools live as `:Tool` nodes in the
graph alongside Gaps and Findings, with edges `(:Tool)-[:USED_BY]->(:Gap)`
and `(:Tool)-[:DEPENDS_ON]->(:Tool)`. Builtins are mirrored to the graph at
substrate init from a Python registry; drones can install a new tool at
runtime (e.g. `pip install playwright`) and call `cm_register_tool` to add
it to the registry as `kind=installed`, recording the install commands and
usage example. Future drones discover via `cm_list_tools`, pull into their
active set via `cm_request_tool`, and execute installed tools through
`terminal_run` using the recorded usage example. Alignment can flag a
suspicious registration (`Tool.flagged_by_alignment`).

**Still to do.** Skills loader (Claude Code style) · `SkillInvocation` finding
type that ranks past usage · vector search over tool descriptions · shared
venv lifecycle management · stale-tool pruning · trust tiers beyond the
single boolean flag.

**Demo target.** A drone authors a new skill mid-run; a later drone finds it
via search, loads it, uses it; the invocation is recorded as a
`SkillInvocation` finding with outcome metadata; a third drone working a
similar gap finds the skill by past-success ranking.

**Blocks.** Phase 5.

---

## Phase 5 — Remaining presets

**Demo.** Memory management, full alignment, and testing all run as preset drones. Stale findings get summarized/pruned. Completed gaps get end-to-end alignment-checked.

**Requires.** Memory pruning policy (decided in this phase, not before) · alignment preset integrated with gap finder (Alignment writes findings; Gap Finding reads them and retires or reopens) · testing preset (runs structured checks on closed gaps) · entity resolution skill or preset (multiple drones will propose duplicate nodes in findings/ontology — canonicalize).

**Out of scope.** Human interface beyond existing CLI.

**Acceptance.** Run a multi-day swarm. Memory footprint stays bounded (defined metric TBD in this phase). Alignment catches a regression introduced by a bad drone run. Entity resolution merges two drones' duplicate proposals for the same entity.

**Blocks.** Phase 6.

---

## Phase 6 — Human interface & Secret Store

**Demo.** Swarm pauses a gap tree pending user action (MFA, add funds, provide credentials). User resolves. Swarm resumes. Secret Store holds API keys scoped per-drone.

**Requires.** Secret Store (separate from graph DB, encrypted, scoped API) · `HumanActionRequired` gap type (pauses descendants until resolved) · notification mechanism (at least: CLI / terminal bell / email webhook) · UI for resolving (CLI minimum) · scoped secret access — drones call `secrets.get("github")` and never see raw values · downloaded-skill validation preset (promotes untrusted skills once verified).

**Acceptance.** Submit a goal requiring GitHub access. Drone produces a `HumanActionRequired` gap. User provides a GitHub token via CLI. Secret Store stores it. Subsequent drone uses the token via scoped API and closes the gap.

**Blocks.** Phase 7.

---

## Phase 7 — Autonomous business (north star)

**Demo.** Submit a goal like "run an online storefront for product X with a $N/month budget." The swarm researches the market, drafts the product catalog, deploys a storefront, integrates Stripe, acquires the first customer, handles support. User intervention limited to: MFA events, re-funding the card, yes/no decisions the swarm escalates.

**Requires.** Ontology bootstrap (early wave of gaps after a fresh goal does research to build the business ontology · reactivates when gap finder detects ontology gaps) · integration skills as first-class (GitHub, Stripe, Vercel or Cloudflare, domain registrar, email provider, analytics) · payment provisioning via issuer-enforced virtual card (Stripe Issuing / Brex / Mercury) · budget enforcement at issuer level + soft limits in the graph · observability dashboards beyond Neo4j Browser · full signal protocol battle-tested · Secret Store battle-tested.

**Acceptance.** Storefront transacts a sale end to end without human intervention beyond initial auth handoffs and funding events.

**Blocks.** N/A (terminal demo).

---

## Cross-cutting

**Observability** ships alongside Phase 0 (drone lifecycle tape: every spawn/death with gap id, duration, tokens, cost, outcome) and grows with each phase. Neo4j Browser is the graph inspector. Custom dashboards become necessary by Phase 3.

**Testing.** Each phase has acceptance tests. Integration tests hit real Neo4j — no mocks. From Phase 4 onward, drones run a testing preset as part of closing gaps.

**Documentation.** Each module has a short `<module>.md` or header block describing purpose, interface, current state. Written as code is written.

**License.** MIT. Revisit before any public announcement if a stronger copyleft is desired.

---

## Open design questions (to be resolved by the phase listed)

| Question | Resolve by |
|---|---|
| Memory pruning policy | Phase 5 |
| Concurrency limits per host | Phase 3 |
| Skill download trust model | Phase 6 |
| Entity resolution strategy for ontology | Phase 5 |
| Observability dashboard stack | Phase 3 |
| Issuer choice for virtual card (Stripe / Brex / Mercury) | Phase 7 |
