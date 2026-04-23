from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import quote

from anthropic import Anthropic
from anthropic.types import Message, ServerToolUseBlock, TextBlock
from anthropic.types.web_search_tool_result_block import WebSearchToolResultBlock
from openai import OpenAI
from openai.types.shared import Reasoning
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tqdm import tqdm

from drone_graph.drones.providers import Provider
from drone_graph.model_registry.anthropic_models_list_dump import (
    fetch_anthropic_models_list_json_dump,
)
from drone_graph.model_registry.records import ModelRegistryEntry
from drone_graph.model_registry.vendor_doc_cache import (
    bounded_source_section,
    corpus_is_usable,
    default_vendor_doc_cache_root,
    excerpt_for_cached_enrichment,
    load_provider_doc_corpus,
)
from drone_graph.skills_marketplace.tool.openai_docs_crawl import (
    get_deprecations_page,
    get_model_card,
    get_pricing_page,
)

# =============================================================================
# Public types & defaults
# =============================================================================

DocEnrichBackend = Literal["openai", "anthropic"]

DEFAULT_OPENAI_DOC_ENRICH_MODEL = "gpt-5-mini"
DEFAULT_ANTHROPIC_DOC_ENRICH_MODEL = "claude-haiku-4-5"
DEFAULT_DOC_ENRICH_MODEL = DEFAULT_OPENAI_DOC_ENRICH_MODEL

_PREVIEW_CHARS = 16_000
_JSON_PREVIEW_CHARS = 8000
_JSON_TRACE_CONTENT = 6000
_JSON_TRACE_BLOCK = 12_000
_OPENAI_DEPRECATIONS_URL = "https://developers.openai.com/api/docs/deprecations"
# Long deprecation pages + model card + pricing can exceed useful attention; keep head + id hits.
_OPENAI_DEP_USER_MAX_CHARS = 56_000
_OPENAI_DEP_HEAD_CHARS = 5_000
_OPENAI_DEP_CONTEXT_RADIUS = 2_500


def _openai_deprecations_user_excerpt(dep: str, vendor_model_id: str) -> str:
    """Prefer page head plus windows around this id so tables are not lost in a huge blob."""
    text = dep.strip()
    if not text:
        return text
    vid = vendor_model_id.strip()
    if not vid or len(text) <= _OPENAI_DEP_USER_MAX_CHARS:
        return text[:_OPENAI_DEP_USER_MAX_CHARS]
    head = text[:_OPENAI_DEP_HEAD_CHARS]
    needles = (f"`{vid}`", f"| `{vid}` |", f"| {vid} |")
    spans: list[tuple[int, int]] = []
    for needle in needles:
        start = 0
        while True:
            i = text.find(needle, start)
            if i == -1:
                break
            lo = max(0, i - _OPENAI_DEP_CONTEXT_RADIUS)
            hi = min(len(text), i + len(needle) + _OPENAI_DEP_CONTEXT_RADIUS)
            spans.append((lo, hi))
            start = i + max(1, len(needle) // 2)
    if not spans:
        return text[:_OPENAI_DEP_USER_MAX_CHARS]
    spans.sort()
    merged: list[tuple[int, int]] = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    pieces = [head]
    for lo, hi in merged:
        if hi <= len(head):
            continue
        lo2 = max(lo, len(head))
        if lo2 < hi:
            pieces.append(text[lo2:hi])
    out = "\n\n--- relevant-deprecation-rows ---\n\n".join(pieces)
    return out[:_OPENAI_DEP_USER_MAX_CHARS]


def _openai_responses_reasoning_effort(doc_model: str) -> str:
    """Reasoning effort for OpenAI Responses doc-enrichment calls (registry overlay JSON)."""
    m = doc_model.strip().lower()
    base = DEFAULT_OPENAI_DOC_ENRICH_MODEL.lower()
    if m == base or m.startswith(f"{base}-"):
        return "high"
    return "medium"


# =============================================================================
# LLM system prompts (registry overlay JSON)
# =============================================================================

# Anthropic-only: shape required by ``Messages.create(..., tools=[...])`` for the hosted
# ``web_search_20250305`` server tool. The OpenAI registry path uses **Crawl4AI** for the
# model card, then ``responses.create`` **without** hosted ``web_search`` (see
# ``_call_doc_enrich_one_openai``).
_ANTHROPIC_MESSAGES_WEB_SEARCH_TOOL: list[dict[str, Any]] = [
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 1},
]


def _anthropic_messages_web_search_tools() -> Iterable[Any]:
    """Argument for Anthropic ``Messages.create`` ``tools=`` (SDK typing is loose here)."""
    return cast(Iterable[Any], _ANTHROPIC_MESSAGES_WEB_SEARCH_TOOL)

_LEGACY_WEB_SEARCH_INSTRUCTIONS = """
You enrich exactly ONE model row for a machine-readable registry.

Hard rules:
1. Use the web_search tool at most ONCE for this entire turn. One query, one round of results.
2. Reason from that single search for deprecation and USD per-1M token pricing when explicit.
3. Do not chain multiple web searches or extra tool rounds.

Respond with ONLY a JSON array containing exactly ONE object (no markdown fences, no commentary),
with keys:
- provider: "openai" or "anthropic" (must match the user message)
- vendor_model_id: string (exact id from the user message)
- deprecated: boolean (required: true or false, never null)
- input_price_per_million_usd: number or null
- output_price_per_million_usd: number or null
- cache_input_price_per_million_usd: number or null
- max_input_tokens: integer or null
- max_output_tokens: integer or null
- reasoning_effort: array of strings or null — always this key name. Include only supported
  levels for this model (e.g. ["low","medium","high"]).
  For provider "openai", use only **API**-documented reasoning levels for this id; ignore
  ChatGPT consumer-only "reasoning" labels. For provider "anthropic", use Claude API effort
  levels from official docs / API metadata when applicable; otherwise null.
"""

_CACHED_JSON_RESPONSE_KEYS = """
Respond with ONLY a JSON array containing exactly ONE object (no markdown fences, no commentary),
with keys:
- provider: "openai" or "anthropic" (must match the user message)
- vendor_model_id: string (exact id from the user message)
- deprecated: boolean (required: true or false, never null)
- input_price_per_million_usd: number or null
- output_price_per_million_usd: number or null
- cache_input_price_per_million_usd: number or null
- max_input_tokens: integer or null
- max_output_tokens: integer or null
- reasoning_effort: array of strings or null — always this key name. For provider "openai",
  include only what the **OpenAI API** model card or API docs explicitly state for this
  ``vendor_model_id`` (e.g. supported Responses ``reasoning.effort`` values). If the API model
  card does not document reasoning effort for this id, use **null**. Do **not** copy "reasoning"
  labels, toggles, or tiers from **ChatGPT** (consumer app) help or subscription pages—they are
  **not** part of the API model list and often **do not** apply to this API id. For provider
  "anthropic", put supported Claude API **effort** levels from the excerpts (e.g. low, medium,
  high, xhigh, max) when this model supports the effort parameter; otherwise null.
"""

_OPENAI_CHATGPT_VS_API_SCOPE = """
**ChatGPT vs OpenAI API (mandatory):** This row is an **API** ``vendor_model_id`` (Chat
Completions / Responses), **not** the consumer **ChatGPT** product. Ignore ChatGPT Plus/Pro/Business
subscription pricing, seat bundles, and ChatGPT-only "reasoning" or **Auto** mode descriptions
**unless** the same source explicitly ties them to **this exact API id** and **API** dollar
per-token (or per-1M) rates. Consumer ChatGPT pricing and reasoning UI are **usually absent**
from ``developers.openai.com`` API model cards—do **not** invent API prices or
``reasoning_effort`` from ChatGPT marketing or help center pages. Use only the user message
(model card report, pricing page, deprecations page); otherwise leave prices and
``reasoning_effort`` as null.
"""

_OPENAI_CRAWL_DATA_LAYOUT = """
**Input layout (fixed pipeline — no browsing):** The user message has exactly three fenced
blocks, in this order: **MODEL CARD**, **API PRICING**, **API DEPRECATIONS**. Each is produced by
our crawler/parser; treat the headings and tables below as the **contract** for what you may see.

**A) MODEL CARD** — from ``get_model_card`` on ``…/api/docs/models/<model_card_doc_id>`` (snapshot
dates stripped from the URL path only; your JSON ``vendor_model_id`` stays the full API id).
This is **not** raw HTML dump. When crawl succeeds, expect markdown shaped like:
- ``# <title>`` — human-facing model title (may differ slightly from the id string).
- ``## Summary`` — a **pipe table** ``| Metric | Value |``. Common rows: **Intelligence**,
  **Speed**; when the parser found a **Text tokens** block with dollar lines, you may also see
  **Price (Input)**, **Price (Cached input)**, **Price (Output)** with ``$…`` values (same units the
  site used next to those labels; usually **per 1M text tokens** on the card).
- ``## Core Specs`` — bullets ``- **Context window:** …``, ``- **Max output tokens:** …``,
  ``- **Knowledge cutoff:** …`` when those phrases appeared in the source (text may embed numbers
  inside sentences).
- ``## Text Token Pricing (Per 1M Tokens)`` — table ``| Type | Price |`` with rows **Input**,
  **Cached input**, **Output** and ``$…`` cells when extracted.
- ``## Modalities`` / ``## Features`` — two-column tables ``| Modality | Support |`` and
  ``| Feature | Support |`` built from key/value pairs parsed between **Modalities**-**Endpoints**
  and **Features**-**Snapshots** in the compacted card text.
- ``## Snapshots`` — bullet list of snapshot API ids (gpt-prefixed ids) found between the
  **Snapshots** and **Rate limits** sections in the compact flow; your ``vendor_model_id`` may
  appear there.
- ``## Rate Limits`` — any **pipe tables** scraped from the card for rate limits.
- Optionally ``## Raw page (Crawl4AI simple websearch fallback)`` — **unprocessed** markdown from
  the same model-card URL, appended when structured parsing is thin or the first crawl returned an
  error parenthetical; mine it for pricing or specs the structured report missed.
If the MODEL CARD block is **only** a parenthetical error and the fallback is still empty,
treat card-derived numbers as **unknown** unless PRICING or DEPRECATIONS still justify them.

**B) API PRICING** — from ``get_pricing_page`` on the **global** API pricing page.
Normalized layout:
- First heading is ``# Pricing``.
- ``## <section name>`` for buckets such as **Flagship models**, **Multimodal models**,
  **Image generation models**, **Video generation models**, **Transcription models**, **Tools**,
  **Specialized models**, **Finetuning** (only sections present on the page appear).
- Under a section you may see ``### Standard``, ``### Batch``, ``### Flex``, ``### Priority`` when
  the site lists pricing modes.
- **Markdown pipe tables** follow; wrapped table lines may appear as single logical rows.
To pick a price row: find the row whose **first column** equals this ``vendor_model_id`` or an id
the row explicitly aliases to it. When several **###** modes exist, prefer **### Standard** for
default API list pricing (not Batch-only) unless the card already gave you a definitive price.

**C) API DEPRECATIONS** — from ``get_deprecations_page``. Normalized layout:
- ``# Deprecations`` top heading.
- ``## Overview``, ``## Deprecation vs. legacy``, ``## Deprecation history`` when those headings
  existed on the page.
- Dated history lines may appear as ``### YYYY-MM-DD: …``.
- Under each dated block, **markdown pipe tables** list shutdowns. Typical columns include
  **Shutdown date**, **Model snapshot** (or **Model / system**), and **Substitute model** (or
  **Recommended replacement**). API ids usually appear **backticked** in cells, e.g.
  ``| 2026-10-23  | `gpt-3.5-turbo-0125`  | `gpt-4.1-mini`  |``.

**Deprecation id matching (critical):**
- Set ``deprecated`` **true** when **API DEPRECATIONS** names **this exact** ``vendor_model_id`` in
  a deprecation table or announcement **as its own id** — typically inside backticks
  (`` `your-id` ``) or as a whole table cell matching **only** that string.
- **Do not** mark ``deprecated`` **true** just because a **longer** snapshot id contains your id as
  a prefix (example: row `` `gpt-3.5-turbo-0125` `` does **not** by itself retire the separate API
  alias ``gpt-3.5-turbo`` unless the same announcement or table row **also** names ``gpt-3.5-turbo``
  explicitly or the prose states that **all** variants of that alias are shut down).
- Scan **every** ``### YYYY-MM-DD: …`` section and **every** table under **Deprecation history**;
  older announcements can still apply if the id appears there.
- If **API DEPRECATIONS** never names this exact ``vendor_model_id`` as deprecated / shutting down,
  set ``deprecated`` **false** (absence is not proof of retirement, but do not infer from unrelated
  rows). MODEL CARD prose alone rarely proves API retirement — prefer deprecations tables.

"""

_OPENAI_REGISTRY_WEB_INSTRUCTIONS = (
    """
You enrich exactly ONE OpenAI API model row for a machine-readable registry.

"""
    + _OPENAI_CHATGPT_VS_API_SCOPE
    + _OPENAI_CRAWL_DATA_LAYOUT
    + """
Hard rules (apply in order):
1. **Identity:** Output ``vendor_model_id`` must **exactly** match the user message field. The MODEL
   CARD URL uses ``model_card_doc_id`` (trailing ``-YYYY-MM-DD`` removed from the path only); the
   report may describe the model family — still fill pricing for the **exact** API id you output.
2. **Deprecation:** Set ``deprecated`` **true** only when **API DEPRECATIONS** (see layout **C**)
   shows **this exact** ``vendor_model_id`` in a shutdown / retirement table or dated announcement
   as described there — **not** because a different longer id merely **contains** your string.
   MODEL CARD text may mention "legacy"; treat ``deprecated`` **true** only if that card text
   clearly applies **this** API id to retirement. Otherwise ``deprecated`` **false**.
3. **USD per 1M text tokens (input / output / cache_input):**
   - Prefer MODEL CARD ``## Text Token Pricing`` and **Summary** rows **Price (Input)**,
     **Price (Cached input)**, **Price (Output)** when present with ``$``.
   - Else use **API PRICING** tables for this id (see layout **B**); prefer **### Standard**.
   - ``cache_input_price_per_million_usd`` maps from **Cached input** or **Price (Cached input)**.
   - Normalize: value already per 1M → use numeric USD; per 1K → multiply by 1000; per token →
     multiply by 1e6. **null** if unknown — **never** use 0 as placeholder.
4. **max_input_tokens / max_output_tokens:** Integers from MODEL CARD **Core Specs** or explicit
   token fields when you can parse a number; else null (registry keeps prior values on merge).
5. **reasoning_effort:** Non-null only if this message explicitly documents API reasoning levels for
   **this** ``vendor_model_id`` (e.g. Responses ``reasoning.effort``). Otherwise null — never infer
   from ChatGPT consumer UI.

"""
    + _CACHED_JSON_RESPONSE_KEYS
)

_SINGLE_MODEL_CACHED_DOC_INSTRUCTIONS_BASE = """
You enrich exactly ONE model row for a machine-readable registry.

You are given EXCERPTS from official vendor documentation only (pricing and deprecations).
Do not use web search or browse the internet. Use only the text in the user message.

Hard rules (all providers):
1. **Deprecation:** Set deprecated=true when the excerpts state this vendor_model_id is
   retired, shut down, sunset, legacy-only for new customers, replaced by another id, or no
   longer offered for new API use. Set deprecated=false when the excerpts show it as a
   current, supported model for new use. If the id never appears in excerpts, default
   deprecated=false (do not assume retired without evidence).
2. **Prices:** Fill input_price_per_million_usd and output_price_per_million_usd only when the
   excerpts clearly give **authoritative** rates for this exact id (or an alias the excerpt
   explicitly equates to this id on the same price row). **Never use 0 as a placeholder for
   unknown prices** — use null when unclear or absent. Do not invent numbers.
3. **Token context window:** Fill max_input_tokens / max_output_tokens only when the excerpts
   state explicit limits for this id; otherwise null (leave unchanged downstream).

"""

_OPENAI_CACHED_DOC_INSTRUCTIONS = (
    """
**OpenAI (user message says provider: openai):**
"""
    + _OPENAI_CHATGPT_VS_API_SCOPE
    + """
- The bundle may include labeled **DEPRECATIONS** and **PRICING** sections from
  platform.openai.com. Treat deprecations **tables** as authoritative for retirement only when they
  name **this exact** ``vendor_model_id`` (usually backticked in **Model snapshot** / shutdown
  rows). A row for a **longer** snapshot id (e.g. ``gpt-3.5-turbo-0125``) does **not** by itself
  retire a **different** API alias (e.g. ``gpt-3.5-turbo``) unless the same text names your id or
  clearly states all variants are shut down. If this exact id is absent from deprecations, keep
  deprecated=false unless another excerpt explicitly retires this id.
- On the **PRICING** section, map table cells to this vendor_model_id's row (match the API id
  string; accept a display name only if the excerpt explicitly maps it to this id).
- **Normalize to USD per 1,000,000 (1M) text tokens** for input_price_per_million_usd and
  output_price_per_million_usd:
  - Value already "per 1M tokens" or "$X / 1M" → use X as the JSON number (no $).
  - "per 1K tokens" or "$X / 1K" → multiply X by 1000 for per-1M.
  - "per token" → multiply the dollar amount by 1_000_000 for per-1M.
- Prefer **standard / default** API pricing for Chat Completions or Responses (not Batch-only
  rows, not special enterprise-only tiers) when multiple prices exist for the same id.
- **Image, audio, embedding, moderation-only** SKUs: if the excerpt only gives per-image,
  per-second, or non-token units and no per-1M **text** token rate for this id, leave
  input/output token price fields null.
- **Cache:** Set cache_input_price_per_million_usd only when the pricing excerpt explicitly lists
  cached input (prompt cache / cache hits) $/1M (or convertible) for this id; otherwise null.
"""
)

_ANTHROPIC_CACHED_DOC_INSTRUCTIONS = """
**Anthropic (user message says provider: anthropic):**
- The user message includes **ANTHROPIC_MODELS_LIST_JSON** — a JSON **array** of objects returned
  by ``Anthropic().models.list()`` (all pages), each object ``model_dump(mode="json")``-shaped:
  ``id``, ``display_name``, ``type``, ``max_input_tokens``, ``max_tokens``, nested
  ``capabilities`` (e.g. ``image_input``, ``pdf_input``, ``thinking``, ``effort`` with per-level
  ``supported`` flags), ``created_at``, etc. Locate the array element whose ``id`` equals this
  row's ``vendor_model_id``.
  Treat that object as **authoritative API metadata** for capabilities and token ceilings unless
  doc excerpts clearly contradict it.
- **CURRENT_ROW_JSON** is the registry's projection of the same model (may omit API-only detail).
  Prefer **ANTHROPIC_MODELS_LIST_JSON** for nested ``capabilities`` / limits when merging.
- Official **platform.claude.com** docs are usually **Markdown** (paths like ``/docs/en/.../*.md``).
  **VENDOR DOCS** excerpts are typically **raw markdown** from direct URL fetches (allowlisted
  simple Crawl4AI crawl), not a bespoke HTML-only parser.
- **Deprecation:** Set deprecated=true when **models overview** prose (e.g.
  ``…/about-claude/models/overview`` or ``…/models/overview.md``) or pricing pages mark this id
  retired, replaced, or scheduled for shutdown.
- **Prices** — treat **``https://platform.claude.com/docs/en/about-claude/pricing.md``** as the
  primary authoritative source when that document appears in the excerpts (the site may mirror
  the same body without the ``.md`` suffix). Tables list Model, Base Input, cache columns, Output
  Tokens, etc.; map the row for this exact ``vendor_model_id``.
- **input_price_per_million_usd / output_price_per_million_usd:** USD per **1M** tokens. Convert
  MTok / per-1M as plain numbers from that pricing page (or equivalent excerpt).
- **cache_input_price_per_million_usd:** from **Cache Hits & Refreshes** (or equivalent cached
  input column) when given per **5M** tokens, **divide by 5** for per-1M; if already per 1M, use
  as-is. Use null if ambiguous or only cache-write columns are given without a clear cached-input
  rate.
- Use **null** (not 0) for unknown prices. Prefer standard (non-batch) list pricing when multiple
  tiers appear.
"""


def cached_doc_enrichment_instructions(provider: Provider) -> str:
    """System / instructions text for the cached-vendor-docs enrichment path."""
    extra = (
        _OPENAI_CACHED_DOC_INSTRUCTIONS
        if provider == Provider.openai
        else _ANTHROPIC_CACHED_DOC_INSTRUCTIONS
    )
    return (
        _SINGLE_MODEL_CACHED_DOC_INSTRUCTIONS_BASE.strip()
        + "\n"
        + extra.strip()
        + "\n"
        + _CACHED_JSON_RESPONSE_KEYS.strip()
        + "\n"
    )


# =============================================================================
# Per-model user prompts
# =============================================================================


def planned_search_query(entry: ModelRegistryEntry) -> str:
    """Single focused query string for Anthropic legacy web_search enrichment."""
    vid = entry.vendor_model_id
    return (
        f"{vid} site:platform.claude.com Claude API pricing OR models overview deprecated"
    )


_OPENAI_MODEL_CARD_TRAILING_DATE = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def _openai_model_card_doc_id(vendor_model_id: str) -> str:
    """``/api/docs/models/<id>`` path segment; strips trailing ``-YYYY-MM-DD`` if present."""
    return _OPENAI_MODEL_CARD_TRAILING_DATE.sub("", vendor_model_id.strip())


def _openai_api_model_card_url(vendor_model_id: str) -> str:
    doc_id = _openai_model_card_doc_id(vendor_model_id)
    safe = quote(doc_id, safe="")
    return f"https://developers.openai.com/api/docs/models/{safe}"


def _single_model_user_message_openai_web(
    entry: ModelRegistryEntry,
    deprecations_excerpt: str,
    *,
    model_card_markdown: str,
    pricing_excerpt: str,
) -> str:
    doc_id = _openai_model_card_doc_id(entry.vendor_model_id)
    card = _openai_api_model_card_url(entry.vendor_model_id)
    dep = deprecations_excerpt.strip() or "(deprecations excerpt unavailable)"
    price = pricing_excerpt.strip() or "(pricing page unavailable)"
    body = model_card_markdown.strip() or "(model card body empty)"
    reading_guide = (
        "## How the three blocks below were produced (read this first)\n"
        "- **MODEL CARD** — Built by ``get_model_card`` from **Official model card URL** below. "
        "Structured markdown: ``## Summary``, ``## Core Specs``, "
        "``## Text Token Pricing (Per 1M Tokens)``, ``## Modalities``, ``## Features``, "
        "``## Snapshots``, ``## Rate Limits`` when present. **Input** / **Cached input** / "
        "**Output** dollar lines are the main per-1M price signal on the card. If you see a "
        "parenthetical crawl error, use PRICING and DEPRECATIONS blocks.\n"
        "- **API PRICING** — From ``get_pricing_page``: ``# Pricing``, ``##`` family sections, "
        "``### Standard`` (and other modes), plus tables. Find the table **row for this** "
        "``vendor_model_id`` when the card omits prices.\n"
        "- **API DEPRECATIONS** — From ``get_deprecations_page``: tables under "
        "``## Deprecation history`` / ``### YYYY-MM-DD: …``. Match **this exact** "
        "``vendor_model_id`` in backticks or cells (see system prompt **Deprecation id matching**)."
        "\n"
        "Full contract: system prompt **Input layout**.\n\n"
    )
    return (
        "Enrich this OpenAI API model (vendor_model_id in output MUST match exactly):\n"
        f"  provider: openai\n"
        f"  vendor_model_id: {entry.vendor_model_id}\n"
        "  model_card_doc_id (URL path only; trailing -YYYY-MM-DD removed if present): "
        f"{doc_id}\n\n"
        + reading_guide
        + "Official model card URL (crawled for structured MODEL CARD block):\n"
        f"  {card}\n\n"
        "----- BEGIN MODEL CARD (get_model_card structured report) -----\n"
        f"{body}\n"
        "----- END MODEL CARD -----\n\n"
        "----- BEGIN API PRICING (get_pricing_page normalized) -----\n"
        f"{price}\n"
        "----- END API PRICING -----\n\n"
        "----- BEGIN API DEPRECATIONS (get_deprecations_page normalized) -----\n"
        f"{dep}\n"
        "----- END API DEPRECATIONS -----\n\n"
        "Return ONLY a JSON array of one object per system instructions."
    )


def _anthropic_registry_merge_user_message(
    entry: ModelRegistryEntry,
    excerpt: str,
    *,
    models_list_json: str,
) -> str:
    row = json.dumps(entry.model_dump(), indent=2, default=str)
    ex = excerpt.strip() or "(doc excerpt unavailable)"
    api_dump = models_list_json.strip() or "(ANTHROPIC_MODELS_LIST_JSON unavailable)"
    return (
        "Merge Claude **platform.claude.com** doc excerpts into this Anthropic registry row.\n"
        "Ground **API token pricing** on "
        "``https://platform.claude.com/docs/en/about-claude/pricing.md`` when that markdown "
        "appears in the excerpts.\n\n"
        "Below: **ANTHROPIC_MODELS_LIST_JSON** — live ``models.list()`` dump (JSON array). Each "
        "element is ``model_dump(mode=\"json\")`` from the Anthropic SDK (``id``, "
        "``capabilities``, ``max_input_tokens``, ``max_tokens``, …). Use the element whose "
        "``id`` equals "
        "``vendor_model_id`` for authoritative API fields.\n"
        "----- BEGIN ANTHROPIC_MODELS_LIST_JSON -----\n"
        f"{api_dump}\n"
        "----- END ANTHROPIC_MODELS_LIST_JSON -----\n\n"
        "CURRENT_ROW_JSON (registry row for this ``vendor_model_id``; projection may omit API-only "
        "nested fields):\n"
        f"{row}\n\n"
        "----- BEGIN DOC EXCERPTS (cached platform.claude.com) -----\n"
        f"{ex}\n"
        "----- END DOC EXCERPTS -----\n\n"
        "Return ONLY a JSON array of one object per system instructions."
    )


def _single_model_user_message(entry: ModelRegistryEntry) -> str:
    q = planned_search_query(entry)
    return (
        "Enrich this single model (vendor_model_id in output MUST match exactly):\n"
        f"  provider: {entry.provider.value}\n"
        f"  vendor_model_id: {entry.vendor_model_id}\n\n"
        "Perform exactly ONE web_search using a query essentially like:\n"
        f"  {q}\n\n"
        "Then return ONLY a JSON array of one object per system instructions."
    )


def _single_model_user_message_cached(entry: ModelRegistryEntry, excerpt: str) -> str:
    return (
        "Enrich this single model (vendor_model_id in output MUST match exactly):\n"
        f"  provider: {entry.provider.value}\n"
        f"  vendor_model_id: {entry.vendor_model_id}\n\n"
        "Official documentation excerpts (use only this text; no web search):\n"
        "----- BEGIN VENDOR DOCS -----\n"
        f"{excerpt}\n"
        "----- END VENDOR DOCS -----\n\n"
        "Return ONLY a JSON array of one object per system instructions."
    )


def _normalize_reasoning_effort_list(v: object) -> list[str] | None:
    """Coerce LLM / JSON ``reasoning_effort`` into a deduped list of non-empty strings."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else None
    if isinstance(v, list):
        out: list[str] = []
        seen: set[str] = set()
        for item in v:
            if not isinstance(item, str):
                continue
            s = item.strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out or None
    return None


# =============================================================================
# Doc overlay (single model, LLM → structured merge)
# =============================================================================


class DocOverlay(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Provider
    vendor_model_id: str = Field(..., min_length=1)
    deprecated: bool = False
    input_price_per_million_usd: float | None = None
    output_price_per_million_usd: float | None = None
    cache_input_price_per_million_usd: float | None = None
    max_input_tokens: int | None = Field(default=None, ge=0)
    max_output_tokens: int | None = Field(default=None, ge=0)
    reasoning_effort: list[str] | None = Field(
        default=None,
        description=(
            "OpenAI: API-documented reasoning effort levels when applicable. "
            "Anthropic: supported effort level list (same JSON key for registry storage)."
        ),
    )

    @field_validator("deprecated", mode="before")
    @classmethod
    def _deprecated_coerce(cls, v: object) -> object:
        """LLMs sometimes emit null; treat as unknown → not deprecated."""
        if v is None:
            return False
        return v

    @field_validator(
        "input_price_per_million_usd",
        "output_price_per_million_usd",
        "cache_input_price_per_million_usd",
        mode="before",
    )
    @classmethod
    def _non_negative_prices(cls, v: object) -> object:
        if v is None:
            return v
        if isinstance(v, int | float) and float(v) < 0:
            return None
        return v

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def _coerce_reasoning_effort(cls, v: object) -> object:
        return _normalize_reasoning_effort_list(v)


# =============================================================================
# Parse model output → overlay rows
# =============================================================================


def strip_markdown_fence(text: str) -> str:
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_first_json_array(text: str) -> list[Any]:
    """Parse the first top-level JSON array from model output (tolerates leading prose)."""
    cleaned = strip_markdown_fence(text)
    start = cleaned.find("[")
    if start < 0:
        msg = "Model output did not contain a JSON array"
        raise ValueError(msg)
    depth = 0
    in_string = False
    escape = False
    quote: str | None = None
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif quote is not None and ch == quote:
                in_string = False
                quote = None
            continue
        if ch in "\"'":
            in_string = True
            quote = ch
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                chunk = cleaned[start : i + 1]
                return cast(list[Any], json.loads(chunk))
    msg = "Unclosed JSON array in model output"
    raise ValueError(msg)


def _parse_overlay_rows(raw: list[Any]) -> list[DocOverlay]:
    out: list[DocOverlay] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        prov = row.get("provider")
        vid = row.get("vendor_model_id")
        if prov not in ("openai", "anthropic") or not isinstance(vid, str) or not vid.strip():
            continue
        out.append(
            DocOverlay.model_validate(
                {
                    **row,
                    "provider": Provider(prov),
                    "vendor_model_id": vid.strip(),
                }
            )
        )
    return out


# =============================================================================
# Verbose tracing (--verbose)
# =============================================================================


def _verbose_dump_json(title: str, payload: Any) -> None:
    """Print a large JSON blob to stderr in chunks (safe for terminals)."""
    try:
        raw = json.dumps(payload, indent=2, default=str)
    except (TypeError, ValueError):
        raw = repr(payload)
    tqdm.write(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    step = 24_000
    for i in range(0, len(raw), step):
        tqdm.write(raw[i : i + step])
    tqdm.write("=" * 72 + "\n")


def _verbose_trace_openai_response(resp: Any) -> None:
    """Human-readable walkthrough of Responses output (queries, URLs, assistant text)."""
    tqdm.write("\n" + "=" * 72)
    tqdm.write("OPENAI — web search + assistant (step-by-step)")
    tqdm.write("=" * 72)
    rid = getattr(resp, "id", None)
    st = getattr(resp, "status", None)
    mod = getattr(resp, "model", None)
    tqdm.write(f"response id={rid!r} status={st!r} model={mod!r}")
    use = getattr(resp, "usage", None)
    if use is not None and hasattr(use, "model_dump"):
        tqdm.write("usage:\n" + json.dumps(use.model_dump(mode="json"), indent=2, default=str))
    output = getattr(resp, "output", None) or []
    tqdm.write(f"output items: {len(output)}")
    for i, item in enumerate(output):
        typ = getattr(item, "type", type(item).__name__)
        tqdm.write(f"\n--- output[{i}] type={typ!r} ---")
        if typ == "web_search_call":
            wid = getattr(item, "id", None)
            wst = getattr(item, "status", None)
            tqdm.write(f"  web_search_call id={wid!r} status={wst!r}")
            action = getattr(item, "action", None)
            if action is not None:
                at = getattr(action, "type", None)
                tqdm.write(f"  action.type={at!r}")
                if at == "search":
                    for q in getattr(action, "queries", None) or []:
                        tqdm.write(f"  search query: {q!r}")
                    q1 = getattr(action, "query", None)
                    if q1:
                        tqdm.write(f"  search query (legacy field): {q1!r}")
                    srcs = getattr(action, "sources", None) or []
                    tqdm.write(f"  source URLs attached to action: {len(srcs)}")
                    for s in srcs[:40]:
                        tqdm.write(f"    - {getattr(s, 'url', '')}")
                    if len(srcs) > 40:
                        tqdm.write(f"    … and {len(srcs) - 40} more")
                elif at == "open_page":
                    tqdm.write(f"  open_page url={getattr(action, 'url', None)!r}")
                elif at == "find_in_page":
                    tqdm.write(
                        f"  find_in_page url={getattr(action, 'url', None)!r} "
                        f"pattern={getattr(action, 'pattern', None)!r}"
                    )
                else:
                    tqdm.write(
                        "  action (json):\n"
                        + json.dumps(action.model_dump(mode="json"), indent=2, default=str)[
                            :_JSON_PREVIEW_CHARS
                        ]
                    )
            if hasattr(item, "model_dump"):
                extra = item.model_dump(mode="json")
                for k, v in extra.items():
                    if k in {"id", "type", "action", "status"} or v is None:
                        continue
                    tqdm.write(f"  extra field {k!r} ({type(v).__name__}):")
                    tqdm.write(json.dumps(v, default=str, indent=2)[:_JSON_TRACE_BLOCK])
        elif typ == "message":
            tqdm.write(
                f"  assistant message status={getattr(item, 'status', None)!r} "
                f"phase={getattr(item, 'phase', None)!r}"
            )
            for j, c in enumerate(getattr(item, "content", None) or []):
                ct = getattr(c, "type", None)
                tqdm.write(f"  content[{j}] type={ct!r}")
                if ct == "output_text":
                    tx = getattr(c, "text", "") or ""
                    tqdm.write(f"    chars={len(tx)}")
                    cap = 4000
                    tail = "\n    … (truncated)" if len(tx) > cap else ""
                    tqdm.write("    text preview:\n" + tx[:cap] + tail)
                elif hasattr(c, "model_dump"):
                    tqdm.write(
                        json.dumps(c.model_dump(mode="json"), indent=2, default=str)[
                            :_JSON_TRACE_CONTENT
                        ]
                    )
        else:
            tqdm.write("  (unhandled item, JSON dump truncated)")
            if hasattr(item, "model_dump"):
                tqdm.write(
                    json.dumps(item.model_dump(mode="json"), indent=2, default=str)[
                        :_JSON_TRACE_BLOCK
                    ]
                )
    tqdm.write("=" * 72 + "\n")


def _verbose_trace_anthropic_message(message: Message) -> None:
    """Human-readable walkthrough of Messages content (tool use + search hits + text)."""
    tqdm.write("\n" + "=" * 72)
    tqdm.write("ANTHROPIC — web search + assistant (step-by-step)")
    tqdm.write("=" * 72)
    tqdm.write(
        f"message id={message.id!r} model={message.model!r} "
        f"stop_reason={message.stop_reason!r} role={message.role!r}"
    )
    tqdm.write(
        "usage:\n"
        + json.dumps(message.usage.model_dump(mode="json"), indent=2, default=str)
    )
    tqdm.write(f"content blocks: {len(message.content)}")
    for i, block in enumerate(message.content):
        bt = getattr(block, "type", "")
        tqdm.write(f"\n--- content[{i}] type={bt!r} ---")
        if isinstance(block, ServerToolUseBlock):
            tqdm.write(f"  server_tool_use name={block.name!r} id={block.id!r}")
            tqdm.write("  input:\n" + json.dumps(block.input, indent=2, default=str))
        elif isinstance(block, WebSearchToolResultBlock):
            tqdm.write(f"  web_search_tool_result tool_use_id={block.tool_use_id!r}")
            cont = block.content
            if isinstance(cont, list):
                tqdm.write(f"  search results: {len(cont)} page(s)")
                for j, hit in enumerate(cont):
                    tqdm.write(f"    [{j}] title: {hit.title!r}")
                    tqdm.write(f"        url:   {hit.url!r}")
                    if hit.page_age:
                        tqdm.write(f"        page_age: {hit.page_age!r}")
                    tqdm.write(
                        f"        encrypted_content: <{len(hit.encrypted_content)} chars opaque>"
                    )
            else:
                tqdm.write(f"  content (non-list): {json.dumps(cont, default=str)[:4000]}")
        elif isinstance(block, TextBlock):
            tqdm.write(f"  text chars={len(block.text)}")
            cap = 4000
            tail = "\n  … (truncated)" if len(block.text) > cap else ""
            tqdm.write("  preview:\n" + block.text[:cap] + tail)
        else:
            tqdm.write("  other block (truncated JSON):")
            tqdm.write(block.model_dump_json(indent=2)[:_JSON_PREVIEW_CHARS])
    tqdm.write("=" * 72 + "\n")


def _anthropic_message_text(message: Message) -> str:
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "".join(parts).strip()


def _parse_overlay_response_text(text: str) -> list[DocOverlay]:
    if not text:
        msg = "Empty model output from doc enrichment"
        raise ValueError(msg)
    try:
        arr = extract_first_json_array(text)
    except (json.JSONDecodeError, ValueError) as e:
        snippet = text[:800].replace("\n", " ")
        msg = f"Failed to parse doc enrichment JSON: {e}; output starts: {snippet!r}"
        raise ValueError(msg) from e
    return _parse_overlay_rows(arr)


# =============================================================================
# OpenAI Responses — shared guards & verbose logging
# =============================================================================


def _ensure_openai_response_ok(resp: Any) -> None:
    if resp.error is not None:
        msg = f"Responses API error: {resp.error}"
        raise ValueError(msg)
    status = getattr(resp, "status", None)
    if status is not None and status != "completed":
        msg = f"Responses API status not completed: {status!r}"
        raise ValueError(msg)


def _log_verbose_user_payload(
    *,
    title: str,
    system_note: str,
    user: str,
    verbose: bool,
) -> None:
    if not verbose:
        return
    tqdm.write("\n" + "=" * 72)
    tqdm.write(title)
    tqdm.write("=" * 72)
    tqdm.write(system_note)
    cap = _PREVIEW_CHARS
    tqdm.write(f"(user) message chars={len(user)} — body:\n{user[:cap]}")
    if len(user) > cap:
        tqdm.write(f"\n… user message truncated ({len(user) - cap} more chars)")
    tqdm.write("=" * 72 + "\n")


def _overlays_for_entry(
    entry: ModelRegistryEntry, overlays: list[DocOverlay]
) -> list[DocOverlay]:
    """Keep at most one overlay row matching this registry entry (ignore stray model output)."""
    key = (entry.provider.value, entry.vendor_model_id)
    matched = [o for o in overlays if (o.provider.value, o.vendor_model_id) == key]
    return matched[:1]


def _registry_outcome_description(overlays: list[DocOverlay]) -> str:
    """How ``apply_doc_overlays`` treats this row (progress logging)."""
    if not overlays:
        return "kept — no overlay returned for this id; row left unchanged"
    if overlays[0].deprecated:
        return "deleted — deprecated (doc overlay); row dropped"
    return "kept — overlay merged (not deprecated)"


# =============================================================================
# Cached excerpts (HTTP on disk via vendor_doc_cache)
# =============================================================================


def _openai_deprecations_excerpt(corpus: str, *, max_chars: int = 100_000) -> str:
    """Slice of cached OpenAI API deprecations page text for the web-enrich user message."""
    dep = bounded_source_section(
        corpus,
        _OPENAI_DEPRECATIONS_URL,
        max_chars=max_chars,
    )
    if dep.strip():
        return dep
    return corpus.strip()[:max_chars]


# =============================================================================
# Vendor LLM calls (one registry row each)
# =============================================================================


def _call_doc_enrich_one_openai(
    *,
    api_key: str,
    model: str,
    entry: ModelRegistryEntry,
    deprecations_excerpt: str,
    pricing_excerpt: str,
    verbose: bool = False,
) -> list[DocOverlay]:
    """OpenAI Responses: get_model_card + pricing/deprecations crawls (no hosted web_search)."""
    client = OpenAI(api_key=api_key, timeout=180.0)
    card_url = _openai_api_model_card_url(entry.vendor_model_id)
    card_md = get_model_card(card_url, verbose=verbose)
    dep_for_user = _openai_deprecations_user_excerpt(
        deprecations_excerpt,
        entry.vendor_model_id,
    )
    user = _single_model_user_message_openai_web(
        entry,
        dep_for_user,
        model_card_markdown=card_md,
        pricing_excerpt=pricing_excerpt,
    )
    _log_verbose_user_payload(
        title="OPENAI — registry enrich (openai_docs_crawl + Responses, no hosted web_search)",
        system_note="(system) instructions length=" + str(len(_OPENAI_REGISTRY_WEB_INSTRUCTIONS)),
        user=user,
        verbose=verbose,
    )

    create_kw: dict[str, Any] = {
        "model": model,
        "instructions": _OPENAI_REGISTRY_WEB_INSTRUCTIONS,
        "input": user,
        "max_output_tokens": 16_384,
        "reasoning": Reasoning(effort=_openai_responses_reasoning_effort(model)),
    }

    resp = client.responses.create(**create_kw)
    _ensure_openai_response_ok(resp)
    if verbose:
        _verbose_trace_openai_response(resp)
        dump = resp.model_dump(mode="json")
        _verbose_dump_json("OpenAI Responses API — full raw JSON (same response)", dump)
        out_txt = resp.output_text.strip()
        tqdm.write(
            f"[doc-enrich] OpenAI aggregated output_text length={len(out_txt)} chars\n"
        )
    parsed = _parse_overlay_response_text(resp.output_text.strip())
    return _overlays_for_entry(entry, parsed)


def _verbose_trace_openai_cached_response(resp: Any) -> None:
    tqdm.write("\n" + "=" * 72)
    tqdm.write("OPENAI — cached-doc response (no web_search)")
    tqdm.write("=" * 72)
    rid = getattr(resp, "id", None)
    st = getattr(resp, "status", None)
    mod = getattr(resp, "model", None)
    tqdm.write(f"response id={rid!r} status={st!r} model={mod!r}")
    use = getattr(resp, "usage", None)
    if use is not None and hasattr(use, "model_dump"):
        tqdm.write("usage:\n" + json.dumps(use.model_dump(mode="json"), indent=2, default=str))
    out_txt = (getattr(resp, "output_text", None) or "").strip()
    cap = 4000
    tail = "\n… (truncated)" if len(out_txt) > cap else ""
    tqdm.write(f"output_text chars={len(out_txt)} preview:{tail}\n{out_txt[:cap]}")
    tqdm.write("=" * 72 + "\n")


def _call_doc_enrich_one_openai_cached(
    *,
    api_key: str,
    model: str,
    entry: ModelRegistryEntry,
    excerpt: str,
    verbose: bool = False,
    instructions: str | None = None,
    user_message: str | None = None,
) -> list[DocOverlay]:
    """OpenAI Responses without tools—model reasons over cached vendor-doc excerpt only."""
    client = OpenAI(api_key=api_key, timeout=180.0)
    inst = instructions or cached_doc_enrichment_instructions(Provider.openai)
    user = user_message or _single_model_user_message_cached(entry, excerpt)
    _log_verbose_user_payload(
        title="OPENAI — cached docs request (instructions + user message)",
        system_note="(system) chars=" + str(len(inst)),
        user=user,
        verbose=verbose,
    )

    create_kw: dict[str, Any] = {
        "model": model,
        "instructions": inst,
        "input": user,
        "max_output_tokens": 16_384,
        "reasoning": Reasoning(effort=_openai_responses_reasoning_effort(model)),
    }
    resp = client.responses.create(**create_kw)
    _ensure_openai_response_ok(resp)
    if verbose:
        _verbose_trace_openai_cached_response(resp)
        dump = resp.model_dump(mode="json")
        _verbose_dump_json("OpenAI Responses API — full raw JSON (cached path)", dump)
        tqdm.write(
            f"[doc-enrich] OpenAI cached-path output_text length="
            f"{len(resp.output_text.strip())} chars\n"
        )
    parsed = _parse_overlay_response_text(resp.output_text.strip())
    return _overlays_for_entry(entry, parsed)


def _call_doc_enrich_one_anthropic_cached(
    *,
    api_key: str,
    model: str,
    entry: ModelRegistryEntry,
    excerpt: str,
    verbose: bool = False,
    instructions: str | None = None,
    user_message: str | None = None,
) -> list[DocOverlay]:
    """Anthropic Messages without web_search—model reasons over cached vendor-doc excerpt only."""
    client = Anthropic(api_key=api_key, timeout=180.0)
    inst = instructions or cached_doc_enrichment_instructions(Provider.anthropic)
    user = user_message or _single_model_user_message_cached(entry, excerpt)
    _log_verbose_user_payload(
        title="ANTHROPIC — cached docs request (system + user message)",
        system_note=f"(system) chars={len(inst)}",
        user=user,
        verbose=verbose,
    )

    message = client.messages.create(
        model=model,
        max_tokens=16_384,
        system=inst,
        messages=[{"role": "user", "content": user}],
    )
    text = _anthropic_message_text(message)
    if verbose:
        tqdm.write(
            f"[doc-enrich] Anthropic cached-path assistant text length={len(text)} chars\n"
        )
        dump = message.model_dump(mode="json")
        _verbose_dump_json("Anthropic Messages API — full raw JSON (cached path)", dump)
    parsed = _parse_overlay_response_text(text)
    return _overlays_for_entry(entry, parsed)


def _call_doc_enrich_one_anthropic(
    *,
    api_key: str,
    model: str,
    entry: ModelRegistryEntry,
    verbose: bool = False,
) -> list[DocOverlay]:
    """One Anthropic Messages call; web_search tool max_uses=1."""
    client = Anthropic(api_key=api_key, timeout=180.0)
    user = _single_model_user_message(entry)
    _log_verbose_user_payload(
        title="ANTHROPIC — request (system + user message sent to Messages API)",
        system_note=f"(system) chars={len(_LEGACY_WEB_SEARCH_INSTRUCTIONS)}",
        user=user,
        verbose=verbose,
    )

    message = client.messages.create(
        model=model,
        max_tokens=16_384,
        system=_LEGACY_WEB_SEARCH_INSTRUCTIONS,
        messages=[{"role": "user", "content": user}],
        tools=_anthropic_messages_web_search_tools(),
    )
    text = _anthropic_message_text(message)
    if verbose:
        _verbose_trace_anthropic_message(message)
        dump = message.model_dump(mode="json")
        _verbose_dump_json("Anthropic Messages API — full raw JSON (same message)", dump)
        tqdm.write(f"[doc-enrich] Anthropic assistant text length={len(text)} chars\n")
    parsed = _parse_overlay_response_text(text)
    return _overlays_for_entry(entry, parsed)


# =============================================================================
# Merge overlays → registry rows
# =============================================================================


def apply_doc_overlays(
    entries: list[ModelRegistryEntry],
    overlays: list[DocOverlay],
) -> list[ModelRegistryEntry]:
    """Merge overlays into entries and drop rows the overlay marks deprecated."""
    by_key: dict[tuple[str, str], DocOverlay] = {}
    for overlay in overlays:
        by_key[(overlay.provider.value, overlay.vendor_model_id)] = overlay

    out: list[ModelRegistryEntry] = []
    for e in entries:
        key = (e.provider.value, e.vendor_model_id)
        row = by_key.get(key)
        if row is not None and row.deprecated:
            continue
        if row is None:
            out.append(e)
            continue
        updates: dict[str, Any] = {}
        if row.input_price_per_million_usd is not None:
            updates["input_price_per_million_usd"] = float(row.input_price_per_million_usd)
        if row.output_price_per_million_usd is not None:
            updates["output_price_per_million_usd"] = float(row.output_price_per_million_usd)
        if row.cache_input_price_per_million_usd is not None:
            updates["cache_input_price_per_million_usd"] = float(
                row.cache_input_price_per_million_usd
            )
        if row.max_input_tokens is not None:
            updates["max_input_tokens"] = int(row.max_input_tokens)
        if row.max_output_tokens is not None:
            updates["max_output_tokens"] = int(row.max_output_tokens)
        if row.reasoning_effort is not None:
            updates["reasoning_effort"] = row.reasoning_effort
        out.append(e.model_copy(update=updates))
    return out


def _load_cached_vendor_corpora(
    providers_needed: set[Provider], *, cache_root: Path
) -> tuple[str, str]:
    """Fetch disk-cached plaintext for each provider present in this enrichment batch."""
    openai_corpus = ""
    anthropic_corpus = ""
    for prov in providers_needed:
        try:
            text = load_provider_doc_corpus(prov, cache_root=cache_root)
        except (OSError, RuntimeError, ValueError) as e:
            tqdm.write(f"[doc-enrich] cached doc fetch failed for {prov.value}: {e}")
            text = ""
        if prov == Provider.openai:
            openai_corpus = text
        else:
            anthropic_corpus = text
    return openai_corpus, anthropic_corpus


def _anthropic_cached_merge_inputs(
    entry: ModelRegistryEntry,
    anthropic_corpus: str,
    *,
    models_list_json: str,
) -> tuple[str, str, str]:
    """Doc excerpt, user message, and system instructions for Anthropic merge enrichment."""
    excerpt = (
        excerpt_for_cached_enrichment(
            anthropic_corpus,
            entry.vendor_model_id,
            provider=Provider.anthropic,
        )
        if anthropic_corpus
        else ""
    )
    user_message = _anthropic_registry_merge_user_message(
        entry, excerpt, models_list_json=models_list_json
    )
    instructions = cached_doc_enrichment_instructions(Provider.anthropic)
    return excerpt, user_message, instructions


def _doc_enrich_overlays_for_one(
    *,
    entry: ModelRegistryEntry,
    backend: DocEnrichBackend,
    idx: int,
    total: int,
    okey: str,
    akey: str,
    dep_block: str,
    pricing_block: str,
    openai_row_model: str,
    doc_llm_model: str,
    anthropic_corpus: str,
    anthropic_models_list_json: str,
    verbose: bool,
    log_each: bool,
) -> list[DocOverlay]:
    """Run the doc LLM path for a single registry row; returns 0 or 1 overlay rows."""
    vid = entry.vendor_model_id

    if entry.provider == Provider.openai:
        if not okey:
            if log_each:
                tqdm.write(
                    f"[doc-enrich] model {idx}/{total} openai/{vid!r} — "
                    "skip (no OPENAI_API_KEY for doc LLM)"
                )
            return []
        if log_each:
            tqdm.write(
                f"[doc-enrich] model {idx}/{total} openai/{vid!r}\n"
                f"  path: openai_docs_crawl + Responses; "
                f"pricing chars={len(pricing_block)} deprecations chars={len(dep_block)}"
            )
        return _call_doc_enrich_one_openai(
            api_key=okey,
            model=openai_row_model,
            entry=entry,
            deprecations_excerpt=dep_block,
            pricing_excerpt=pricing_block,
            verbose=verbose,
        )

    excerpt, user_message, instructions = _anthropic_cached_merge_inputs(
        entry,
        anthropic_corpus,
        models_list_json=anthropic_models_list_json,
    )
    if backend == "openai":
        if not okey:
            if log_each:
                tqdm.write(
                    f"[doc-enrich] model {idx}/{total} anthropic/{vid!r} — "
                    "skip (no OPENAI_API_KEY for merge LLM)"
                )
            return []
        if log_each:
            tqdm.write(
                f"[doc-enrich] model {idx}/{total} anthropic/{vid!r}\n"
                f"  path: OpenAI Responses (cached docs + models.list JSON); "
                f"models.list chars={len(anthropic_models_list_json)} excerpt chars={len(excerpt)}"
            )
        return _call_doc_enrich_one_openai_cached(
            api_key=okey,
            model=doc_llm_model,
            entry=entry,
            excerpt=excerpt,
            verbose=verbose,
            instructions=instructions,
            user_message=user_message,
        )

    if not akey:
        if log_each:
            tqdm.write(
                f"[doc-enrich] model {idx}/{total} anthropic/{vid!r} — "
                "skip (no ANTHROPIC_API_KEY for doc LLM)"
            )
        return []
    if log_each:
        tqdm.write(
            f"[doc-enrich] model {idx}/{total} anthropic/{vid!r}\n"
            f"  path: Anthropic Messages (cached + models.list JSON); "
            f"models.list chars={len(anthropic_models_list_json)} excerpt chars={len(excerpt)}"
        )
    return _call_doc_enrich_one_anthropic_cached(
        api_key=akey,
        model=doc_llm_model,
        entry=entry,
        excerpt=excerpt,
        verbose=verbose,
        instructions=instructions,
        user_message=user_message,
    )


# =============================================================================
# Orchestration
# =============================================================================


def enrich_models_via_vendor_docs(
    *,
    entries: list[ModelRegistryEntry],
    backend: DocEnrichBackend,
    api_key: str,
    model: str,
    show_progress: bool = False,
    verbose: bool = False,
    openai_vendor_api_key: str | None = None,
    anthropic_vendor_api_key: str | None = None,
    progress_callback: Callable[[list[ModelRegistryEntry]], None] | None = None,
) -> list[ModelRegistryEntry]:
    """Fill pricing and drop deprecated rows using vendor LLM APIs.

    **OpenAI** registry rows: **openai_docs_crawl** — ``get_model_card`` per model (URL uses
    **model_card_doc_id**), plus one **get_pricing_page** and one **get_deprecations_page** per
    enrichment batch, then OpenAI **Responses** (no hosted ``web_search``). If live deprecations
    crawl is unusable, falls back to the disk-cached deprecations excerpt when available.

    **Anthropic** rows: one live **``models.list()``** JSON array dump per batch (same shape as
    ``m.model_dump(mode="json")`` per list item), plus ``CURRENT_ROW_JSON``, plus cached
    ``platform.claude.com`` excerpts, via the doc LLM. When ``backend`` is ``openai``, merge runs on
    OpenAI Responses (no tools). When ``backend`` is ``anthropic``, on Anthropic Messages.

    Rows whose provider does not match an available vendor API key are left unchanged (no
    overlay).
    """
    processed: list[ModelRegistryEntry] = []
    n = len(entries)
    providers_needed = {e.provider for e in entries}
    cache_root = default_vendor_doc_cache_root()

    okey = (openai_vendor_api_key or (api_key if backend == "openai" else "") or "").strip()
    akey = (anthropic_vendor_api_key or (api_key if backend == "anthropic" else "") or "").strip()
    log_each = verbose or show_progress

    openai_corpus, anthropic_corpus = _load_cached_vendor_corpora(
        providers_needed, cache_root=cache_root
    )

    if openai_corpus and not corpus_is_usable(openai_corpus) and log_each:
        tqdm.write(
            f"[doc-enrich] OpenAI deprecations cache short ({len(openai_corpus)} chars); "
            "continuing with available text."
        )
    if anthropic_corpus and not corpus_is_usable(anthropic_corpus) and log_each:
        tqdm.write(
            f"[doc-enrich] Anthropic doc cache short ({len(anthropic_corpus)} chars); "
            "continuing with available text."
        )

    bar = tqdm(
        total=n,
        desc="Doc enrichment (vendor docs)",
        unit="model",
        disable=not show_progress or n == 0,
        leave=True,
    )
    if log_each:
        tqdm.write(
            f"[doc-enrich] disk cache root {cache_root!s} "
            f"(override DRONE_GRAPH_VENDOR_DOC_CACHE); backend={backend!r}."
        )

    dep_block = ""
    pricing_block = ""
    need_openai_doc_llm = Provider.openai in providers_needed and bool(okey)
    if need_openai_doc_llm:
        dep_live = get_deprecations_page(verbose=verbose)
        dep_stripped = dep_live.strip()
        use_dep_fallback = (
            len(dep_stripped) < 200
            or "crawl failed" in dep_live.lower()
            or "returned empty" in dep_live.lower()
        )
        if use_dep_fallback and openai_corpus:
            dep_block = _openai_deprecations_excerpt(openai_corpus)
        else:
            dep_block = dep_live
        pricing_block = get_pricing_page(verbose=verbose)
    elif openai_corpus:
        dep_block = _openai_deprecations_excerpt(openai_corpus)

    openai_row_model = model if backend == "openai" else DEFAULT_OPENAI_DOC_ENRICH_MODEL

    anthropic_models_list_json = ""
    if Provider.anthropic in providers_needed and akey:
        try:
            anthropic_models_list_json = fetch_anthropic_models_list_json_dump(akey)
        except Exception as exc:
            anthropic_models_list_json = f"(anthropic models.list failed: {exc})"
            if log_each:
                tqdm.write(f"[doc-enrich] Anthropic models.list() JSON dump failed: {exc}")

    for idx, entry in enumerate(entries, start=1):
        bar.set_postfix_str(entry.vendor_model_id[:24])
        bar.refresh()
        t0 = time.perf_counter()
        one = _doc_enrich_overlays_for_one(
            entry=entry,
            backend=backend,
            idx=idx,
            total=n,
            okey=okey,
            akey=akey,
            dep_block=dep_block,
            pricing_block=pricing_block,
            openai_row_model=openai_row_model,
            doc_llm_model=model,
            anthropic_corpus=anthropic_corpus,
            anthropic_models_list_json=anthropic_models_list_json,
            verbose=verbose,
            log_each=log_each,
        )

        merged_one = apply_doc_overlays([entry], one)
        processed.extend(merged_one)
        dt = time.perf_counter() - t0

        if progress_callback is not None:
            remaining = entries[idx:]
            progress_callback([*processed, *remaining])
        if log_each:
            outcome = _registry_outcome_description(one)
            tqdm.write(
                f"[doc-enrich] model {idx}/{n} {entry.provider.value}/"
                f"{entry.vendor_model_id!r} — finished in {dt:.1f}s; registry: {outcome}"
            )

        bar.set_postfix_str(f"ok {dt:.0f}s")
        bar.update(1)
    bar.close()
    return processed
