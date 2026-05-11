from __future__ import annotations

import json
from typing import Any

import pytest

from drone_graph.drones.providers import Provider
from drone_graph.gaps import Gap, ModelTier
from drone_graph.model_registry import ModelRegistry, ModelRegistryEntry, ModelRegistryFile


# ---- ModelRegistryEntry capability coercion -------------------------------


def test_capabilities_coerce_legacy_flat_list() -> None:
    e = ModelRegistryEntry.model_validate(
        {
            "dgraph_model_id": "x",
            "provider": "openai",
            "vendor_model_id": "gpt-4o",
            "deprecated": False,
            "max_input_tokens": 1,
            "max_output_tokens": 1,
            "reasoning_effort": None,
            "input_price_per_million_usd": 0.0,
            "output_price_per_million_usd": 0.0,
            "cache_input_price_per_million_usd": None,
            "capabilities": ["tools", "streaming", "vision"],
            "rate_limits": {},
        }
    )
    assert e.capabilities == ["tools", "streaming", "vision"]


def test_capabilities_coerce_legacy_tools_features_dict() -> None:
    e = ModelRegistryEntry.model_validate(
        {
            "dgraph_model_id": "x",
            "provider": "anthropic",
            "vendor_model_id": "claude-x",
            "deprecated": False,
            "max_input_tokens": 1,
            "max_output_tokens": 1,
            "reasoning_effort": None,
            "input_price_per_million_usd": 0.0,
            "output_price_per_million_usd": 0.0,
            "cache_input_price_per_million_usd": None,
            "capabilities": {
                "tools": ["tools"],
                "features": ["streaming", "vision", "pdf_input"],
            },
            "rate_limits": {},
        }
    )
    assert e.capabilities == ["tools", "streaming", "vision", "pdf_input"]


# ---- Fixtures --------------------------------------------------------------


_TIERS = [
    ModelTier.nano,
    ModelTier.mini,
    ModelTier.standard,
    ModelTier.advanced,
    ModelTier.frontier,
]


def _openai_entry(letter: str, idx: int, **overrides: Any) -> dict[str, Any]:
    return {
        "dgraph_model_id": f"oa-{letter}",
        "provider": "openai",
        "vendor_model_id": f"openai-m{idx}",
        "deprecated": False,
        "max_input_tokens": 1,
        "max_output_tokens": 1,
        "reasoning_effort": None,
        "input_price_per_million_usd": 0.15 * (idx + 1),
        "output_price_per_million_usd": 0.6 * (idx + 1),
        "cache_input_price_per_million_usd": None,
        "capabilities": ["tools"],
        "rate_limits": {},
        **overrides,
    }


def _anthropic_entry(letter: str, idx: int, **overrides: Any) -> dict[str, Any]:
    return {
        "dgraph_model_id": f"an-{letter}",
        "provider": "anthropic",
        "vendor_model_id": f"claude-m{idx}",
        "deprecated": False,
        "max_input_tokens": 1,
        "max_output_tokens": 1,
        "reasoning_effort": None,
        "input_price_per_million_usd": 1.0 + idx,
        "output_price_per_million_usd": 5.0 + idx,
        "cache_input_price_per_million_usd": None,
        "capabilities": ["tools"],
        "rate_limits": {},
        **overrides,
    }


def _populated_dict() -> dict[str, Any]:
    """A complete-both-providers registry for resolution tests."""
    return {
        "tier_defaults_by_provider": {
            "openai": {
                "nano":     "oa-a",
                "mini":     "oa-b",
                "standard": "oa-c",
                "advanced": "oa-d",
                "frontier": "oa-e",
            },
            "anthropic": {
                "nano":     "an-a",
                "mini":     "an-b",
                "standard": "an-c",
                "advanced": "an-d",
                "frontier": "an-e",
            },
        },
        "models": [
            _openai_entry("a", 0),
            _openai_entry("b", 1),
            _openai_entry("c", 2),
            _openai_entry("d", 3),
            _openai_entry("e", 4),
            _anthropic_entry("a", 0),
            _anthropic_entry("b", 1),
            _anthropic_entry("c", 2),
            _anthropic_entry("d", 3),
            _anthropic_entry("e", 4),
        ],
    }


def _populated_registry() -> ModelRegistry:
    return ModelRegistry.from_json(json.dumps(_populated_dict()))


# ---- Empty / bootstrap ----------------------------------------------------


def test_empty_packaged_shape_rejects_resolve() -> None:
    """Bootstrap shape: no models → resolution fails for any (tier, provider)."""
    reg = ModelRegistry.from_json(
        json.dumps({"tier_defaults_by_provider": {}, "models": []})
    )
    assert reg.is_populated is False
    with pytest.raises(ValueError, match="empty"):
        reg.resolve_for_tier(ModelTier.nano, Provider.openai)


def test_load_default_registry_parses() -> None:
    reg = ModelRegistry.load_default()
    assert isinstance(reg, ModelRegistry)


def test_bootstrap_registry_file_validates() -> None:
    data = ModelRegistryFile.model_validate(
        {"tier_defaults_by_provider": {}, "models": []}
    )
    assert not data.models
    ModelRegistry.from_json(data.model_dump_json())


def test_bootstrap_rejects_non_empty_tier_defaults() -> None:
    bad = {
        "tier_defaults_by_provider": {"openai": {"standard": "a"}},
        "models": [],
    }
    with pytest.raises(ValueError, match="Bootstrap state"):
        ModelRegistryFile.model_validate(bad)


# ---- Resolution -----------------------------------------------------------


@pytest.mark.parametrize("tier", _TIERS)
@pytest.mark.parametrize("provider", [Provider.openai, Provider.anthropic])
def test_resolve_for_tier_within_provider(
    tier: ModelTier, provider: Provider
) -> None:
    reg = _populated_registry()
    entry = reg.resolve_for_tier(tier, provider)
    assert not entry.deprecated
    assert entry.provider == provider
    # ID prefix matches the provider shorthand used in the fixture.
    expected_prefix = "oa-" if provider is Provider.openai else "an-"
    assert entry.dgraph_model_id.startswith(expected_prefix)


def test_resolve_for_gap_within_provider() -> None:
    reg = _populated_registry()
    gap = Gap(intent="x", criteria="x", model_tier=ModelTier.nano)
    entry = reg.resolve_for_gap(gap, Provider.openai)
    assert entry.dgraph_model_id == "oa-a"


def test_resolve_for_gap_with_override_wins() -> None:
    """Operator-set overrides take precedence over the registry's defaults."""
    reg = _populated_registry()
    gap = Gap(intent="x", criteria="x", model_tier=ModelTier.standard)
    overrides = {"openai": {"standard": "oa-e"}}  # frontier model for std tier
    entry = reg.resolve_for_gap(gap, Provider.openai, overrides=overrides)
    assert entry.dgraph_model_id == "oa-e"


def test_resolve_falls_back_to_other_provider_when_unconfigured() -> None:
    """If the requested provider has no ladder, fall back to another that does."""
    data = _populated_dict()
    # Drop anthropic's ladder; ask for an anthropic tier.
    data["tier_defaults_by_provider"].pop("anthropic")
    reg = ModelRegistry.from_json(json.dumps(data))
    entry = reg.resolve_for_tier(ModelTier.standard, Provider.anthropic)
    assert entry.provider == Provider.openai


def test_resolve_disables_fallback_when_requested() -> None:
    data = _populated_dict()
    data["tier_defaults_by_provider"].pop("anthropic")
    reg = ModelRegistry.from_json(json.dumps(data))
    with pytest.raises(ValueError, match="No complete tier ladder"):
        reg.resolve_for_tier(
            ModelTier.standard, Provider.anthropic, allow_provider_fallback=False
        )


def test_resolve_ignores_override_pointing_at_deprecated_model() -> None:
    data = _populated_dict()
    # Mark oa-c (would-be standard) deprecated; the entry stays in models but
    # the override should be rejected and fall through to the registry default.
    for m in data["models"]:
        if m["dgraph_model_id"] == "oa-c":
            m["deprecated"] = True
    # Registry default for standard is oa-c — needs to point at a non-deprecated
    # model. Rewire defaults: standard → oa-b (mini-priced model).
    data["tier_defaults_by_provider"]["openai"]["standard"] = "oa-b"
    reg = ModelRegistry.from_json(json.dumps(data))
    gap = Gap(intent="x", criteria="x", model_tier=ModelTier.standard)
    entry = reg.resolve_for_gap(
        gap, Provider.openai, overrides={"openai": {"standard": "oa-c"}}
    )
    assert entry.dgraph_model_id == "oa-b"


# ---- Pricing --------------------------------------------------------------


def test_estimate_cost_usd() -> None:
    reg = _populated_registry()
    entry = reg.require("oa-a")
    # 1M input @ 0.15 + 500k output @ 0.6 = 0.15 + 0.3 = 0.45
    cost = reg.estimate_cost_usd(entry, input_tokens=1_000_000, output_tokens=500_000)
    assert abs(cost - 0.45) < 1e-9


# ---- Validation errors ----------------------------------------------------


def test_rejects_duplicate_dgraph_model_id() -> None:
    bad = _populated_dict()
    # Duplicate "oa-a"
    bad["models"][1]["dgraph_model_id"] = "oa-a"
    with pytest.raises(ValueError, match="Duplicate dgraph_model_id"):
        ModelRegistryFile.model_validate(bad)


def test_rejects_deprecated_tier_default() -> None:
    bad = _populated_dict()
    for m in bad["models"]:
        if m["dgraph_model_id"] == "oa-a":
            m["deprecated"] = True
    with pytest.raises(ValueError, match="deprecated"):
        ModelRegistryFile.model_validate(bad)


def test_rejects_partial_ladder() -> None:
    bad = _populated_dict()
    # Drop one tier from openai → partial ladder is invalid.
    del bad["tier_defaults_by_provider"]["openai"]["frontier"]
    with pytest.raises(ValueError, match="partial"):
        ModelRegistryFile.model_validate(bad)


def test_rejects_provider_mismatch_in_ladder() -> None:
    bad = _populated_dict()
    # Point openai/nano at an anthropic model id.
    bad["tier_defaults_by_provider"]["openai"]["nano"] = "an-a"
    with pytest.raises(ValueError, match="provider"):
        ModelRegistryFile.model_validate(bad)


def test_requires_at_least_one_complete_ladder() -> None:
    """When models[] is non-empty, at least one provider needs a full ladder."""
    bad = _populated_dict()
    bad["tier_defaults_by_provider"] = {}
    with pytest.raises(ValueError, match="at least one provider"):
        ModelRegistryFile.model_validate(bad)


def test_json_roundtrip() -> None:
    raw = json.dumps(_populated_dict())
    parsed = ModelRegistryFile.model_validate_json(raw)
    assert {p.value for p in parsed.providers_with_ladder} == {"openai", "anthropic"}
