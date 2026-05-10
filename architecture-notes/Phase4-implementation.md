# Phase 4 — Implementation notes

This document describes what **ships today** under the Phase 4 umbrella in drone-graph: an evolving skills-and-registry layer on top of the graph-backed tool substrate. It complements [`ROADMAP.md`](../ROADMAP.md) (which still lists aspirational items); when the two disagree on intent, **this file reflects the code**.

Phase 4 centers on **discoverable tools**, **optional semantic ranking**, **trust-aware activation**, **on-disk skill packages**, **telemetry when installed tools are exercised**, and **lifecycle hygiene** for operator-registered capabilities—not on replacing builtins with arbitrary downloaded agents.

---

### Registry as the collective catalogue

Tools remain first-class **Neo4j** nodes (`:Tool`) mirrored from the Python `@register_tool` registry at substrate init. Workers extend the catalogue at runtime via **`cm_register_tool`**, which creates **`kind=installed`** records carrying human-oriented **`usage`** strings, optional **`install_commands`**, and structured metadata the runtime uses later for discovery and execution policy.

---

### Semantic embeddings as an optional sidecar

Dense vectors for tool descriptions live in a **SQLite** sidecar (`embeddings/` — `SQLiteEmbeddingStore`), keyed by tool name and scope. **`ToolStore`** accepts optional **`embedding_store`** and **`embedder`**; when both are set, upserts backfill description vectors and **`cm_search_tools`** can **cosine-rank** tool names for a natural-language query via **`rank_tools_by_query`**. If embeddings are not configured, semantic search returns a clear “not configured” path while listing tools still works through **`cm_list_tools`**. Indexing failures never block graph writes.

---

### Trust tiers and gap-level suggestions

Each **`Tool`** carries a **`trust_tier`** (high, standard, low, blocked). Mirrored **builtins** are treated as **high** trust. **`effective_trust`** resolves a name against the graph. At dispatch, **`run_drone`** merges **high**-trust names found in the gap’s **`tool_suggestions`** into the **active** tool set so those tools are available without an extra request step. **`cm_request_tool(name)`** enforces policy: **blocked** tools never activate; **low**-trust tools only activate when the current gap’s **`tool_suggestions`** explicitly lists the name—closing the “pull in anything from the graph” footgun for untrusted registrations.

---

### On-disk skill packages

The **`skills_marketplace/skill_packages`** package parses **Claude Code–style** directories: a **`SKILL.md`** body (and optional **`metadata.json`**). Paths resolve via **`DRONE_GRAPH_SKILL_ROOT`** or the process working directory. Packages are validated when linked from **`cm_register_tool`** so bogus paths fail registration early.

---

### Injecting skill context at dispatch

**`context_preload`** on a gap may include entries of the form **`skill_package:<path>`**. **`orchestrator/preload.render_preloads`** loads the package and injects rendered content into the worker’s **initial user message** alongside built-in preloaders (`recent_findings`, `leaves`, `tree_shape`). That gives Phase 4 “load the handbook before acting” without baking prose into the gap intent.

---

### Registering tools with richer linkage

**`cm_register_tool`** accepts optional **`skill_package_path`** / **`skill_package_id`**, **`trust_tier`**, **`needs_venv`**, and related fields. Successful registration persists **`skill_package_path`** / **`skill_package_id`** on the **`Tool`** record so future drones can tie an installed name back to on-disk documentation. Validation ensures linked packages parse cleanly.

---

### How installed tools meet the terminal

Installed tools remain **documentation-first**: the authoritative execution path is still **`terminal_run`**, using the registered **`usage`** (and install story) as the operator’s contract. When **`needs_venv`** is set on an installed tool and **`DRONE_GRAPH_WORKSPACE`** points at a workspace with a **`.venv`**, **`terminal_run`** can **`source`** that environment before running the command so imports match install-time assumptions. **`invocation_tool_name`** on **`terminal_run`** ties a shell invocation back to a **`:Tool.name`** for telemetry.

---

### Skill-invocation findings

When **`terminal_run`** is called with **`invocation_tool_name`** set to a known installed tool, the runtime appends a **`Finding`** with **`kind=skill_invocation`**, populating **`invocation_tool_name`**, **`invocation_outcome`**, and **`invocation_metrics_json`** (e.g. exit code, duration). **`record_usage`** keeps **`USED_BY`** edges and **`last_used_at`** warm. These fields exist on **`Finding`** generally but are meaningful for **`skill_invocation`** rows—substrate for future “what worked for similar gaps” ranking.

---

### Deprecation and stale installed tools

**Alignment** and operators can mark installed tools as stale or unwanted. **`ToolStore.deprecate_stale_installed_tools`**, the **`cm_deprecate_stale_tools`** builtin (Alignment loadout + CLI **`drone-graph tools deprecate-stale`**), and **soft deprecation fields** (`deprecated_at`, `deprecated_reason`) hide entries from default **`cm_list_tools`** / **`cm_search_tools`** hydration while preserving auditability. Embedding rows for deprecated tools are removed from the sidecar when embeddings are enabled.

---

### Atomic install and deduplication (when signals exist)

**`cm_install_package`** (in **`tools/builtins/concurrency.py`**) coordinates **install locks** and a shared **install registry** when the runtime attaches a **`SignalStore`** (multi-drone / scheduler paths). It returns early if another drone already registered the same **`install_key`**. In **single-threaded `run_combined_loop`**, **`SignalStore`** is typically absent—those tools surface a clear error rather than silently no-op—so the supported manual path remains **`terminal_run` + `cm_register_tool`** unless concurrency is wired.

---

### Adjacent surfaces used by Phase 4 demos

**`model_registry/`** doc enrichment and **`skills_marketplace/tool`** Crawl4AI helpers are not the core registry loop, but they share the same trust posture: **allowlisted hosts**, SSRF-aware fetch, and machine-assisted registry updates. They demonstrate **external documentation** as an installable-style capability without collapsing into arbitrary web browsing for worker drones.

---

### Verification and operating constraints

Automated coverage includes trust-tier behavior (**`tests/test_tool_trust_tiers.py`**), provider message shaping (**`tests/test_drone_providers.py`**), embedding/ranking wiring where tested, and integration paths against Neo4j as elsewhere in the repo. Orchestrator scenarios under **`src/drone_graph/seeds/events/`** exercise long runs; **success rates depend on model and gap wording**, not only on Phase 4 machinery—narrow criteria and explicit artefact checks remain the reliable way to prove end-to-end behavior.

---

### Drift from the original Phase 4 roadmap paragraph

[`ROADMAP.md`](../ROADMAP.md) still mentions a standalone “skills loader” and “vector search” as future-shaped bullets. In practice, **skill loading** is **`skill_package:` preload + registration validation**, and **vector search** is **optional description embeddings** plus **`cm_search_tools`**. Items not covered above (e.g. shared venv **lifecycle** beyond **`needs_venv` + workspace `.venv`**, **past-success ranking** from **`skill_invocation`** density, fully autonomous marketplace trust) remain **incremental** work—not regressions in what is already merged.

---

*Last aligned with implementation audit: 2026-04-30.*
