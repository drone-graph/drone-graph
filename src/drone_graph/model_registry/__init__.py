"""Model registry: loaded JSON + resolution helpers.

Doc enrichment (`doc_enrich`) uses **Crawl4AI** for the official ``developers.openai.com``
model card, then **OpenAI Responses** (no hosted ``web_search``) per OpenAI model, plus a
cached deprecations page, and
**Anthropic Models API JSON + cached** ``platform.claude.com`` overview and pricing merged
via the doc LLM. When both vendor keys are set, the doc LLM runs on **OpenAI** (``gpt-5-mini``).
CLI: ``drone-graph model-registry fresh|update|sync`` (default JSON path is the packaged file).
Future: enrichment runs as a **Drone**; **web search** becomes a **Skill** from a
**skills marketplace**, not hard-coded API tool wiring in this package.
"""

from drone_graph.model_registry.records import (
    ModelRegistryEntry,
    ModelRegistryFile,
    RateLimits,
    normalize_capabilities_value,
)
from drone_graph.model_registry.registry import ModelRegistry

__all__ = [
    "ModelRegistry",
    "ModelRegistryEntry",
    "ModelRegistryFile",
    "RateLimits",
    "default_packaged_registry_json_path",
    "enrich_registry_models",
    "generate_registry_file",
    "normalize_capabilities_value",
    "sync_registry_file",
    "update_registry_file",
]


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    _generate_names = {
        "default_packaged_registry_json_path",
        "enrich_registry_models",
        "generate_registry_file",
        "sync_registry_file",
        "update_registry_file",
    }
    if name in _generate_names:
        from drone_graph.model_registry import generate  # noqa: I001

        return getattr(generate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
