from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, cast

from anthropic import Anthropic
from anthropic.types import ModelInfo
from openai import OpenAI
from tqdm import tqdm

from drone_graph.drones.providers import Provider
from drone_graph.gaps.records import ModelTier
from drone_graph.model_registry.doc_enrich import (
    DEFAULT_ANTHROPIC_DOC_ENRICH_MODEL,
    DEFAULT_OPENAI_DOC_ENRICH_MODEL,
    enrich_models_via_vendor_docs,
)
from drone_graph.model_registry.records import (
    ModelRegistryEntry,
    ModelRegistryFile,
    RateLimits,
)

GRAPH_ID_PREFIX = "dgraph"


def default_packaged_registry_json_path() -> Path:
    """Path to the packaged ``model_registry.json`` (alongside this module)."""
    return Path(__file__).resolve().parent / "model_registry.json"


def enrich_registry_models(
    entries: list[ModelRegistryEntry],
    *,
    show_progress: bool = False,
    verbose: bool = False,
    doc_enrich_web_search: bool | None = None,
    progress_callback: Callable[[list[ModelRegistryEntry]], None] | None = None,
) -> list[ModelRegistryEntry]:
    """Run vendor-doc enrichment on ``entries`` (uses vendor API keys from the environment).

    ``doc_enrich_web_search`` is accepted for API compatibility and ignored; routing is
    provider-specific inside ``doc_enrich`` (OpenAI: Crawl4AI model card + deprecations cache;
    Anthropic: API row + cached platform.claude.com docs).
    """
    _ = doc_enrich_web_search
    okey_stripped = (os.environ.get("OPENAI_API_KEY") or "").strip()
    akey_stripped = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if show_progress:
        tqdm.write("Enriching registry rows (OpenAI Crawl4AI + deprecations; Anthropic docs)…")
    if verbose:
        tqdm.write(
            "[doc-enrich] verbose logging: full request/response JSON "
            "(Anthropic web_search traces when that path is used)"
        )
    if okey_stripped and akey_stripped:
        if show_progress:
            tqdm.write(
                "Doc-enrich backend: openai "
                f"({DEFAULT_OPENAI_DOC_ENRICH_MODEL}; both vendor keys set)"
            )
        return enrich_models_via_vendor_docs(
            entries=list(entries),
            backend="openai",
            api_key=okey_stripped,
            model=DEFAULT_OPENAI_DOC_ENRICH_MODEL,
            show_progress=show_progress,
            verbose=verbose,
            openai_vendor_api_key=okey_stripped or None,
            anthropic_vendor_api_key=akey_stripped or None,
            progress_callback=progress_callback,
        )
    if akey_stripped:
        return enrich_models_via_vendor_docs(
            entries=list(entries),
            backend="anthropic",
            api_key=akey_stripped,
            model=DEFAULT_ANTHROPIC_DOC_ENRICH_MODEL,
            show_progress=show_progress,
            verbose=verbose,
            openai_vendor_api_key=okey_stripped or None,
            anthropic_vendor_api_key=akey_stripped or None,
            progress_callback=progress_callback,
        )
    if okey_stripped:
        return enrich_models_via_vendor_docs(
            entries=list(entries),
            backend="openai",
            api_key=okey_stripped,
            model=DEFAULT_OPENAI_DOC_ENRICH_MODEL,
            show_progress=show_progress,
            verbose=verbose,
            openai_vendor_api_key=okey_stripped or None,
            anthropic_vendor_api_key=None,
            progress_callback=progress_callback,
        )
    msg = "No API key available for doc enrichment. Set OPENAI_API_KEY and/or ANTHROPIC_API_KEY."
    raise ValueError(msg)


def update_registry_file(
    *,
    output: Path,
    show_progress: bool = False,
    verbose: bool = False,
    doc_enrich_web_search: bool | None = None,
) -> ModelRegistryFile:
    """Re-run doc enrichment on an existing registry file (no vendor list refetch)."""
    if not output.is_file():
        msg = f"Registry file not found: {output}. Run `drone-graph model-registry fresh` first."
        raise ValueError(msg)
    current = ModelRegistryFile.model_validate_json(output.read_text(encoding="utf-8"))
    if not current.models:
        msg = f"Registry has no models: {output}"
        raise ValueError(msg)
    enriched_models = enrich_registry_models(
        list(current.models),
        show_progress=show_progress,
        verbose=verbose,
        doc_enrich_web_search=doc_enrich_web_search,
        progress_callback=lambda models: _write_registry_snapshot(output, models),
    )
    data = finalize_registry(enriched_models)
    output.write_text(data.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return data


def sync_registry_file(
    *,
    output: Path,
    show_progress: bool = False,
    verbose: bool = False,
    doc_enrich_web_search: bool | None = None,
) -> ModelRegistryFile:
    """Merge newly listed vendor models into ``output``, then enrich all rows."""
    if not output.is_file():
        msg = f"Registry file not found: {output}. Run `drone-graph model-registry fresh` first."
        raise ValueError(msg)
    current = ModelRegistryFile.model_validate_json(output.read_text(encoding="utf-8"))
    existing_keys = {(m.provider, m.vendor_model_id) for m in current.models}
    merged: list[ModelRegistryEntry] = list(current.models)

    okey = os.environ.get("OPENAI_API_KEY")
    akey = os.environ.get("ANTHROPIC_API_KEY")
    openai_ids: list[str] | None = None
    anthropic_infos: list[ModelInfo] | None = None
    if show_progress:
        tqdm.write("Fetching model lists from vendor APIs (merge new ids only)…")
    if okey and okey.strip():
        openai_ids = fetch_openai_vendor_model_ids(okey.strip(), broad=True)
    if akey and akey.strip():
        anthropic_infos = fetch_anthropic_model_infos_with_details(akey.strip(), broad=True)

    if openai_ids is None and anthropic_infos is None:
        msg = "No API keys found. Set OPENAI_API_KEY and/or ANTHROPIC_API_KEY."
        raise ValueError(msg)

    added = 0
    for vid in openai_ids or []:
        key = (Provider.openai, vid)
        if key not in existing_keys:
            merged.append(_entry_for_openai(vid))
            existing_keys.add(key)
            added += 1
    for info in anthropic_infos or []:
        key = (Provider.anthropic, info.id)
        if key not in existing_keys:
            merged.append(_entry_for_anthropic(info))
            existing_keys.add(key)
            added += 1
    if show_progress:
        tqdm.write(f"Merged {added} new vendor model row(s); enriching {len(merged)} total…")

    enriched_models = enrich_registry_models(
        merged,
        show_progress=show_progress,
        verbose=verbose,
        doc_enrich_web_search=doc_enrich_web_search,
        progress_callback=lambda models: _write_registry_snapshot(output, models),
    )
    data = finalize_registry(enriched_models)
    output.write_text(data.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return data


_OPENAI_DEFAULT_MAX_INPUT = 128_000
_OPENAI_DEFAULT_MAX_OUTPUT = 16_384

_OPENAI_SKIP_SUBSTRINGS: tuple[str, ...] = (
    "embed",
    "embedding",
    "whisper",
    "tts",
    "dall-e",
    "moderation",
    "davinci",
    "curie",
    "babbage",
    "ada-",
    "text-embedding",
    "text-search",
    "text-similarity",
    "audio",
    "transcribe",
    "speech",
    "realtime",
    "omni-moderation",
)

# Vendor list has no deprecation flag; allowlist keeps current chat families only.
_OPENAI_CHAT_ID_PREFIXES: tuple[str, ...] = (
    "chatgpt-4o",
    "gpt-4.1",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-5",
    "o1",
    "o3",
    "o4",
)

_ANTHROPIC_LEGACY_SUBSTRINGS: tuple[str, ...] = (
    "claude-1",
    "claude-2",
    "claude-instant",
)


def slug_vendor_id(vendor_model_id: str) -> str:
    s = vendor_model_id.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "unknown"


def dgraph_model_id(provider: Provider, vendor_model_id: str) -> str:
    return f"{GRAPH_ID_PREFIX}-{provider.value}-{slug_vendor_id(vendor_model_id)}"


def is_current_openai_chat_model(model_id: str) -> bool:
    mid = model_id.lower()
    if mid.startswith("ft:"):
        return False
    if any(part in mid for part in _OPENAI_SKIP_SUBSTRINGS):
        return False
    return any(mid.startswith(p) for p in _OPENAI_CHAT_ID_PREFIXES)


def is_openai_api_chat_candidate_broad(model_id: str) -> bool:
    """Wider net for list-models + doc enrichment (still skips obvious non-chat families)."""
    mid = model_id.lower()
    if mid.startswith("ft:"):
        return False
    if any(part in mid for part in _OPENAI_SKIP_SUBSTRINGS):
        return False
    if mid.startswith("gpt-"):
        return True
    if mid.startswith(("o1", "o3", "o4")):
        return True
    return mid.startswith("chatgpt-")


def is_anthropic_list_model(info: ModelInfo, *, broad: bool) -> bool:
    if info.type != "model":
        return False
    if broad:
        return True
    low = info.id.lower()
    return not any(s in low for s in _ANTHROPIC_LEGACY_SUBSTRINGS)


def is_current_anthropic_model(info: ModelInfo) -> bool:
    return is_anthropic_list_model(info, broad=False)


def _merge_ordered_capability_tags(*groups: list[str]) -> list[str]:
    """Concatenate tag lists in order, dropping duplicates."""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for s in group:
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _openai_capabilities(vendor_model_id: str) -> list[str]:
    mid = vendor_model_id.lower()
    tools = ["tools"]
    feats: list[str] = ["streaming"]
    if "4o" in mid or "vision" in mid:
        feats.append("vision")
    if "json" in mid or "turbo" in mid or mid.startswith("gpt-4") or mid.startswith("gpt-5"):
        feats.append("json_mode")
    seen: set[str] = set()
    features: list[str] = []
    for c in feats:
        if c not in seen:
            seen.add(c)
            features.append(c)
    return _merge_ordered_capability_tags(tools, features)


def _anthropic_capabilities(info: ModelInfo) -> list[str]:
    mc = info.capabilities
    tools = ["tools"]
    feats: list[str] = ["streaming"]
    if mc is None:
        return _merge_ordered_capability_tags(tools, feats)
    if getattr(mc, "image_input", None):
        feats.append("vision")
    if getattr(mc, "pdf_input", None):
        feats.append("pdf_input")
    if getattr(mc, "structured_outputs", None):
        feats.append("json_mode")
    if getattr(mc, "thinking", None):
        feats.append("thinking")
    if getattr(mc, "code_execution", None):
        feats.append("code_execution")
    if getattr(mc, "citations", None):
        feats.append("citations")
    if getattr(mc, "batch", None):
        feats.append("batch")
    if getattr(mc, "context_management", None):
        feats.append("context_management")
    if getattr(mc, "effort", None):
        feats.append("effort")
    seen: set[str] = set()
    features: list[str] = []
    for c in feats:
        if c not in seen:
            seen.add(c)
            features.append(c)
    return _merge_ordered_capability_tags(tools, features)


def _anthropic_reasoning_effort_levels(info: ModelInfo) -> list[str] | None:
    mc = info.capabilities
    if mc is None:
        return None
    effort = getattr(mc, "effort", None)
    if effort is None:
        return None
    if hasattr(effort, "model_dump"):
        raw = cast(dict[str, Any], effort.model_dump(mode="json"))
    elif isinstance(effort, dict):
        raw = cast(dict[str, Any], effort)
    else:
        return None

    preferred_order = ("low", "medium", "high", "xhigh", "max")
    out: list[str] = []
    for key in preferred_order:
        node = raw.get(key)
        if isinstance(node, dict) and node.get("supported") is True:
            out.append(key)

    extras = sorted(
        k
        for k, v in raw.items()
        if k not in {"supported", *preferred_order}
        and isinstance(v, dict)
        and v.get("supported") is True
    )
    out.extend(extras)
    return out or None


def _iter_sync_pages(page: Any) -> Iterator[Any]:
    cur: Any = page
    while True:
        yield cur
        if not cur.has_next_page():
            break
        cur = cur.get_next_page()


def fetch_openai_vendor_model_ids(api_key: str, *, broad: bool = False) -> list[str]:
    client = OpenAI(api_key=api_key)
    ids: list[str] = []
    pick = is_openai_api_chat_candidate_broad if broad else is_current_openai_chat_model
    root = client.models.list()
    for pg in _iter_sync_pages(root):
        for m in pg.data:
            if pick(m.id):
                ids.append(m.id)
    ids = sorted(set(ids))
    return ids


def fetch_anthropic_model_infos(api_key: str, *, broad: bool = False) -> list[ModelInfo]:
    client = Anthropic(api_key=api_key)
    out: list[ModelInfo] = []
    root = client.models.list(limit=100)
    for pg in _iter_sync_pages(root):
        for m in pg.data:
            if is_anthropic_list_model(m, broad=broad):
                out.append(m)
    by_id = {m.id: m for m in out}
    return [by_id[k] for k in sorted(by_id)]


def fetch_anthropic_model_detail(api_key: str, model_id: str) -> ModelInfo | None:
    """Return ``models.retrieve`` payload when the SDK supports it (richer capabilities)."""
    client = Anthropic(api_key=api_key)
    retrieve = getattr(client.models, "retrieve", None)
    if retrieve is None:
        return None
    try:
        return cast(ModelInfo, retrieve(model_id))
    except Exception:
        return None


def fetch_anthropic_model_infos_with_details(
    api_key: str, *, broad: bool = False
) -> list[ModelInfo]:
    """List models, then merge each id with ``models.retrieve`` when available."""
    infos = fetch_anthropic_model_infos(api_key, broad=broad)
    merged: list[ModelInfo] = []
    for info in infos:
        detail = fetch_anthropic_model_detail(api_key, info.id)
        merged.append(detail if detail is not None else info)
    return merged


def _entry_for_openai(vendor_model_id: str) -> ModelRegistryEntry:
    gid = dgraph_model_id(Provider.openai, vendor_model_id)
    return ModelRegistryEntry(
        dgraph_model_id=gid,
        provider=Provider.openai,
        vendor_model_id=vendor_model_id,
        deprecated=False,
        max_input_tokens=_OPENAI_DEFAULT_MAX_INPUT,
        max_output_tokens=_OPENAI_DEFAULT_MAX_OUTPUT,
        reasoning_effort=None,
        input_price_per_million_usd=0.0,
        output_price_per_million_usd=0.0,
        cache_input_price_per_million_usd=None,
        capabilities=_openai_capabilities(vendor_model_id),
        rate_limits=RateLimits(),
    )


def _entry_for_anthropic(info: ModelInfo) -> ModelRegistryEntry:
    gid = dgraph_model_id(Provider.anthropic, info.id)
    max_in = int(info.max_input_tokens) if info.max_input_tokens is not None else 200_000
    if max_in == 0:
        max_in = 200_000
    max_out = int(info.max_tokens) if info.max_tokens is not None else 8192
    if max_out == 0:
        max_out = 8192
    effort_levels = _anthropic_reasoning_effort_levels(info)
    return ModelRegistryEntry(
        dgraph_model_id=gid,
        provider=Provider.anthropic,
        vendor_model_id=info.id,
        deprecated=False,
        max_input_tokens=max_in,
        max_output_tokens=max_out,
        reasoning_effort=effort_levels,
        input_price_per_million_usd=0.0,
        output_price_per_million_usd=0.0,
        cache_input_price_per_million_usd=None,
        capabilities=_anthropic_capabilities(info),
        rate_limits=RateLimits(),
    )


def _pick_first_vendor(
    candidates: Iterable[str],
    *,
    vendor_ids: set[str],
) -> str | None:
    for c in candidates:
        if c in vendor_ids:
            return c
    return None


def _anthropic_cheap_vendor(aset: set[str]) -> str | None:
    if not aset:
        return None
    named = _pick_first_vendor(
        (
            "claude-3-haiku-20240307",
            "claude-3-5-haiku-20241022",
            "claude-haiku-4-20250514",
        ),
        vendor_ids=aset,
    )
    if named is not None:
        return named
    for v in sorted(aset):
        if "haiku" in v.lower():
            return v
    return sorted(aset)[0]


def _anthropic_standard_vendor(aset: set[str], *, avoid: str | None) -> str | None:
    if not aset:
        return None
    preferred = (
        "claude-3-5-sonnet-20241022",
        "claude-3-5-sonnet-20240620",
        "claude-sonnet-4-20250514",
    )
    for p in preferred:
        if p in aset and p != avoid:
            return p
    for cand in sorted(aset):
        if "sonnet" in cand.lower() and cand != avoid:
            return cand
    for cand in sorted(aset):
        if cand != avoid:
            return cand
    return sorted(aset)[0]


def _anthropic_frontier_vendor(aset: set[str]) -> str | None:
    v = _pick_first_vendor(
        (
            "claude-opus-4-20250514",
            "claude-3-opus-20240229",
            "claude-sonnet-4-20250514",
            "claude-3-5-sonnet-20241022",
        ),
        vendor_ids=aset,
    )
    if v is not None:
        return v
    for vid in sorted(aset, reverse=True):
        low = vid.lower()
        if "opus" in low or "sonnet" in low:
            return vid
    return sorted(aset)[-1] if aset else None


_OPENAI_TIER_PREFERENCES: dict[ModelTier, tuple[str, ...]] = {
    ModelTier.nano: (
        "gpt-5-4-nano",
        "gpt-5-nano",
        "gpt-4.1-nano",
        "gpt-4o-mini",
    ),
    ModelTier.mini: (
        "gpt-5-4-mini",
        "gpt-5-mini",
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-4o",
    ),
    ModelTier.standard: (
        "gpt-5-4",
        "gpt-5",
        "gpt-4o",
        "gpt-4.1",
        "gpt-4-turbo",
        "gpt-4",
    ),
    ModelTier.advanced: (
        "gpt-5-4",
        "gpt-5-3-codex",
        "gpt-5",
        "o3",
        "o1",
        "gpt-4o",
    ),
    ModelTier.frontier: (
        "gpt-5-4-pro",
        "gpt-5-pro",
        "o1-pro",
        "gpt-5",
        "gpt-4o",
    ),
}

_ANTHROPIC_TIER_PREFERENCES: dict[ModelTier, tuple[str, ...]] = {
    ModelTier.nano: (
        "claude-haiku-4-5-20251001",
        "claude-3-5-haiku-20241022",
    ),
    ModelTier.mini: (
        "claude-haiku-4-5-20251001",
        "claude-3-5-haiku-20241022",
        "claude-3-5-sonnet-20241022",
    ),
    ModelTier.standard: (
        "claude-sonnet-4-6",
        "claude-sonnet-4-5-20250929",
        "claude-3-5-sonnet-20241022",
    ),
    ModelTier.advanced: (
        "claude-sonnet-4-6",
        "claude-opus-4-7",
        "claude-3-5-sonnet-20241022",
    ),
    ModelTier.frontier: (
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5-20251101",
        "claude-opus-4-1-20250805",
    ),
}


def _ladder_for(
    provider: Provider,
    vendor_ids: set[str],
    prefs: dict[ModelTier, tuple[str, ...]],
) -> dict[ModelTier, str] | None:
    """Pick one vendor_model_id per tier from a preference list, falling
    back to alphabetical-by-id when nothing matches. Returns ``None`` if
    the provider has no models at all."""
    if not vendor_ids:
        return None
    sorted_ids = sorted(vendor_ids)
    ladder: dict[ModelTier, str] = {}
    for tier, candidates in prefs.items():
        picked = _pick_first_vendor(candidates, vendor_ids=vendor_ids)
        if picked is None:
            # Last-resort fallback to keep the ladder complete — picks a
            # progressively-later id for higher tiers.
            tier_index = list(prefs.keys()).index(tier)
            picked = sorted_ids[min(tier_index, len(sorted_ids) - 1)]
        ladder[tier] = dgraph_model_id(provider, picked)
    return ladder


def select_tier_defaults(
    *,
    openai_vendor_ids: list[str],
    anthropic_vendor_ids: list[str],
) -> dict[Provider, dict[ModelTier, str]]:
    """Build a ``tier_defaults_by_provider`` map. Each provider with vendor
    ids gets a complete 5-tier ladder. Repetition across tiers is allowed
    when a provider doesn't have enough distinct models — operators can
    override any tier in Settings."""
    out: dict[Provider, dict[ModelTier, str]] = {}
    o_ladder = _ladder_for(Provider.openai, set(openai_vendor_ids), _OPENAI_TIER_PREFERENCES)
    if o_ladder is not None:
        out[Provider.openai] = o_ladder
    a_ladder = _ladder_for(
        Provider.anthropic, set(anthropic_vendor_ids), _ANTHROPIC_TIER_PREFERENCES
    )
    if a_ladder is not None:
        out[Provider.anthropic] = a_ladder
    if not out:
        raise ValueError(
            "Cannot derive tier_defaults_by_provider: no vendor models for "
            "either provider."
        )
    return out


def build_registry_file(
    *,
    openai_vendor_ids: list[str] | None,
    anthropic_infos: list[ModelInfo] | None,
    show_progress: bool = False,
) -> ModelRegistryFile:
    models: list[ModelRegistryEntry] = []
    o_ids: list[str] = sorted(openai_vendor_ids or [])
    a_infos: list[ModelInfo] = list(anthropic_infos or [])

    total_rows = len(o_ids) + len(a_infos)
    bar = tqdm(
        total=total_rows,
        desc="Registering models",
        unit="model",
        disable=not show_progress or total_rows == 0,
        leave=True,
    )
    for vid in o_ids:
        models.append(_entry_for_openai(vid))
        bar.update(1)
    for info in a_infos:
        models.append(_entry_for_anthropic(info))
        bar.update(1)
    bar.close()

    if not models:
        msg = "No models collected; set OPENAI_API_KEY and/or ANTHROPIC_API_KEY"
        raise ValueError(msg)

    tier_defaults = select_tier_defaults(
        openai_vendor_ids=o_ids,
        anthropic_vendor_ids=[i.id for i in a_infos],
    )
    return ModelRegistryFile(
        tier_defaults_by_provider=tier_defaults, models=models
    )


def finalize_registry(models: list[ModelRegistryEntry]) -> ModelRegistryFile:
    """Sort models and recompute the per-provider tier ladders (non-deprecated
    set only)."""
    if not models:
        msg = "No models remain after doc enrichment"
        raise ValueError(msg)
    models_sorted = sorted(models, key=lambda m: m.dgraph_model_id)
    o_ids = sorted({m.vendor_model_id for m in models_sorted if m.provider == Provider.openai})
    a_ids = sorted({m.vendor_model_id for m in models_sorted if m.provider == Provider.anthropic})
    tier_defaults = select_tier_defaults(openai_vendor_ids=o_ids, anthropic_vendor_ids=a_ids)
    return ModelRegistryFile(
        tier_defaults_by_provider=tier_defaults, models=models_sorted
    )


def _write_registry_snapshot(output: Path, models: list[ModelRegistryEntry]) -> None:
    """Persist an intermediate registry snapshot during model-by-model enrichment."""
    if not models:
        return
    data = finalize_registry(models)
    output.write_text(data.model_dump_json(indent=2) + "\n", encoding="utf-8")


def generate_registry_file(
    *,
    output: Path,
    show_progress: bool = False,
    verbose: bool = False,
    doc_enrich_web_search: bool | None = None,
) -> ModelRegistryFile:
    """Build registry JSON: vendor list APIs, then doc enrichment via direct LLM.

    **OpenAI rows:** Cached ``developers.openai.com`` deprecations plus **Crawl4AI** per model
    for the official API model card (no OpenAI hosted ``web_search``).

    **Anthropic rows:** Built from ``models.list`` + ``models.retrieve``; enrichment merges
    cached ``platform.claude.com`` overview and pricing (OpenAI ``gpt-5-mini`` when both keys
    exist, else Anthropic Haiku).

    **Temporary architecture:** enrichment lives in ``doc_enrich``. **Future:** a
    **Drone** with marketplace **skills** (see ``architecture-notes/model-registry.md``).
    """
    okey = os.environ.get("OPENAI_API_KEY")
    akey = os.environ.get("ANTHROPIC_API_KEY")

    openai_ids: list[str] | None = None
    anthropic_infos: list[ModelInfo] | None = None

    if show_progress:
        tqdm.write("Fetching model lists from vendor APIs…")
    if okey and okey.strip():
        openai_ids = fetch_openai_vendor_model_ids(okey.strip(), broad=True)
    if akey and akey.strip():
        anthropic_infos = fetch_anthropic_model_infos_with_details(akey.strip(), broad=True)

    if openai_ids is None and anthropic_infos is None:
        msg = "No API keys found. Set OPENAI_API_KEY and/or ANTHROPIC_API_KEY in the environment."
        raise ValueError(msg)

    data = build_registry_file(
        openai_vendor_ids=openai_ids,
        anthropic_infos=anthropic_infos,
        show_progress=show_progress,
    )

    enriched_models = enrich_registry_models(
        list(data.models),
        show_progress=show_progress,
        verbose=verbose,
        doc_enrich_web_search=doc_enrich_web_search,
        progress_callback=lambda models: _write_registry_snapshot(output, models),
    )
    data = finalize_registry(enriched_models)

    output.write_text(data.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return data
