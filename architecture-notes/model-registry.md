# Model registry

The **model registry** is our source of truth for which LLMs exist in the system, how much they cost, what they can do, and how to call them. Vendor-facing strings (`vendor_model_id`) can change; **`dgraph_model_id`** is stable and owned by this project.

Orchestration resolves **`Gap.model_tier`** → a registry row → **`provider`** + **`vendor_model_id`** (+ caps) for `make_client` and Neo4j `Drone` records.

## Future: drone-maintained registry

A **preset or worker drone** may later search vendor docs / APIs for new models and **propose** registry updates (new rows, `deprecated: true`, price changes). That path should produce **reviewable artifacts** (findings or PRs) before the canonical registry file or graph is updated—same trust class as downloading skills from the internet (see `ROADMAP.md` Phase 4 / Phase 6).

### Current vs future: who runs the LLM and web search?

**Today (bootstrap CLI):** `generate-model-registry` calls **vendor LLM APIs directly** from this repo (`doc_enrich.py`): OpenAI **Responses** with the built-in **`web_search`** tool, or Anthropic **Messages** with the **`web_search_20250305`** server tool. There is **no Drone** and **no skills marketplace** on this path yet—it is a developer convenience to produce JSON.

**Future:** The same job is intended to run as a **Drone** (or worker) on the hivemind substrate: the **AI that reasons about docs and registry rows *is* the drone** (invoked through orchestration), and **web search is not baked into `doc_enrich` forever**—it becomes a **Skill** loaded from a **skills marketplace** (an installable capability the drone can call), alongside other tools. This module’s direct API + web-search code is a **temporary stand-in** until that runtime exists.

## Registry file shape

The packaged bootstrap file is `src/drone_graph/model_registry/model_registry.json`: **`models` is `[]`** and **`tier_defaults` is `{}`** until you run **`drone-graph generate-model-registry`** and point **`DRONE_GRAPH_MODEL_REGISTRY_PATH`** at the generated JSON (or merge generated rows into your own file). `ModelRegistry.load_auto()` uses that empty default when the env var is unset.

## Generating the registry (`generate-model-registry`)

There is **one** supported flow: **`drone-graph generate-model-registry`** lists models from vendor APIs (broad filters), then **always** runs a **web search–backed** pass against official docs for pricing, limits, and deprecation, merges results, and drops rows marked deprecated. (See **Current vs future** above: today this is direct vendor API + web search; later a **drone** + **marketplace web-search skill**.)

Set API keys in the **process environment** (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). **At least one** key must be set so listing returns at least one model.

| Variable | Purpose |
|----------|---------|
| **`OPENAI_API_KEY`** | When set, lists OpenAI chat-capable model ids (broad `gpt-*` / `o*` / `chatgpt-*` filter in `generate.py`; still skips embeddings, audio, etc.). |
| **`ANTHROPIC_API_KEY`** | When set, lists Anthropic `type == "model"` rows (broad: no extra legacy substring filter on the list step). |

**Doc enrichment (always on):** Each registry row is enriched in a **separate** API call with **at most one web search** per model (deprecation checked first from that search; pricing filled only when the same results support it). Progress logs print the **intended search focus** string for each model.

- If **`ANTHROPIC_API_KEY`** is set, enrichment uses **Anthropic Messages** + **`web_search_20250305`**, default model **`claude-haiku-4-5`**. **Web search** must be enabled for your org in the Claude Console.
- If only **`OPENAI_API_KEY`** is set, enrichment uses **OpenAI Responses** + **`web_search`**, default **`gpt-5-mini`**.
- When **both** keys are set, **Anthropic** is used for enrichment.

**Caveats:** Output is **non-deterministic**; some fields may stay at list defaults if the model omits them—**review** before production. **`tier_defaults`** are still heuristic name picks after filtering. **`capabilities`** for OpenAI remain heuristic; Anthropic rows use the SDK capability object when present.

```bash
export ANTHROPIC_API_KEY=...   # preferred when you have it (Haiku 4.5 default for enrichment)
export OPENAI_API_KEY=...      # optional: OpenAI listing; required for enrichment if no Anthropic key
uv run drone-graph generate-model-registry              # writes model_registry.json in cwd
uv run drone-graph generate-model-registry -o out.json  # custom path
```

Top level:

| Field | Type | Description |
|--------|------|-------------|
| **`tier_defaults`** | object | **Bootstrap:** must be `{}` when **`models`** is `[]`. **Populated:** must map each **`ModelTier`** (`cheap`, `standard`, `frontier`) to a **`dgraph_model_id`** that exists in `models` and must not be deprecated. |
| **`models`** | array | List of **model registry entries** (see below). Empty until you generate or author a registry. |

## Model registry entry (schema)

All **prices are USD per 1 million tokens** unless noted.

| Field | Type | Required | Description |
|--------|------|----------|-------------|
| **`dgraph_model_id`** | string | yes | Stable internal id (prefix / naming convention up to the team; must be unique across `models`). |
| **`provider`** | string | yes | `anthropic` or `openai` (matches `Provider` in code). |
| **`vendor_model_id`** | string | yes | Exact API model id passed to the SDK. |
| **`deprecated`** | boolean | yes | `false` = eligible for routing; `true` = kept for history / analytics but must not appear in `tier_defaults` and must not be chosen for new runs. |
| **`max_input_tokens`** | integer | yes | Policy ceiling for input context (aligned with product limits, not necessarily the vendor’s raw maximum). |
| **`max_output_tokens`** | integer | yes | Policy ceiling for completion tokens. |
| **`reasoning_effort`** | string or null | yes | Nullable. Meaning is model-specific (e.g. low / medium / high, or vendor-specific tokens). Use `null` when N/A. |
| **`input_price_per_million_usd`** | number | yes | Input token price, **USD per 1M tokens**. |
| **`output_price_per_million_usd`** | number | yes | Output token price, **USD per 1M tokens**. |
| **`cache_read_price_per_million_usd`** | number or null | yes | Optional. Cache read pricing where the vendor exposes it; same **per 1M** unit. `null` if unused. |
| **`cache_write_price_per_million_usd`** | number or null | yes | Optional. Cache write pricing; **per 1M**. `null` if unused. |
| **`capabilities`** | array of strings | yes | Multi-valued capability flags (e.g. `tools`, `vision`, `streaming`, `json_mode`). Unknown strings are allowed for forward compatibility; document conventions here as you adopt them. |
| **`rate_limits`** | object | yes | Soft hints for orchestration / backoff. See below. |

### `rate_limits` object

| Field | Type | Description |
|--------|------|-------------|
| **`rpm`** | integer or null | Requests per minute (vendor or your own quota). |
| **`tpm`** | integer or null | Tokens per minute (vendor or your own quota). |

Both may be `null` if unknown.

## Resolution rules (v1)

1. If **`models`** is empty, resolution fails with a clear error until a populated registry is configured (generate + **`DRONE_GRAPH_MODEL_REGISTRY_PATH`**, or replace the packaged file after merge).
2. Read **`gap.model_tier`**.
3. Look up **`tier_defaults[tier]`** → **`dgraph_model_id`**.
4. Load that entry from **`models`**; reject if missing.
5. Reject if **`deprecated`** is `true` (misconfiguration).
6. Use **`vendor_model_id`** + **`provider`** for the API client; clamp or cap with **`max_*`** as the runtime implements.

Optional later: **`dgraph_model_id` override on `Gap`**, multi-model routing, or A/B by tier.

## Related types

- **`ModelTier`** — `gaps/records.py` (`cheap`, `standard`, `frontier`).
- **`Provider`** — `drones/providers.py` (`anthropic`, `openai`).
