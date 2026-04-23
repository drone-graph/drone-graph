# Model registry

The **model registry** is our source of truth for which LLMs exist in the system, how much they cost, what they can do, and how to call them. Vendor-facing strings (`vendor_model_id`) can change; **`dgraph_model_id`** is stable and owned by this project.

Orchestration resolves **`Gap.model_tier`** → a registry row → **`provider`** + **`vendor_model_id`** (+ caps) for `make_client` and Neo4j `Drone` records.

## Future: drone-maintained registry

A **preset or worker drone** may later search vendor docs / APIs for new models and **propose** registry updates (new rows, `deprecated: true`, price changes). That path should produce **reviewable artifacts** (findings or PRs) before the canonical registry file or graph is updated—same trust class as downloading skills from the internet (see `ROADMAP.md` Phase 4 / Phase 6).

### Current vs future: who runs the LLM and where docs come from?

**Today (bootstrap CLI):** `drone-graph model-registry …` calls **vendor APIs** and **doc enrichment** from this repo (`generate.py`, `doc_enrich.py`). There is **no Drone** on this path yet—it is a developer convenience to produce JSON.

**OpenAI rows (doc enrichment):**

- **Crawl4AI** via `drone_graph.skills_marketplace.tool.openai_docs_crawl` (Phase‑1 “marketplace” tooling): `get_model_card`, `get_pricing_page`, `get_deprecations_page` on `developers.openai.com` (allowlisted URLs; no arbitrary SSRF).
- One **OpenAI Responses** call per model (**no** hosted `web_search` tool on this path) consumes the crawled markdown blocks; the model emits a small JSON **overlay** (pricing, deprecation, limits) merged into the row.

**Anthropic rows (doc enrichment):**

- One live **`Anthropic().models.list()`** JSON dump per batch (`anthropic_models_list_dump.py`).
- **Cached** `platform.claude.com` plaintext from disk (`vendor_doc_cache.py`) plus that JSON in the merge prompt.
- **Doc LLM backend** depends on which API keys are set (see table below). Anthropic merge uses **OpenAI Responses** when an OpenAI key is available (preferred when both keys exist); otherwise **Anthropic Messages** without hosted web search on the **cached** path used by the main enrichment loop.

**Future:** The same job is intended to run as a **Drone** on the hivemind substrate, with doc fetch + reasoning split across **Skills** from a real **skills marketplace** instead of hard-coded `doc_enrich` + `skills_marketplace.tool` imports.

## Registry file shape

The packaged bootstrap file is `src/drone_graph/model_registry/model_registry.json`: **`models` is `[]`** and **`tier_defaults` is `{}`** until you populate it.

- **`ModelRegistry.load_default()`** reads the packaged file.
- **`ModelRegistry.load_auto()`** uses **`DRONE_GRAPH_MODEL_REGISTRY_PATH`** when set; otherwise the packaged default.

## CLI: generating and refreshing the registry

Primary commands (Typer): **`drone-graph model-registry`** with subcommands:

| Command | Behavior |
|---------|----------|
| **`fresh`** | List models from vendor APIs (per `generate.py` filters), run doc enrichment, **overwrite** the registry JSON (clean build). Default output: packaged `model_registry.json` next to `generate.py`. |
| **`update`** | Re-run **doc enrichment only** on an **existing** JSON file (no vendor list refetch). Fails if the file is missing. |
| **`sync`** | Merge **newly discovered** vendor model ids into the current file, then enrich the full list. |

Options: **`-o` / `--output`** (path), **`-v` / `--verbose`**, or env **`DRONE_GRAPH_REGISTRY_VERBOSE=1`** for noisy enrichment logs.

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...   # optional but needed for Anthropic listing + Anthropic-only doc path

uv run drone-graph model-registry fresh
uv run drone-graph model-registry fresh -o out.json -v
uv run drone-graph model-registry update
uv run drone-graph model-registry sync
```

**Keys:**

- **At least one** of `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` is required to **list** models when building from APIs.
- **Doc enrichment** requires at least one key; if **both** are set, the doc-LLM **backend** for the enrich loop is **`openai`** (default model **`gpt-5-mini`** per `doc_enrich.py`), with the OpenAI key used for Responses and the Anthropic key still used for `models.list()` and Anthropic row inputs.

| Variable | Purpose |
|----------|---------|
| **`OPENAI_API_KEY`** | OpenAI model listing; OpenAI row crawl + Responses; Anthropic row merge via Responses when both keys set. |
| **`ANTHROPIC_API_KEY`** | Anthropic model listing + `models.list()` JSON; Anthropic Messages when OpenAI key absent. |
| **`DRONE_GRAPH_MODEL_REGISTRY_PATH`** | Optional path to registry JSON for `ModelRegistry.load_auto()`. |
| **`DRONE_GRAPH_VENDOR_DOC_CACHE`** | Optional root dir for cached vendor doc plaintext (see `vendor_doc_cache.py`). |
| **`DRONE_GRAPH_VENDOR_DOC_CACHE_MAX_AGE_HOURS`** | Optional cache TTL for HTTP refetch of cached docs. |

**Caveats:** Enrichment is **non-deterministic**; review pricing and `tier_defaults` before production. OpenAI **`capabilities`** heuristics in `generate.py` are approximate; Anthropic rows derive tags from the SDK capability object when present. **`tier_defaults`** remain heuristic picks after filtering.

## Top-level JSON shape

| Field | Type | Description |
|--------|------|-------------|
| **`tier_defaults`** | object | **Bootstrap:** must be `{}` when **`models`** is `[]`. **Populated:** must map each **`ModelTier`** (`cheap`, `standard`, `frontier`) to a **`dgraph_model_id`** that exists in `models` and must not be deprecated. |
| **`models`** | array | List of **model registry entries** (see below). Empty until you generate or author a registry. |

## Model registry entry (schema)

All **prices are USD per 1 million tokens** unless noted.

| Field | Type | Required | Description |
|--------|------|----------|-------------|
| **`dgraph_model_id`** | string | yes | Stable internal id (must be unique across `models`). |
| **`provider`** | string | yes | `anthropic` or `openai` (matches `Provider` in code). |
| **`vendor_model_id`** | string | yes | Exact API model id passed to the SDK. |
| **`deprecated`** | boolean | yes | `false` = eligible for routing; `true` = kept for history but must not appear in `tier_defaults` or new runs; enrichment may **drop** the row when the doc overlay marks deprecated. |
| **`max_input_tokens`** | integer | yes | Policy ceiling for input context. |
| **`max_output_tokens`** | integer | yes | Policy ceiling for completion tokens. |
| **`reasoning_effort`** | array of strings or null | yes | Nullable list (e.g. OpenAI API reasoning levels; Anthropic effort levels from API/docs). |
| **`input_price_per_million_usd`** | number | yes | Input token price, **USD per 1M tokens**. |
| **`output_price_per_million_usd`** | number | yes | Output token price, **USD per 1M tokens**. |
| **`cache_input_price_per_million_usd`** | number or null | yes | Cached input / prompt-cache hit pricing **per 1M**; `null` if unused. |
| **`capabilities`** | array of strings | yes | **Single flat list** of tags (e.g. `tools`, `streaming`, `vision`, `json_mode`, Anthropic flags). Legacy JSON shape `{"tools":[…],"features":[…]}` is still **accepted on load** and normalized to one list (`records.normalize_capabilities_value`). |
| **`rate_limits`** | object | yes | Soft hints (`rpm`, `tpm`). |

### `rate_limits` object

| Field | Type | Description |
|--------|------|-------------|
| **`rpm`** | integer or null | Requests per minute. |
| **`tpm`** | integer or null | Tokens per minute. |

Both may be `null` if unknown.

## Resolution rules (v1)

1. If **`models`** is empty, resolution fails with a clear error until a populated registry is configured (`model-registry fresh`, **`DRONE_GRAPH_MODEL_REGISTRY_PATH`**, or replace the packaged file after merge).
2. Read **`gap.model_tier`**.
3. Look up **`tier_defaults[tier]`** → **`dgraph_model_id`**.
4. Load that entry from **`models`**; reject if missing.
5. Reject if **`deprecated`** is `true` (misconfiguration).
6. Use **`vendor_model_id`** + **`provider`** for the API client; clamp or cap with **`max_*`** as the runtime implements.

Optional later: **`dgraph_model_id` override on `Gap`**, multi-model routing, or A/B by tier.

## Related types and modules

- **`ModelTier`** — `gaps/records.py` (`cheap`, `standard`, `frontier`).
- **`Provider`** — `drones/providers.py` (`anthropic`, `openai`).
- **Implementation** — `src/drone_graph/model_registry/` (`records.py`, `registry.py`, `generate.py`, `doc_enrich.py`, `vendor_doc_cache.py`, `anthropic_models_list_dump.py`).
- **Phase‑1 doc tools** — `src/drone_graph/skills_marketplace/tool/` (`openai_docs_crawl.py`, `crawl4ai_page_tool.py`); imported by `doc_enrich` for OpenAI developer docs crawls.

**Layering note:** `architecture-notes/modules.md` may still say `model_registry` only imports `gaps` and `drones`; in practice **`doc_enrich.py` also imports `skills_marketplace`** for Crawl4AI—update `modules.md` when you next edit dependency rules.
