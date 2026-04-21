"""Vendor doc enrichment for the model registry (temporary integration).

**Default path:** Fetches a small allowlist of official pricing/deprecation pages once
per provider (disk-cached, TTL), then calls the LLM **without** hosted web search—only
the cached excerpt for each model id (see ``vendor_doc_cache``).

**Fallback:** ``use_web_search=True`` (CLI ``--doc-enrich-web-search`` or env
``DRONE_GRAPH_DOC_ENRICH_WEB_SEARCH=1``) uses OpenAI Responses ``web_search`` or
Anthropic ``web_search_20250305`` as before—one hosted search per model.

**Future:** The reasoning model becomes a **Drone** on the substrate; **web search**
is a **Skill** installed from a **skills marketplace**, not a vendor-tool parameter
hard-coded here. Replace this module's direct API usage when the drone runtime and
marketplace are available.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from typing import Any, Literal, cast

from anthropic import Anthropic
from anthropic.types import Message, ServerToolUseBlock, TextBlock
from anthropic.types.web_search_tool_result_block import WebSearchToolResultBlock
from openai import OpenAI
from openai.types.shared import Reasoning
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tqdm import tqdm

from drone_graph.drones.providers import Provider
from drone_graph.model_registry.records import ModelRegistryEntry
from drone_graph.model_registry.vendor_doc_cache import (
    corpus_is_usable,
    default_vendor_doc_cache_root,
    excerpt_for_model_id,
    load_provider_doc_corpus,
)

DocEnrichBackend = Literal["openai", "anthropic"]

DEFAULT_OPENAI_DOC_ENRICH_MODEL = "gpt-5-mini"
DEFAULT_ANTHROPIC_DOC_ENRICH_MODEL = "claude-haiku-4-5"
DEFAULT_DOC_ENRICH_MODEL = DEFAULT_OPENAI_DOC_ENRICH_MODEL

# Anthropic-only: shape required by ``Messages.create(..., tools=[...])`` for the hosted
# ``web_search_20250305`` server tool. The OpenAI path does not use this—it passes
# ``tools=[{"type": "web_search"}]`` on ``responses.create`` instead (see
# ``_call_doc_enrich_one_openai``).
_ANTHROPIC_MESSAGES_WEB_SEARCH_TOOL: list[dict[str, Any]] = [
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 1},
]


def _anthropic_messages_web_search_tools() -> Iterable[Any]:
    """Argument for Anthropic ``Messages.create`` ``tools=`` (SDK typing is loose here)."""
    return cast(Iterable[Any], _ANTHROPIC_MESSAGES_WEB_SEARCH_TOOL)

_SINGLE_MODEL_DOC_INSTRUCTIONS = """
You enrich exactly ONE model row for a machine-readable registry.

Hard rules:
1. Use the web_search tool at most ONCE for this entire turn. One query, one round of results.
2. Reason in this order from that single search only:
   (a) Decide whether this vendor_model_id is deprecated for new API use (retired, replaced, or
       no longer offered). If yes: set deprecated to true; you may leave price fields null.
   (b) If not deprecated: fill input_price_per_million_usd and output_price_per_million_usd in
       USD per 1M tokens only when the same search results clearly state them; otherwise null.
       Prefer official vendor docs (platform.openai.com, docs.anthropic.com). Do not invent prices.
3. Do not chain multiple web searches, open_page crawls, or extra tool rounds. Keep it minimal.

Respond with ONLY a JSON array containing exactly ONE object (no markdown fences, no commentary),
with keys:
- provider: "openai" or "anthropic" (must match the user message)
- vendor_model_id: string (exact id from the user message)
- deprecated: boolean (required: true or false, never null)
- input_price_per_million_usd: number or null
- output_price_per_million_usd: number or null
- cache_read_price_per_million_usd: number or null
- cache_write_price_per_million_usd: number or null
- max_input_tokens: integer or null
- max_output_tokens: integer or null
- reasoning_effort: string or null
"""

_SINGLE_MODEL_CACHED_DOC_INSTRUCTIONS = """
You enrich exactly ONE model row for a machine-readable registry.

You are given EXCERPTS from official vendor documentation only (pricing and deprecations).
Do not use web search or browse the internet. Use only the text in the user message.

Hard rules:
1. Decide whether this vendor_model_id is deprecated for new API use (retired, replaced, or
   no longer offered) using only the provided excerpts. If yes: set deprecated to true;
   you may leave price fields null.
2. If not deprecated: fill input_price_per_million_usd and output_price_per_million_usd in
   USD per 1M tokens only when the excerpts clearly state them for this exact id (or an
   unambiguous alias listed next to the same prices); otherwise null. Do not invent prices.
3. If the excerpts do not mention this model id at all, set deprecated to false unless the
   excerpts explicitly deprecate it; leave prices null.

Respond with ONLY a JSON array containing exactly ONE object (no markdown fences, no commentary),
with keys:
- provider: "openai" or "anthropic" (must match the user message)
- vendor_model_id: string (exact id from the user message)
- deprecated: boolean (required: true or false, never null)
- input_price_per_million_usd: number or null
- output_price_per_million_usd: number or null
- cache_read_price_per_million_usd: number or null
- cache_write_price_per_million_usd: number or null
- max_input_tokens: integer or null
- max_output_tokens: integer or null
- reasoning_effort: string or null
"""


def planned_search_query(entry: ModelRegistryEntry) -> str:
    """Single focused query string we log and suggest to the model (one web_search per model)."""
    vid = entry.vendor_model_id
    if entry.provider == Provider.openai:
        return f"{vid} OpenAI API model deprecated pricing site:platform.openai.com"
    return f"{vid} Anthropic Claude API model deprecated pricing site:docs.anthropic.com"


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


class DocOverlay(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Provider
    vendor_model_id: str = Field(..., min_length=1)
    deprecated: bool = False
    input_price_per_million_usd: float | None = None
    output_price_per_million_usd: float | None = None
    cache_read_price_per_million_usd: float | None = None
    cache_write_price_per_million_usd: float | None = None
    max_input_tokens: int | None = Field(default=None, ge=0)
    max_output_tokens: int | None = Field(default=None, ge=0)
    reasoning_effort: str | None = None

    @field_validator("deprecated", mode="before")
    @classmethod
    def _deprecated_coerce(cls, v: object) -> object:
        """LLMs sometimes emit null; treat as unknown → not deprecated."""
        if v is None:
            return False
        return v

    @field_validator("input_price_per_million_usd", "output_price_per_million_usd", mode="before")
    @classmethod
    def _non_negative_prices(cls, v: object) -> object:
        if v is None:
            return v
        if isinstance(v, int | float) and float(v) < 0:
            return None
        return v


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
                        + json.dumps(action.model_dump(mode="json"), indent=2, default=str)[:8000]
                    )
            if hasattr(item, "model_dump"):
                extra = item.model_dump(mode="json")
                for k, v in extra.items():
                    if k in {"id", "type", "action", "status"} or v is None:
                        continue
                    tqdm.write(f"  extra field {k!r} ({type(v).__name__}):")
                    tqdm.write(json.dumps(v, default=str, indent=2)[:12000])
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
                    tqdm.write(json.dumps(c.model_dump(mode="json"), indent=2, default=str)[:6000])
        else:
            tqdm.write("  (unhandled item, JSON dump truncated)")
            if hasattr(item, "model_dump"):
                tqdm.write(json.dumps(item.model_dump(mode="json"), indent=2, default=str)[:12000])
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
            tqdm.write(block.model_dump_json(indent=2)[:8000])
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


def _overlays_for_entry(
    entry: ModelRegistryEntry, overlays: list[DocOverlay]
) -> list[DocOverlay]:
    """Keep at most one overlay row matching this registry entry (ignore stray model output)."""
    key = (entry.provider.value, entry.vendor_model_id)
    matched = [o for o in overlays if (o.provider.value, o.vendor_model_id) == key]
    return matched[:1]


def _call_doc_enrich_one_openai(
    *,
    api_key: str,
    model: str,
    entry: ModelRegistryEntry,
    verbose: bool = False,
) -> list[DocOverlay]:
    """One OpenAI Responses call with at most one web_search (max_tool_calls=1)."""
    client = OpenAI(api_key=api_key, timeout=180.0)
    user = _single_model_user_message(entry)
    if verbose:
        tqdm.write("\n" + "=" * 72)
        tqdm.write("OPENAI — request (instructions + user message sent to Responses API)")
        tqdm.write("=" * 72)
        tqdm.write(
            "(system) instructions length=" + str(len(_SINGLE_MODEL_DOC_INSTRUCTIONS))
        )
        cap = 16_000
        tqdm.write(f"(user) message chars={len(user)} — body:\n{user[:cap]}")
        if len(user) > cap:
            tqdm.write(f"\n… user message truncated ({len(user) - cap} more chars)")
        tqdm.write("=" * 72 + "\n")

    create_kw: dict[str, Any] = {
        "model": model,
        "instructions": _SINGLE_MODEL_DOC_INSTRUCTIONS,
        "input": user,
        "tools": [{"type": "web_search"}],
        "max_tool_calls": 1,
        "max_output_tokens": 16_384,
        # Explicit medium reasoning for registry enrichment (quality vs latency tradeoff).
        "reasoning": Reasoning(effort="medium"),
    }
    if verbose:
        create_kw["include"] = [
            "web_search_call.results",
            "web_search_call.action.sources",
        ]

    resp = client.responses.create(**create_kw)
    if resp.error is not None:
        msg = f"Responses API error: {resp.error}"
        raise ValueError(msg)
    status = getattr(resp, "status", None)
    if status is not None and status != "completed":
        msg = f"Responses API status not completed: {status!r}"
        raise ValueError(msg)
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
) -> list[DocOverlay]:
    """OpenAI Responses without tools—model reasons over cached vendor-doc excerpt only."""
    client = OpenAI(api_key=api_key, timeout=180.0)
    user = _single_model_user_message_cached(entry, excerpt)
    if verbose:
        tqdm.write("\n" + "=" * 72)
        tqdm.write("OPENAI — cached docs request (instructions + user message)")
        tqdm.write("=" * 72)
        tqdm.write(
            "(system) chars=" + str(len(_SINGLE_MODEL_CACHED_DOC_INSTRUCTIONS))
        )
        cap = 16_000
        tqdm.write(f"(user) message chars={len(user)} — body:\n{user[:cap]}")
        if len(user) > cap:
            tqdm.write(f"\n… user message truncated ({len(user) - cap} more chars)")
        tqdm.write("=" * 72 + "\n")

    create_kw: dict[str, Any] = {
        "model": model,
        "instructions": _SINGLE_MODEL_CACHED_DOC_INSTRUCTIONS,
        "input": user,
        "max_output_tokens": 16_384,
        "reasoning": Reasoning(effort="medium"),
    }
    resp = client.responses.create(**create_kw)
    if resp.error is not None:
        msg = f"Responses API error: {resp.error}"
        raise ValueError(msg)
    status = getattr(resp, "status", None)
    if status is not None and status != "completed":
        msg = f"Responses API status not completed: {status!r}"
        raise ValueError(msg)
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
) -> list[DocOverlay]:
    """Anthropic Messages without web_search—model reasons over cached vendor-doc excerpt only."""
    client = Anthropic(api_key=api_key, timeout=180.0)
    user = _single_model_user_message_cached(entry, excerpt)
    if verbose:
        tqdm.write("\n" + "=" * 72)
        tqdm.write("ANTHROPIC — cached docs request (system + user message)")
        tqdm.write("=" * 72)
        tqdm.write(f"(system) chars={len(_SINGLE_MODEL_CACHED_DOC_INSTRUCTIONS)}")
        cap = 16_000
        tqdm.write(f"(user) chars={len(user)} — body:\n{user[:cap]}")
        if len(user) > cap:
            tqdm.write(f"\n… user message truncated ({len(user) - cap} more chars)")
        tqdm.write("=" * 72 + "\n")

    message = client.messages.create(
        model=model,
        max_tokens=16_384,
        system=_SINGLE_MODEL_CACHED_DOC_INSTRUCTIONS,
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
    if verbose:
        tqdm.write("\n" + "=" * 72)
        tqdm.write("ANTHROPIC — request (system + user message sent to Messages API)")
        tqdm.write("=" * 72)
        tqdm.write(f"(system) chars={len(_SINGLE_MODEL_DOC_INSTRUCTIONS)}")
        cap = 16_000
        tqdm.write(f"(user) chars={len(user)} — body:\n{user[:cap]}")
        if len(user) > cap:
            tqdm.write(f"\n… user message truncated ({len(user) - cap} more chars)")
        tqdm.write("=" * 72 + "\n")

    message = client.messages.create(
        model=model,
        max_tokens=16_384,
        system=_SINGLE_MODEL_DOC_INSTRUCTIONS,
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
        if row.cache_read_price_per_million_usd is not None:
            cr = float(row.cache_read_price_per_million_usd)
            updates["cache_read_price_per_million_usd"] = cr
        if row.cache_write_price_per_million_usd is not None:
            cw = float(row.cache_write_price_per_million_usd)
            updates["cache_write_price_per_million_usd"] = cw
        if row.max_input_tokens is not None:
            updates["max_input_tokens"] = int(row.max_input_tokens)
        if row.max_output_tokens is not None:
            updates["max_output_tokens"] = int(row.max_output_tokens)
        if row.reasoning_effort is not None:
            updates["reasoning_effort"] = row.reasoning_effort
        out.append(e.model_copy(update=updates))
    return out


def enrich_models_via_vendor_docs(
    *,
    entries: list[ModelRegistryEntry],
    backend: DocEnrichBackend,
    api_key: str,
    model: str,
    show_progress: bool = False,
    verbose: bool = False,
    use_web_search: bool | None = None,
) -> list[ModelRegistryEntry]:
    """Fill pricing and drop deprecated rows using vendor LLM APIs (direct).

    By default loads official pricing/deprecation pages once per **provider** (see
    ``vendor_doc_cache``), caches them on disk with a TTL, and sends a **bounded excerpt**
    per model—**no** hosted web search per row.

    Set ``use_web_search=True`` (or env ``DRONE_GRAPH_DOC_ENRICH_WEB_SEARCH=1``) for the
    legacy path: one hosted web search per model (OpenAI ``max_tool_calls=1``; Anthropic
    ``max_uses: 1``).

    Stand-in until orchestration runs this as a **Drone** with **web search** as a
    **skills marketplace** capability instead of built-in vendor tools here.
    """
    if use_web_search is None:
        use_web_search = os.environ.get(
            "DRONE_GRAPH_DOC_ENRICH_WEB_SEARCH", ""
        ).strip().lower() in ("1", "true", "yes", "on")

    all_overlays: list[DocOverlay] = []
    n = len(entries)
    providers_needed = {e.provider for e in entries}
    corpora: dict[Provider, str] = {}
    cache_root = default_vendor_doc_cache_root()

    if not use_web_search:
        for prov in providers_needed:
            try:
                corpora[prov] = load_provider_doc_corpus(prov, cache_root=cache_root)
            except (OSError, RuntimeError, ValueError) as e:
                tqdm.write(
                    f"[doc-enrich] cached doc fetch failed for {prov.value}: {e}; "
                    "falling back to per-model web search."
                )
                use_web_search = True
                corpora.clear()
                break
            if not corpus_is_usable(corpora[prov]):
                tqdm.write(
                    f"[doc-enrich] cached corpus for {prov.value} too short "
                    f"({len(corpora[prov])} chars); falling back to per-model web search."
                )
                use_web_search = True
                corpora.clear()
                break

    bar_desc = (
        "Doc enrichment (web search / model)"
        if use_web_search
        else "Doc enrichment (cached vendor docs)"
    )
    bar = tqdm(
        total=n,
        desc=bar_desc,
        unit="model",
        disable=not show_progress or n == 0,
        leave=True,
    )
    log_each = verbose or show_progress
    if log_each and not use_web_search:
        tqdm.write(
            f"[doc-enrich] using disk cache root {cache_root!s} "
            f"(override DRONE_GRAPH_VENDOR_DOC_CACHE); web_search disabled per model."
        )

    for idx, entry in enumerate(entries, start=1):
        if log_each:
            if use_web_search:
                q = planned_search_query(entry)
                tqdm.write(
                    f"[doc-enrich] model {idx}/{n} ({backend}) "
                    f"{entry.provider.value}/{entry.vendor_model_id!r}\n"
                    f"  web_search focus: {q}"
                )
            else:
                ex = excerpt_for_model_id(
                    corpora[entry.provider],
                    entry.vendor_model_id,
                )
                tqdm.write(
                    f"[doc-enrich] model {idx}/{n} ({backend}) "
                    f"{entry.provider.value}/{entry.vendor_model_id!r}\n"
                    f"  cached excerpt chars={len(ex)}"
                )
        bar.set_postfix_str(entry.vendor_model_id[:24])
        bar.refresh()
        t0 = time.perf_counter()
        if use_web_search:
            if backend == "openai":
                one = _call_doc_enrich_one_openai(
                    api_key=api_key,
                    model=model,
                    entry=entry,
                    verbose=verbose,
                )
            else:
                one = _call_doc_enrich_one_anthropic(
                    api_key=api_key,
                    model=model,
                    entry=entry,
                    verbose=verbose,
                )
        else:
            excerpt = excerpt_for_model_id(
                corpora[entry.provider],
                entry.vendor_model_id,
            )
            if backend == "openai":
                one = _call_doc_enrich_one_openai_cached(
                    api_key=api_key,
                    model=model,
                    entry=entry,
                    excerpt=excerpt,
                    verbose=verbose,
                )
            else:
                one = _call_doc_enrich_one_anthropic_cached(
                    api_key=api_key,
                    model=model,
                    entry=entry,
                    excerpt=excerpt,
                    verbose=verbose,
                )
        dt = time.perf_counter() - t0
        all_overlays.extend(one)
        if log_each:
            tqdm.write(
                f"[doc-enrich] model {idx}/{n} finished in {dt:.1f}s "
                f"({len(one)} overlay row(s))"
            )
        bar.set_postfix_str(f"ok {dt:.0f}s")
        bar.update(1)
    bar.close()
    return apply_doc_overlays(entries, all_overlays)
