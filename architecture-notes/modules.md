# Modules

Each module is a directory under `src/drone_graph/` with a single
responsibility. Boundaries are enforced by imports: `substrate` imports
nothing internal; higher-level modules import down the stack, not across.

## Dependency rules

- `substrate/` imports nothing internal.
- `gaps/records` imports nothing internal.
- `gaps/store` may import `substrate` only (`GapStore` runs Cypher against
  Neo4j).
- `terminal/` imports nothing internal.
- `prompts/` is text + a loader; imports nothing internal.
- `tools/records` imports nothing internal.
- `tools/store` may import `substrate` and `embeddings` (optional SQLite
  embedding sidecar via `ToolStore`).
- `tools/trust` imports `tools.store` and `tools.registry` only
  (`effective_trust` helper).
- `tools/registry` and `tools/builtins/*` may import `gaps`,
  `skills_marketplace.skill_packages`, `terminal`, `tools.records`,
  `tools.store` — the builtin dispatchers run
  against the drone's runtime context, which is provided to them by
  `drones/runtime`.
- `skills_marketplace/` does not import other `drone_graph.*` packages
  (only third-party libs and sibling files).
- `model_registry/` may import `gaps` (for `ModelTier`), `drones.providers`
  (for `Provider`), and `skills_marketplace` (Crawl4AI doc tools) — not
  `substrate`, not `terminal`, not `tools`.
- `drones/` may import `substrate`, `gaps`, `terminal`, `tools`, `prompts`.
  It does **not** yet import `model_registry`. (`drones.runtime` lazily
  imports `orchestrator.preload` to render preset gap context — that's the
  one cross-import allowed, deliberately deferred.)
- `orchestrator/` may import `substrate`, `gaps`, `tools`, `drones`,
  `skills_marketplace.skill_packages` (`preload.render_preloads` loads
  `SKILL.md` packages).
- `cli.py` may import anything.

If you're about to import across a sibling (e.g., `terminal` reaching into
`drones`), stop — that's a layering violation and means something is
miscategorized.

---

## `substrate/`

**Purpose.** All Neo4j access. The only module allowed to import the
`neo4j` driver.

**Interface.**
- `Substrate(uri, user, password)` — constructor
- `.session()` — context manager yielding a `neo4j.Session`
- `.init_schema()` — idempotent (constraints + indexes for `:Gap`,
  `:Finding`, `:Drone`, `:Tool`)
- `.execute_read(cypher, **params) -> list[dict]`
- `.execute_write(cypher, **params) -> list[dict]`
- `SCHEMA_STATEMENTS` — `schema.py` list consumed by `init_schema`

**Grows into.** Vector-search integration · claim-and-lease primitives
(Phase 3) · optimistic-locking helpers.

**Depends on.** Nothing internal.

---

## `gaps/`

**Purpose.** Typed records and the **GapStore** API over the gap subgraph.
Records stay pure IO-free shapes; the store is the only gap-layer code that
touches Neo4j.

**Interface.**
- `Gap`, `Finding`, `GapStatus`, `FindingAuthor`, `FindingKind`, `ModelTier` —
  Pydantic / enum (`records.py`). Gap fields: `id`, `intent`, `criteria`,
  `status`, `reopen_count`, `retire_reason`, `model_tier`, `created_at`,
  `tool_loadout`, `tool_suggestions`, `context_preload`, `preset_kind`.
  Finding fields: `id`, `tick`, `author`, `kind`, `summary`,
  `affected_gap_ids`, `artefact_paths`, `created_at`.
- `GapStore(substrate)` — full read/write API:
  - Reads: `get`, `all_gaps`, `roots`, `leaves`, `children_of`, `parent_of`,
    `parent_edges`, `recent_findings`, `all_findings`,
    `find_leaf_by_intent_substring`, `get_preset`.
  - Writes: `apply_decompose`, `apply_create`, `apply_retire`,
    `apply_reopen`, `apply_rewrite_intent`, `apply_noop`, `apply_fill`
    (with auto-rollup via `_propagate_fill_upwards`), `apply_fail`,
    `append_finding`, `create_root`, `upsert_preset`, `reset_all`.

**Grows into.** Richer dependency helpers · validation for structured
checks · `SkillInvocation` record (Phase 4).

**Depends on.** `records`: nothing internal. `store`: `substrate` only.

---

## `tools/`

**Purpose.** The collective mind's **tool registry**, both the graph-side
metadata and the in-process Python dispatch table. Tools live as `:Tool`
nodes alongside gaps and findings; their implementations are either
builtin Python callables or installed shell-runnable documentation.

**Interface.**
- `records.py`: `Tool` (Pydantic), `ToolKind` (`builtin | installed`),
  **`TrustTier`** (`high | standard | low | blocked`), **`trust_tier`** on each
  tool (installed defaults **standard**; mirrored builtins **high**),
  `empty_input_schema`. Optional `skill_package_path` /
  `skill_package_id` link an installed tool to a directory containing
  `SKILL.md` (operator-supplied paths are a trust boundary). Optional
  `needs_venv`: when set on an installed tool, `terminal_run` with matching
  `invocation_tool_name` sources `DRONE_GRAPH_WORKSPACE/.venv` before `cmd`.
  **`last_used_at`** bumps on `record_usage`; **`deprecated_at` /
  `deprecated_reason`** soft-hide installed tools from default discovery.
- `store.py`: `ToolStore(substrate, embedding_store=..., embedder=...)` —
  `get`, `all_tools`, `search`, `depends_on`, `upsert_builtin` (idempotent,
  used by init; persists **`trust_tier`** on builtins and installed tools),
  `register_installed` (drone-driven), `record_usage`
  (`USED_BY` edge + updates `last_used_at`, clears deprecation on reuse),
  `flag` (alignment marking), **`deprecate_stale_installed_tools`** (soft-deprecate
  stale or flagged **installed** tools; deletes description embeddings when the
  sidecar is configured); **`is_discoverable`** helper for listing; optional
  embedding backfill; `semantic_search_configured` /
  `semantic_rank_tool_names` when embeddings are wired (delegates to
  `rank_tools_by_query`).
- `registry.py`: `BuiltinTool`, `ToolResult`, `DroneContext` (mutable
  per-drone state shared with dispatchers), `register_tool` decorator,
  `get_builtin`, `list_builtins`, `universal_query_tool_names`,
  `to_anthropic_tool_def`, `builtin_to_record`.
- `trust.py`: `effective_trust(name, tool_store)` — builtins **high**; else
  graph record's **`trust_tier`**; unknown **None**.
- `builtins/` — packages of `@register_tool` definitions:
  - `queries.py` — universal cm_* read tools (`cm_get_gap`, `cm_list_gaps`,
    `cm_children_of`, `cm_parent_of`, `cm_leaves`, `cm_findings`,
    `cm_finding`, `cm_list_tools` (optional `include_deprecated`; hides soft-deprecated
    installed tools by default), `cm_search_tools` (same filter when hydrating hits),
    `cm_get_tool` (full record including deprecation fields).
  - `registry_admin.py` — `cm_deprecate_stale_tools` (Alignment preset loadout +
    `drone-graph tools deprecate-stale`; stale / flagged installed tools only).
  - `structural.py` — Gap Finding's verbs (`decompose`, `create`, `retire`,
    `reopen`, `rewrite_intent`, `noop`).
  - `alignment.py` — `write_alignment_finding`.
  - `worker.py` — emergent default loadout (`terminal_run` wraps
    `source …/activate && cmd` when the invoked installed tool has `needs_venv`
    and `DRONE_GRAPH_WORKSPACE` resolves to a `.venv`; no auto-create or pruning),
    `cm_read_gap`, `cm_write_finding`, `cm_register_tool` (optional
    `skill_package_path` / `skill_package_id`, `needs_venv`, **`trust_tier`**;
    validated with `load_skill_package`), **`cm_request_tool`** (**blocked**
    never activates; **low** only when the gap's **`tool_suggestions`** lists
    the name)).
- `mirror_builtins_to_graph(tool_store)` — called at substrate init to sync
  the Python registry into the graph.

**Grows into.** Truly callable installed tools (today they're documentation
+ `terminal_run`) · skill packages (Phase 4).

**Depends on.** `substrate` (store) · `gaps`, `terminal` (builtin
dispatchers).

---

## `embeddings/`

**Purpose.** Sidecar **SQLite** storage for dense vectors keyed by
`(tool_name, scope, model_id)` — derived data from `:Tool` metadata, not
authoritative over Neo4j.

**Interface.**
- `SQLiteEmbeddingStore(path)` — WAL file (pattern matches `signals/sqlite`);
  `upsert`, `get`, `delete`, `list_by_scope`.
- `Embedder` protocol (`model_id`, `embed(text)`).
- `maybe_index_tool_description` — best-effort backfill from `Tool.name` +
  `Tool.description` (`scope=description`).
- `rank_tools_by_query(query, store=..., embedder=...)` — embeds the query,
  cosine-similarity rank of `tool_name` ids vs stored vectors (same `model_id`);
  optional `limit`. Drones call this indirectly via universal builtin
  `cm_search_tools` when the runtime `ToolStore` has `embedding_store` and
  `embedder` configured.
- `ToolStore(..., embedding_store=..., embedder=...)` triggers indexing after
  `upsert_builtin` / `register_installed` when both are set; embedding
  failures never block graph writes. Soft deprecation removes matching
  description rows via `SQLiteEmbeddingStore.delete` so `cm_search_tools`
  does not rank deprecated installed tools.

**Grows into.** Skill-body scopes (`skill:*`) · real provider embedders · ANN
index (currently full scan + cosine in Python).

**Depends on.** `tools.records` only (`backfill`); no `substrate`.

---

## `drones/`

**Purpose.** The unified drone runtime: one `run_drone(gap)` entry point
for every gap, preset or emergent. Loads `hivemind.md` as system prompt,
computes the active tool surface from the gap's `tool_loadout` plus
universal queries, **merges high-trust names from `gap.tool_suggestions`**
(see `tools.trust.effective_trust`) so suggested builtins need not call
`cm_request_tool`, then runs a multi-turn message loop, terminates on a fill /
fail or max turns.

**Interface.**
- `run_drone(gap_or_id, *, store, tool_store, client, tick, max_turns,
  command_timeout_s, tape) -> DroneResult` — blocking (`runtime.py`)
- `DroneResult` — outcome (`fill | fail | preset_done | max_turns | error`),
  finding id, findings_written, token / cost / turn counts.
- `Provider`, `make_client`, `ChatClient`, `ChatResponse`, `ToolCall`,
  `Usage`, `cost_usd`, `resolve_orchestrator_provider_model` —
  `providers.py`. Uses **`ANTHROPIC_API_KEY`** / **`OPENAI_API_KEY`** (SDK
  convention). When CLI omits `--provider`, resolution is: only Anthropic key
  → Anthropic; only OpenAI key → OpenAI; **both keys → Anthropic** (pass
  `--provider openai` to use OpenAI). Default models: `claude-sonnet-4-6`,
  `gpt-4o`. Provider clients are wrapped in bounded exponential backoff for
  transient errors.

**Grows into.** Skill loading (Phase 4) · self-parameter-setting tool
(Phase 4) · budget tracking (Phase 5) · `ModelRegistry.resolve_for_gap`
when tier-derived model ids land.

**Depends on.** `substrate`, `gaps`, `terminal`, `tools`, `prompts`. Lazily
imports `orchestrator.preload` inside `run_drone` to render preset gap
context.

---

## `terminal/`

**Purpose.** Per-drone persistent bash session (`bash --noprofile --norc`).
Marker-framed stdout, stderr file tail, `cd` / `export` persist across
calls. The runtime wraps it in a `_TerminalBox` so a `TerminalDead` (e.g.
syntax error in a `{ }` block) respawns the shell instead of killing the
drone.

**Interface.**
- `Terminal` — persistent shell; `.run(cmd, timeout_s) -> CommandResult`;
  `.close()`
- `CommandResult` — stdout, stderr, exit_code
- `TerminalTimeout`, `TerminalDead` — failure modes
- `resolve_bash_executable()`, `is_terminal_supported()` — Windows /
  Git-Bash aware discovery
- `workspace_venv.py` — `WORKSPACE_ENV` (`DRONE_GRAPH_WORKSPACE`),
  `resolve_venv_activate_script()` → `{workspace}/.venv` (`bin/activate` or
  `Scripts/activate`); operator-managed disk layout only.

**Grows into.** Registration of detached processes in the collective mind
(Phase 3) · port / resource reservation · cwd / env snapshot.

**Depends on.** Nothing internal.

---

## `orchestrator/`

**Purpose.** The combined loop: dispatch the Gap Finding preset drone
each cycle, the Alignment preset drone every N cycles, optionally a worker
drone every N cycles. Inject scheduled scenario events. Stop on max
cycles, 3 consecutive noops with no work pending, or too many consecutive
client errors.

**Interface.**
- `bootstrap.init_collective_mind(substrate) -> (GapStore, ToolStore)` —
  schema + builtins mirrored to graph + preset gaps minted. Idempotent.
  Constants: `PRESET_GAP_FINDING`, `PRESET_ALIGNMENT`. Alignment preset
  `tool_loadout` includes `write_alignment_finding` and
  `cm_deprecate_stale_tools`.
- `loop.run_combined_loop(*, substrate, client, scenario_name=None,
  out_dir=None, tape=None, target_leaves, align_every, max_gf,
  worker_every, worker_max_turns, preset_max_turns, reset=None)` — main
  entry. Writes `events.jsonl`, `tape.jsonl`, `timeline.md`, `tree.md`,
  `summary.md` under `out_dir`.
- `preload.PRELOADERS`, `preload.render_preloads` — context preloaders
  (`recent_findings`, `leaves`, `tree_shape`) plus `skill_package:<path>`
  (on-disk `SKILL.md` packages) merged into gaps' `context_preload`.
- `scenarios.{available_roots,available_scenarios,inject_event,
  load_root_seed,load_scenario}` — root + scheduled-event loaders.
- `tape.EventTape`, `tape.default_tape_path` — JSONL drone-lifecycle log.
- `rendering.render_tree`, `rendering.render_findings` — used by
  `_write_run_artifacts` and the CLI.

**Grows into.** Concurrency + claim-and-lease (Phase 3) · budget
enforcement (Phase 5) · richer scenario types beyond scheduled events.

**Depends on.** `substrate`, `gaps`, `tools`, `drones`.

---

## `prompts/`

**Purpose.** The single shared system prompt and a loader. Per-role prose
moved into preset gap intent text in `orchestrator/bootstrap.py` —
`prompts/` only holds the cross-cutting hivemind framing.

**Interface.**
- `hivemind.md` — the system prompt for every drone
- `load_hivemind() -> str`

**Grows into.** Prompt versioning (likely content-hashed, with evolution
log) once changes start affecting outcomes.

**Depends on.** Nothing internal.

---

## `model_registry/`

**Purpose.** The model registry: stable `dgraph_model_id`, vendor
`provider` + `vendor_model_id`, token caps, USD-per-1M-token pricing
(input / output / cache), flat `capabilities` list, `rate_limits`,
`deprecated`. When populated, `tier_defaults` maps each `ModelTier` to a
row in `models`.

**Interface (v1).**
- `ModelRegistryEntry`, `ModelRegistryFile`, `RateLimits` — Pydantic
  shapes (`records.py`); `normalize_capabilities_value` for legacy JSON
  coercion
- `ModelRegistry.load_default()` / `load_path(Path)` / `load_auto()` —
  packaged bootstrap or `DRONE_GRAPH_MODEL_REGISTRY_PATH`
- `resolve_for_tier` / `resolve_for_gap` — tier → non-deprecated entry
- `estimate_cost_usd` — rough cost from token counts
- `generate_registry_file`, `update_registry_file`, `sync_registry_file` —
  vendor list + doc enrichment
- CLI: `drone-graph model-registry fresh | update | sync`

**Grows into.** Drone-maintained registry proposals (reviewed before
merge) · optional `dgraph_model_id` on `Gap` · budget enforcement
(Phase 5) · replace hard-coded `doc_enrich` with Drone + marketplace
skills.

**Depends on.** `gaps` (`ModelTier`), `drones.providers` (`Provider`),
`skills_marketplace` (OpenAI doc crawl tools via `doc_enrich.py`).

**Spec.** [`architecture-notes/model-registry.md`](model-registry.md).

---

## `skills_marketplace/`

**Purpose.** Marketplace-adjacent helpers: **on-disk skill packages**
(`skill_packages/` — `SKILL.md` + optional `metadata.json`, loaded by preload
and `cm_register_tool`), plus pre-tools-registry installable-style tools
(Crawl4AI fetchers) used today by `model_registry.doc_enrich`.

**Interface.**
- `skills_marketplace.skill_packages` — `load_skill_package`, path resolution
  (`DRONE_GRAPH_SKILL_ROOT`), `ParsedSkillPackage`, `SkillPackageError`
- `skills_marketplace.tool.openai_docs_crawl` — `get_model_card`,
  `get_pricing_page`, `get_deprecations_page`, `get_simple_websearch`
  (allowlisted hosts; SSRF-safe)
- `skills_marketplace.tool.crawl4ai_page_tool` —
  `fetch_allowed_page_markdown` for `developers.openai.com` only
- `skills_marketplace.skills` — placeholder package for future skills

**Grows into.** Deeper marketplace integration with `tools/` and vector
search.

**Depends on.** Subpackages use only third-party libs and each other;
`skill_packages` records use Pydantic only (no `substrate` / `gaps` / `tools`).

---

## `cli.py`

**Purpose.** Thin Typer entry points. Loads `.env` via `load_dotenv()` in
`main()`.

**Interface.**
- `reset-db` — wipe the substrate, re-mint preset gaps + builtin tools.
- `gap list [--status ...]`, `gap show <id-prefix>`,
  `gap tree`, `gap create --intent ... --criteria ...`.
- `finding list [-n N] [--author ...] [--kind ...]`,
  `finding show <id-prefix>`.
- `drone run [GAP_ID] [--provider ...] [--model ...] [--max-turns N]
  [--tape PATH]` — dispatch one drone against a gap (or auto-pick the
  oldest active leaf).
- `model-registry fresh | update | sync`.

**Grows into.** `submit-goal` shortcut · `tool list` / `tool show` (read
the `:Tool` registry from the CLI) · `resolve-human-action` (Phase 6) ·
`add-budget` (Phase 6).

**Depends on.** Anything (by design).
