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
    tier_defaults_by_provider: dict[Provider, dict[ModelTier, str]] = Field(
        default_factory=dict,
        description=(
            "Per-provider tier ladder. ``tier_defaults_by_provider[provider][tier]`` "
            "maps to a dgraph_model_id. Required when models[] is non-empty: at "
            "least one provider must have all three tiers covered (cheap, "
            "standard, frontier); other providers may be partial or absent."
        ),
    )
    models: list[ModelRegistryEntry]

    @model_validator(mode="after")
    def _validate_registry(self) -> ModelRegistryFile:
        if not self.models:
            if self.tier_defaults_by_provider:
                raise ValueError(
                    "Bootstrap state requires models[] empty and "
                    "tier_defaults_by_provider {}. Run "
                    "`drone-graph model-registry fresh` to populate."
                )
            return self

        ids = [m.dgraph_model_id for m in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate dgraph_model_id in models[]")
        by_id = {m.dgraph_model_id: m for m in self.models}

        expected_tiers = {
            ModelTier.nano,
            ModelTier.mini,
            ModelTier.standard,
            ModelTier.advanced,
            ModelTier.frontier,
        }
        complete_ladders = 0

        for provider, ladder in self.tier_defaults_by_provider.items():
            for tier, gid in ladder.items():
                if gid not in by_id:
                    raise ValueError(
                        f"tier_defaults_by_provider[{provider.value}][{tier.value}] "
                        f"points to unknown dgraph_model_id: {gid!r}"
                    )
                entry = by_id[gid]
                if entry.deprecated:
                    raise ValueError(
                        f"tier_defaults_by_provider[{provider.value}][{tier.value}] "
                        f"references deprecated model: {gid!r}"
                    )
                if entry.provider != provider:
                    raise ValueError(
                        f"tier_defaults_by_provider[{provider.value}][{tier.value}]"
                        f"={gid!r} but model has provider={entry.provider.value}"
                    )
            if set(ladder.keys()) == expected_tiers:
                complete_ladders += 1
            elif ladder:
                missing = expected_tiers - set(ladder.keys())
                raise ValueError(
                    f"tier_defaults_by_provider[{provider.value}] is partial "
                    f"(missing {missing}); each provider must either cover all "
                    "three tiers or be omitted entirely."
                )

        if complete_ladders == 0:
            raise ValueError(
                "tier_defaults_by_provider must define a complete "
                "nano/mini/standard/advanced/frontier ladder for at least "
                "one provider when models[] is non-empty."
            )

        return self

    def ladder(self, provider: Provider) -> dict[ModelTier, str]:
        """Return the tier ladder for ``provider``. Raises ``KeyError`` if the
        ladder is absent — callers should fall back to a provider that has
        one (see ``ModelRegistry.resolve_for_tier``)."""
        return self.tier_defaults_by_provider[provider]

    @property
    def providers_with_ladder(self) -> list[Provider]:
        return [
            p
            for p, ladder in self.tier_defaults_by_provider.items()
            if len(ladder) == 5
        ]
