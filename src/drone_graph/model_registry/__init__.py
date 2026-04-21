"""Model registry: loaded JSON + resolution helpers.

Doc enrichment (`doc_enrich`) calls vendor LLMs over **cached** official docs by default
(optional per-model hosted web search via env or CLI).
Future: enrichment runs as a **Drone**; **web search** becomes a **Skill** from a
**skills marketplace**, not hard-coded API tool wiring in this package.
"""

from drone_graph.model_registry.records import ModelRegistryEntry, ModelRegistryFile, RateLimits
from drone_graph.model_registry.registry import ModelRegistry

__all__ = ["ModelRegistry", "ModelRegistryEntry", "ModelRegistryFile", "RateLimits"]
