from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from drone_graph.drones.providers import Provider
from drone_graph.gaps.records import ModelTier


def normalize_capabilities_value(v: object) -> list[str]:
    """Coerce registry ``capabilities`` to a single ordered, deduped string list.

    Accepts:
    - ``list[str]`` (already flat)
    - legacy ``{"tools": [...], "features": [...]}`` (tools first, then features)
    """
    if v is None:
        return []
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
        return out
    if isinstance(v, dict):
        tools_raw = v.get("tools")
        feats_raw = v.get("features")
        tools = (
            [str(x).strip() for x in tools_raw if str(x).strip()]
            if isinstance(tools_raw, list)
            else []
        )
        feats = (
            [str(x).strip() for x in feats_raw if str(x).strip()]
            if isinstance(feats_raw, list)
            else []
        )
        merged = [*tools, *feats]
        seen: set[str] = set()
        out: list[str] = []
        for s in merged:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out
    return []


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
    reasoning_effort: list[str] | None = Field(
        default=None,
        description=(
            "Same JSON key for all vendors: array of supported effort levels for this model. "
            "OpenAI: API-documented Responses reasoning effort values when available. "
            "Anthropic: Claude API effort levels (e.g. low, medium, high, xhigh, max)."
        ),
    )
    input_price_per_million_usd: float = Field(..., ge=0.0)
    output_price_per_million_usd: float = Field(..., ge=0.0)
    cache_input_price_per_million_usd: float | None = Field(
        default=None,
        ge=0.0,
        description="USD per 1M cached input tokens (prompt cache hits); null if unused",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description=(
            "Single list of capability tags (tool surfaces such as ``tools`` plus "
            "``streaming``, ``vision``, ``json_mode``, Anthropic flags, etc.). "
            "Order: legacy ``tools`` entries first, then ``features``, then deduped."
        ),
    )
    rate_limits: RateLimits = Field(default_factory=RateLimits)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _capabilities_coerce(cls, v: object) -> object:
        return normalize_capabilities_value(v)

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def _coerce_reasoning_effort(cls, v: object) -> object:
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


class ModelRegistryFile(BaseModel):
    tier_defaults: dict[ModelTier, str] = Field(
        ...,
        description=(
            "Maps each ModelTier to dgraph_model_id when models[] is non-empty; "
            "must be {} when models[] is empty (bootstrap before model-registry fresh)."
        ),
    )
    models: list[ModelRegistryEntry]

    @model_validator(mode="after")
    def _validate_registry(self) -> ModelRegistryFile:
        if not self.models:
            if self.tier_defaults:
                raise ValueError(
                    "Bootstrap state requires models[] empty and tier_defaults {}. "
                    "Run `drone-graph model-registry fresh` to populate, then set "
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
