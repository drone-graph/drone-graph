from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from drone_graph.drones.providers import Provider
from drone_graph.gaps.records import ModelTier


class RateLimits(BaseModel):
    rpm: int | None = Field(default=None, description="Requests per minute")
    tpm: int | None = Field(default=None, description="Tokens per minute")


class ModelRegistryEntry(BaseModel):
    dgraph_model_id: str = Field(
        ...,
        min_length=1,
        description="Stable id owned by this project",
    )
    provider: Provider
    vendor_model_id: str = Field(..., min_length=1)
    deprecated: bool = Field(
        ...,
        description="false = routable; true = retired but kept for history",
    )
    max_input_tokens: int = Field(..., ge=0)
    max_output_tokens: int = Field(..., ge=0)
    reasoning_effort: str | None = Field(
        default=None,
        description="Model-specific; null when not applicable",
    )
    input_price_per_million_usd: float = Field(..., ge=0.0)
    output_price_per_million_usd: float = Field(..., ge=0.0)
    cache_read_price_per_million_usd: float | None = Field(
        default=None,
        ge=0.0,
        description="USD per 1M cached-read tokens; null if unused",
    )
    cache_write_price_per_million_usd: float | None = Field(
        default=None,
        ge=0.0,
        description="USD per 1M cached-write tokens; null if unused",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Multi-valued capability flags (e.g. tools, vision, streaming)",
    )
    rate_limits: RateLimits = Field(default_factory=RateLimits)


class ModelRegistryFile(BaseModel):
    tier_defaults: dict[ModelTier, str] = Field(
        ...,
        description=(
            "Maps each ModelTier to dgraph_model_id when models[] is non-empty; "
            "must be {} when models[] is empty (bootstrap before generate-model-registry)."
        ),
    )
    models: list[ModelRegistryEntry]

    @model_validator(mode="after")
    def _validate_registry(self) -> ModelRegistryFile:
        if not self.models:
            if self.tier_defaults:
                raise ValueError(
                    "Bootstrap state requires models[] empty and tier_defaults {}. "
                    "Run `drone-graph generate-model-registry` to populate, then set "
                    "tier_defaults to dgraph_model_ids that exist in models[]."
                )
            return self

        ids = [m.dgraph_model_id for m in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate dgraph_model_id in models[]")

        by_id = {m.dgraph_model_id: m for m in self.models}
        for tier, gid in self.tier_defaults.items():
            if gid not in by_id:
                msg = f"tier_defaults[{tier!r}] points to unknown dgraph_model_id: {gid!r}"
                raise ValueError(msg)
            if by_id[gid].deprecated:
                msg = f"tier_defaults[{tier!r}] must not reference deprecated model: {gid!r}"
                raise ValueError(msg)

        expected = {ModelTier.cheap, ModelTier.standard, ModelTier.frontier}
        if set(self.tier_defaults.keys()) != expected:
            missing = expected - set(self.tier_defaults.keys())
            extra = set(self.tier_defaults.keys()) - expected
            raise ValueError(
                f"tier_defaults must exactly cover {expected}; missing={missing} extra={extra}"
            )

        return self
