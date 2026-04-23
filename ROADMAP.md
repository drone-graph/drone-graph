# Roadmap

Each phase produces a demoable artifact that stands on its own. Build order is blocking: phase N depends on phase N−1. Skipping ahead is not safe — later phases assume earlier-phase invariants.

This roadmap is living. As the architecture firms up under real runs, rewrite it. Absolute dates only — no "next week."

---

## Phase 0 — First drone, first gap

**Demo.** Hand-write a gap into Neo4j. One drone spawns, uses its terminal, writes a finding, dies. Verify in Neo4j Browser.

**Requires.** Python toolchain · Neo4j via docker-compose · minimal graph schema (Gap / Finding / Drone) · drone runtime (no skills) · per-drone terminal wrapper · hivemind system prompt v0 · orchestrator stub (single drone, sequential, FIFO) · CLI for gap insertion and orchestrator run.

**Out of scope.** Gap finder · concurrency · claim-and-lease · signal protocol · skills · tool registry · alignment · memory management · vector search · Secret Store · ontology bootstrap · budget enforcement.

**Acceptance.** `drone-graph submit-gap "Create /tmp/hello.txt containing 'hi'"` followed by `drone-graph run-orchestrator` results in: the file exists, Neo4j contains `(:Gap)-[:CLOSED_WITH]->(:Finding)-[:PRODUCED_BY]->(:Drone)`, gap status is `closed`.

**Blocks.** Everything.

**Detailed spec.** [`architecture/phase-0.md`](architecture/phase-0.md).

---

## Phase 1 — Orchestrator & multi-gap

**Demo.** Submit several gaps; orchestrator processes them sequentially, honoring `BLOCKED_BY` dependencies.

**Requires.** Gap store query API · status transitions (`open → in_progress → closed | failed`) · topological ordering of the gap DAG · typed gap queries (by status, by tier, by age).

**Out of scope.** Concurrency · signal protocol.

**Acceptance.** Submit three gaps where B depends on A and C depends on B; they execute A → B → C regardless of insertion order.

**Blocks.** Phase 2.

---

## Phase 2 — Gap finder preset (the real thesis demo)

**Demo.** User submits a goal. Gap finder decomposes into a tree. Worker drones pick off leaves. Results roll back up. First run that demonstrates the thesis end to end.

**Requires.** Gap finder preset drone · decomposition that respects the ~5-layer depth rule (leaves become workable) · alignment preset (even primitive — "does the output satisfy the goal text") · trigger logic: gap finder runs on schedule + when open gap count is low + on user input.

**Out of scope.** Concurrency beyond naive sequential · autonomous skill authoring · Secret Store.

**Acceptance.** Submit "write a short story about a lighthouse." Gap finder decomposes into ~3–10 gaps (outline, character, scenes, polish). Worker drones close leaves. Final story exists as a finding referenced by the root gap.

**Blocks.** Phase 3+.

---

## Phase 3 — Concurrency & signal protocol

**Demo.** Multiple drones run concurrently. No double-work. No corrupt findings. No duplicate package installs. No two drones editing the same file.

**Requires.** Gap claim + lease with TTL · heartbeat renewal · file open registry · package install check · optimistic locking on findings writes (CAS via version property) · detached-process / port registries · dead-lease reaper.

**Out of scope.** Skill authoring.

**Acceptance.** Run a goal that produces ≥5 concurrently-workable gaps. Verify: no gap worked twice, no lost-update on findings, no duplicate `pip install` races, expired leases get reclaimed.

**Blocks.** Phase 4+.

---

## Phase 4 — Skills & tool registry

**Demo.** Drones load, use, author, and version skills. Packages install into a shared venv; tool registry tracks them; stale tools get pruned.

**Requires.** Claude Code skills loader · skill IDs as `content-hash` · semver in frontmatter · evolution log (`parent_hash`) · trust tiers (`authored_by`, `validated`) · skill selection via vector search + findings-of-past-skill-use (`SkillInvocation` nodes) · tool registry CRUD · shared venv management · install-check integrated with the signal protocol.

**Out of scope.** Downloading skills from the internet (trust not yet solved) · full preset suite beyond gap finder + alignment.

**Acceptance.** A drone authors a new skill mid-run; a later drone finds it via search, loads it, uses it; the invocation is recorded as a `SkillInvocation` finding with outcome metadata; a third drone working a similar gap finds the skill by past-success ranking.

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
