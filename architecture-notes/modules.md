# Modules

Each module is a directory under `src/drone_graph/` with a single responsibility. Boundaries are enforced by imports: `substrate` imports nothing internal; higher-level modules import down the stack, not across.

## Dependency rules

- `substrate/` imports nothing internal.
- `gaps/` imports nothing internal.
- `terminal/` imports nothing internal.
- `prompts/` is text + a loader; imports nothing.
- `model_registry/` may import `gaps` (for `ModelTier`) and `drones.providers` (for `Provider`) only — no `substrate`, no `terminal`.
- `drones/` may import `substrate`, `gaps`, `terminal`, `prompts`, `model_registry`.
- `orchestrator/` may import `substrate`, `gaps`, `drones`.
- `cli.py` may import anything.

If you're about to import across a sibling (e.g., `terminal` reaching into `drones`), stop — that's a layering violation and means something is miscategorized.

---

## `substrate/`

**Purpose.** All Neo4j access. The only module allowed to import the `neo4j` driver.

**Interface (Phase 0).**
- `Substrate(uri, user, password)` — constructor
- `.session()` — context manager yielding a `neo4j.Session`
- `.init_schema()` — idempotent
- `.execute_read(cypher, **params) -> list[dict]`
- `.execute_write(cypher, **params) -> list[dict]`

**Grows into.** Fog-of-war loaders (`FindingHandle.expand()`, `.neighbors()`), claim-and-lease primitives, optimistic-locking helpers, vector-search integration.

**Depends on.** Nothing internal.

---

## `gaps/`

**Purpose.** Typed records and enums. Pure data shapes, no IO.

**Interface (Phase 0).**
- `Gap` — pydantic model
- `Finding` — pydantic model
- `GapStatus` — enum (`open`, `in_progress`, `closed`, `failed`)
- `ModelTier` — enum (`cheap`, `standard`, `frontier`)

**Grows into.** `SkillInvocation` record (Phase 4) · `GapDependency` helper (Phase 1) · validation for structured checks.

**Depends on.** Nothing internal.

---

## `model_registry/`

**Purpose.** The **model registry**: stable `dgraph_model_id`, vendor `provider` + `vendor_model_id`, token caps, **USD per 1M token** pricing (input/output/cache), multi-valued **capabilities**, **rate_limits**, and **`deprecated`**. When populated, **`tier_defaults`** maps each **`ModelTier`** to a row in **`models`**.

**Interface (v1).**
- `ModelRegistryEntry`, `ModelRegistryFile`, `RateLimits` — Pydantic shapes (`records.py`).
- `ModelRegistry.load_default()` / `load_path(Path)` / `load_auto()` — load packaged **bootstrap** (empty `models[]`) or `DRONE_GRAPH_MODEL_REGISTRY_PATH` / merged JSON after `generate-model-registry`.
- `resolve_for_tier` / `resolve_for_gap` — routing from gap tier to a non-deprecated entry.
- `estimate_cost_usd` — rough cost from token counts using registry prices.
- `generate_registry_file` (`generate.py`) + CLI `drone-graph generate-model-registry` — list models from vendor APIs (env keys), then web-search doc enrichment (Anthropic preferred, else OpenAI); writes JSON for `DRONE_GRAPH_MODEL_REGISTRY_PATH`. **Temporary:** calls LLM + web search **directly**; **future** = same work done by a **Drone**, with **web search** as a **skills marketplace** tool (see `architecture-notes/model-registry.md`).

**Grows into.** Drone-driven discovery of new models and proposed registry updates (reviewed before merge) · optional `dgraph_model_id` override on `Gap` · stricter budget enforcement (Phase 5) · marketplace-backed tools instead of hard-coded vendor web search.

**Depends on.** `gaps` (for `ModelTier`), `drones.providers` (for `Provider` enum alignment).

**Spec.** [`architecture-notes/model-registry.md`](model-registry.md).

---

## `drones/`

**Purpose.** The drone runtime. Spawn, run to completion, dissolve.

**Interface (Phase 0).**
- `run_drone(gap_id, substrate) -> DroneResult` — blocking
- `Provider` — enum (`anthropic`, `openai`)
- `make_client(provider, model) -> ChatClient`

**Grows into.** Skill loading (Phase 4) · self-parameter-setting tool (Phase 4) · budget tracking during run (Phase 5) · skill authoring (Phase 4) · resolve models via `model_registry`.

**Depends on.** `substrate`, `gaps`, `terminal`, `prompts`, `model_registry`.

---

## `terminal/`

**Purpose.** Per-drone bash session. One per drone, dies with the drone.

**Interface (Phase 0).**
- `Terminal()` — spawns bash subprocess
- `.run(cmd, timeout) -> CommandResult`
- `.close()`

**Grows into.** Registration of detached processes in the collective mind (Phase 3) · port / resource reservation (Phase 3) · cwd / env snapshot for drone debugging.

**Depends on.** Nothing internal.

---

## `orchestrator/`

**Purpose.** Core loop: find gap, spawn drone, collect, update.

**Interface (Phase 0).**
- `run_once(substrate) -> DroneResult | None`
- `run_forever(substrate, poll_interval_s)`

**Grows into.** Dependency-aware gap selection (Phase 1) · concurrency + claim-and-lease (Phase 3) · budget enforcement (Phase 5) · provider routing by `ModelTier` (Phase 1/2).

**Depends on.** `substrate`, `gaps`, `drones`.

---

## `prompts/`

**Purpose.** Markdown text — the shared hivemind system prompt — plus a loader.

**Interface (Phase 0).**
- `hivemind.md` — the prompt itself
- `load_hivemind() -> str`

**Grows into.** Prompt versioning (likely content-hashed, with evolution log) once changes start affecting outcomes · per-preset prompt specialization (Phase 2).

**Depends on.** Nothing internal.

---

## `cli.py`

**Purpose.** Thin typer entry points. No business logic lives here.

**Interface (Phase 0).**
- `submit-gap <description>`
- `run-orchestrator`
- `reset-db`

**Grows into.** `submit-goal` (Phase 2) · `status` / `ls-gaps` (observability) · `resolve-human-action` (Phase 6) · `add-budget` (Phase 6).
