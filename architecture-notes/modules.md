# Modules

Each module is a directory under `src/drone_graph/` with a single responsibility. Boundaries are enforced by imports: `substrate` imports nothing internal; higher-level modules import down the stack, not across.

## Dependency rules

- `substrate/` imports nothing internal.
- `gaps/records` imports nothing internal.
- `gaps/store` may import `substrate` only (`GapStore` runs Cypher against Neo4j).
- `terminal/` imports nothing internal.
- `prompts/` is text + a loader; imports nothing internal.
- `skills_marketplace/` does not import other `drone_graph.*` packages (only third-party libs and sibling files under `skills_marketplace/`).
- `model_registry/` may import `gaps` (for `ModelTier`), `drones.providers` (for `Provider`), and `skills_marketplace` (Crawl4AI doc tools from `doc_enrich.py`) — not `substrate`, not `terminal`.
- `drones/` may import `substrate`, `gaps`, `terminal`, `prompts`. It does **not** yet import `model_registry` (tier → `vendor_model_id` wiring is still ahead); when added, keep imports one-way from `drones` into `model_registry`, not the reverse.
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
- `SCHEMA_STATEMENTS` — `schema.py` list consumed by `init_schema`

**Grows into.** Fog-of-war loaders (`FindingHandle.expand()`, `.neighbors()`), claim-and-lease primitives, optimistic-locking helpers, vector-search integration.

**Depends on.** Nothing internal.

---

## `gaps/`

**Purpose.** Typed records, enums, and the **GapStore** API over the gap subgraph. Records stay pure IO-free shapes; the store is the only gap-layer code that touches Neo4j.

**Interface (Phase 0–1).**
- `Gap`, `Finding`, `GapStatus`, `ModelTier` — Pydantic / enum (`records.py`)
- `GapStore(substrate)` — `create`, `get`, `all_gaps`, `by_tier`, `by_age`, `open_gaps`, `ready_gaps`, `claim_next_ready`, `mark_open`, `mark_failed`, `add_blocker`, `blockers_of`, and related Cypher helpers (`store.py`)

**Grows into.** `SkillInvocation` record (Phase 4) · richer dependency helpers (Phase 1+) · validation for structured checks.

**Depends on.** `records`: nothing internal. `store`: `substrate` only.

---

## `model_registry/`

**Purpose.** The **model registry**: stable `dgraph_model_id`, vendor `provider` + `vendor_model_id`, token caps, **USD per 1M token** pricing (input/output/cache), flat **`capabilities`** string list, **`rate_limits`**, and **`deprecated`**. When populated, **`tier_defaults`** maps each **`ModelTier`** to a row in **`models`**.

**Interface (v1).**
- `ModelRegistryEntry`, `ModelRegistryFile`, `RateLimits` — Pydantic shapes (`records.py`); `normalize_capabilities_value` for legacy JSON coercion
- `ModelRegistry.load_default()` / `load_path(Path)` / `load_auto()` — packaged bootstrap or `DRONE_GRAPH_MODEL_REGISTRY_PATH`
- `resolve_for_tier` / `resolve_for_gap` — tier → non-deprecated entry
- `estimate_cost_usd` — rough cost from token counts
- `generate_registry_file`, `update_registry_file`, `sync_registry_file` (`generate.py`) — vendor list + **doc enrichment** (`doc_enrich.py`: Crawl4AI + OpenAI Responses for OpenAI rows; cached Anthropic docs + `models.list()` JSON + OpenAI or Anthropic doc LLM for Anthropic rows)
- CLI: **`drone-graph model-registry`** `fresh` | `update` | `sync` (see `cli.py`)

**Grows into.** Drone-maintained registry proposals (reviewed before merge) · optional `dgraph_model_id` on `Gap` · budget enforcement (Phase 5) · replace hard-coded `doc_enrich` with Drone + marketplace skills.

**Depends on.** `gaps` (`ModelTier`), `drones.providers` (`Provider`), **`skills_marketplace`** (OpenAI doc crawl tools via `doc_enrich.py`).

**Spec.** [`architecture-notes/model-registry.md`](model-registry.md).

---

## `drones/`

**Purpose.** The drone runtime: LLM client adapters, tool loop, spawn → run → exit with graph writes.

**Interface (Phase 0–1).**
- `run_drone(gap_id, substrate, client=..., tape=...) -> DroneResult` — blocking (`runtime.py`)
- `Provider`, `make_client`, `ChatClient`, `ChatResponse`, `ToolCall`, `Usage`, `cost_usd` — `providers.py`
- `DroneResult` — dataclass outcome (`runtime.py`)

**Grows into.** Skill loading (Phase 4) · self-parameter-setting tool (Phase 4) · budget tracking (Phase 5) · **`ModelRegistry.resolve_for_gap`** (or equivalent) when orchestration passes tier-derived model ids.

**Depends on.** `substrate`, `gaps`, `terminal`, `prompts`.

---

## `terminal/`

**Purpose.** Per-drone persistent bash session (`bash --noprofile --norc`). Marker-framed stdout, optional stderr file tail, `cd` / `export` persist across calls.

**Interface (Phase 1).**
- `Terminal` — persistent shell; `.run(cmd, timeout_s) -> CommandResult`; `.close()`
- `CommandResult` — stdout, stderr, exit_code
- `TerminalTimeout`, `TerminalDead` — failure modes
- `resolve_bash_executable()`, `is_terminal_supported()` — Windows/Git-Bash aware discovery (`shell.py`)

**Grows into.** Registration of detached processes in the collective mind (Phase 3) · port / resource reservation (Phase 3) · cwd / env snapshot for drone debugging.

**Depends on.** Nothing internal.

---

## `orchestrator/`

**Purpose.** Core loop: claim the next ready gap, spawn drone, record tape events, handle retries / failure propagation.

**Interface (Phase 1).**
- `run_once(substrate, *, provider, model, tape=..., run_drone_fn=..., make_client_fn=...) -> DroneResult | None` — `loop.py`
- `run_forever(substrate, ...)` — polling loop with signal handling
- `EventTape`, `default_tape_path` — JSONL event log (`tape.py`)

**Grows into.** Concurrency + claim-and-lease (Phase 3) · budget enforcement (Phase 5) · routing **`ModelTier`** → registry-backed model id before `make_client` (Phase 1/2).

**Depends on.** `substrate`, `gaps`, `drones`.

---

## `prompts/`

**Purpose.** Markdown text — the shared hivemind system prompt — plus a loader.

**Interface (Phase 0).**
- `hivemind.md` — the prompt itself
- `load_hivemind() -> str` — reads packaged resource text

**Grows into.** Prompt versioning (likely content-hashed, with evolution log) once changes start affecting outcomes · per-preset prompt specialization (Phase 2).

**Depends on.** Nothing internal.

---

## `skills_marketplace/`

**Purpose.** Phase‑1 **installable-style tools** (Crawl4AI fetchers) used today by **`model_registry.doc_enrich`** for vendor docs. Not a full marketplace runtime — no discovery, no drone-side loader yet.

**Interface (Phase 1).**
- `skills_marketplace.tool.openai_docs_crawl` — `get_model_card`, `get_pricing_page`, `get_deprecations_page`, `get_simple_websearch` (allowlisted hosts; SSRF-safe)
- `skills_marketplace.tool.crawl4ai_page_tool` — `fetch_allowed_page_markdown` for `developers.openai.com` only
- `skills_marketplace.skills` — placeholder package for future higher-level skills

**Grows into.** Real marketplace: versioned skills, Drone-invoked tools, catalog beyond Crawl4AI (see `architecture-notes/model-registry.md` “Future”).

**Depends on.** Nothing under other `drone_graph.*` top-level packages.

---

## `cli.py`

**Purpose.** Thin Typer entry points. Loads `.env` via `load_dotenv()` in `main()`.

**Interface (Phase 0–1).**
- `submit-gap`, `run-orchestrator`, `reset-db`
- `gap list`, `gap show`
- **`model-registry`** `fresh` | `update` | `sync`

**Grows into.** `submit-goal` (Phase 2) · `status` / `ls-gaps` (observability) · `resolve-human-action` (Phase 6) · `add-budget` (Phase 6).

**Depends on.** Anything (by design).
