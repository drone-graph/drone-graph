from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

from drone_graph.gaps.records import Gap, ModelTier
from drone_graph.model_registry.records import ModelRegistryEntry, ModelRegistryFile


class ModelRegistry:
    def __init__(self, data: ModelRegistryFile) -> None:
        self._data = data
        self._by_id: dict[str, ModelRegistryEntry] = {m.dgraph_model_id: m for m in data.models}

    @property
    def is_populated(self) -> bool:
        return bool(self._data.models)

    @classmethod
    def from_json(cls, raw: str | bytes) -> ModelRegistry:
        data = ModelRegistryFile.model_validate_json(raw)
        return cls(data)

    @classmethod
    def load_path(cls, path: Path) -> ModelRegistry:
        return cls.from_json(path.read_text(encoding="utf-8"))

    @classmethod
    def load_default(cls) -> ModelRegistry:
        pkg = resources.files("drone_graph.model_registry")
        raw = pkg.joinpath("model_registry.json").read_bytes()
        return cls.from_json(raw)

    @classmethod
    def load_auto(cls) -> ModelRegistry:
        override = os.environ.get("DRONE_GRAPH_MODEL_REGISTRY_PATH")
        if override:
            return cls.load_path(Path(override))
        return cls.load_default()

    def get(self, dgraph_model_id: str) -> ModelRegistryEntry | None:
        return self._by_id.get(dgraph_model_id)

    def require(self, dgraph_model_id: str) -> ModelRegistryEntry:
        m = self.get(dgraph_model_id)
        if m is None:
            msg = f"Unknown dgraph_model_id: {dgraph_model_id!r}"
            raise KeyError(msg)
        return m

    def resolve_for_tier(self, tier: ModelTier) -> ModelRegistryEntry:
        if not self._data.models:
            msg = (
                "Model registry is empty. Run `drone-graph generate-model-registry` "
                "(with OPENAI_API_KEY / ANTHROPIC_API_KEY in the environment), then set "
                "`DRONE_GRAPH_MODEL_REGISTRY_PATH` to the generated JSON, or merge "
                "models into your registry file."
            )
            raise ValueError(msg)
        gid = self._data.tier_defaults[tier]
        m = self.require(gid)
        if m.deprecated:
            msg = f"Resolved model {gid!r} is deprecated (check tier_defaults)"
            raise ValueError(msg)
        return m

    def resolve_for_gap(self, gap: Gap) -> ModelRegistryEntry:
        return self.resolve_for_tier(gap.model_tier)

    def estimate_cost_usd(
        self,
        entry: ModelRegistryEntry,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        scale = 1_000_000.0
        cost = (input_tokens / scale) * entry.input_price_per_million_usd + (
            output_tokens / scale
        ) * entry.output_price_per_million_usd
        if entry.cache_read_price_per_million_usd is not None:
            cost += (cache_read_tokens / scale) * entry.cache_read_price_per_million_usd
        if entry.cache_write_price_per_million_usd is not None:
            cost += (cache_write_tokens / scale) * entry.cache_write_price_per_million_usd
        return cost
