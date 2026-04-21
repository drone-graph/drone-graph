from __future__ import annotations

import json

import pytest

from drone_graph.gaps import Gap, ModelTier
from drone_graph.model_registry import ModelRegistry, ModelRegistryFile

_POPULATED = {
    "tier_defaults": {"cheap": "a", "standard": "b", "frontier": "c"},
    "models": [
        {
            "dgraph_model_id": "a",
            "provider": "openai",
            "vendor_model_id": "m1",
            "deprecated": False,
            "max_input_tokens": 1,
            "max_output_tokens": 1,
            "reasoning_effort": None,
            "input_price_per_million_usd": 0.15,
            "output_price_per_million_usd": 0.6,
            "cache_read_price_per_million_usd": None,
            "cache_write_price_per_million_usd": None,
            "capabilities": ["tools"],
            "rate_limits": {},
        },
        {
            "dgraph_model_id": "b",
            "provider": "openai",
            "vendor_model_id": "m2",
            "deprecated": False,
            "max_input_tokens": 1,
            "max_output_tokens": 1,
            "reasoning_effort": None,
            "input_price_per_million_usd": 0,
            "output_price_per_million_usd": 0,
            "cache_read_price_per_million_usd": None,
            "cache_write_price_per_million_usd": None,
            "capabilities": ["streaming"],
            "rate_limits": {},
        },
        {
            "dgraph_model_id": "c",
            "provider": "openai",
            "vendor_model_id": "m3",
            "deprecated": False,
            "max_input_tokens": 1,
            "max_output_tokens": 1,
            "reasoning_effort": None,
            "input_price_per_million_usd": 0,
            "output_price_per_million_usd": 0,
            "cache_read_price_per_million_usd": None,
            "cache_write_price_per_million_usd": None,
            "capabilities": ["vision"],
            "rate_limits": {},
        },
    ],
}


def _populated_registry() -> ModelRegistry:
    return ModelRegistry.from_json(json.dumps(_POPULATED))


def test_load_default_registry_is_bootstrap() -> None:
    reg = ModelRegistry.load_default()
    assert reg.is_populated is False
    assert reg.get("dgraph-openai-gpt-4o-mini") is None


def test_resolve_for_tier_fails_on_bootstrap() -> None:
    reg = ModelRegistry.load_default()
    with pytest.raises(ValueError, match="empty"):
        reg.resolve_for_tier(ModelTier.cheap)


@pytest.mark.parametrize(
    "tier",
    [ModelTier.cheap, ModelTier.standard, ModelTier.frontier],
)
def test_resolve_for_tier(tier: ModelTier) -> None:
    reg = _populated_registry()
    entry = reg.resolve_for_tier(tier)
    assert not entry.deprecated
    assert entry.vendor_model_id
    assert entry.capabilities


def test_resolve_for_gap() -> None:
    reg = _populated_registry()
    gap = Gap(description="x", model_tier=ModelTier.cheap)
    entry = reg.resolve_for_gap(gap)
    assert entry.dgraph_model_id == "a"


def test_estimate_cost_usd() -> None:
    reg = _populated_registry()
    entry = reg.require("a")
    # 1M input @ 0.15 + 500k output @ 0.6 = 0.15 + 0.3 = 0.45
    cost = reg.estimate_cost_usd(entry, input_tokens=1_000_000, output_tokens=500_000)
    assert abs(cost - 0.45) < 1e-9


def test_bootstrap_registry_file_validates() -> None:
    data = ModelRegistryFile.model_validate({"tier_defaults": {}, "models": []})
    assert not data.models
    ModelRegistry.from_json(data.model_dump_json())


def test_bootstrap_rejects_non_empty_tier_defaults() -> None:
    bad = {"tier_defaults": {"cheap": "a"}, "models": []}
    with pytest.raises(ValueError, match="Bootstrap state"):
        ModelRegistryFile.model_validate(bad)


def test_rejects_duplicate_dgraph_model_id() -> None:
    bad = {
        "tier_defaults": {"cheap": "a", "standard": "b", "frontier": "c"},
        "models": [
            {
                "dgraph_model_id": "a",
                "provider": "openai",
                "vendor_model_id": "m1",
                "deprecated": False,
                "max_input_tokens": 1,
                "max_output_tokens": 1,
                "reasoning_effort": None,
                "input_price_per_million_usd": 0,
                "output_price_per_million_usd": 0,
                "cache_read_price_per_million_usd": None,
                "cache_write_price_per_million_usd": None,
                "capabilities": [],
                "rate_limits": {},
            },
            {
                "dgraph_model_id": "a",
                "provider": "openai",
                "vendor_model_id": "m2",
                "deprecated": False,
                "max_input_tokens": 1,
                "max_output_tokens": 1,
                "reasoning_effort": None,
                "input_price_per_million_usd": 0,
                "output_price_per_million_usd": 0,
                "cache_read_price_per_million_usd": None,
                "cache_write_price_per_million_usd": None,
                "capabilities": [],
                "rate_limits": {},
            },
            {
                "dgraph_model_id": "b",
                "provider": "openai",
                "vendor_model_id": "m3",
                "deprecated": False,
                "max_input_tokens": 1,
                "max_output_tokens": 1,
                "reasoning_effort": None,
                "input_price_per_million_usd": 0,
                "output_price_per_million_usd": 0,
                "cache_read_price_per_million_usd": None,
                "cache_write_price_per_million_usd": None,
                "capabilities": [],
                "rate_limits": {},
            },
            {
                "dgraph_model_id": "c",
                "provider": "openai",
                "vendor_model_id": "m4",
                "deprecated": False,
                "max_input_tokens": 1,
                "max_output_tokens": 1,
                "reasoning_effort": None,
                "input_price_per_million_usd": 0,
                "output_price_per_million_usd": 0,
                "cache_read_price_per_million_usd": None,
                "cache_write_price_per_million_usd": None,
                "capabilities": [],
                "rate_limits": {},
            },
        ],
    }
    with pytest.raises(ValueError, match="Duplicate dgraph_model_id"):
        ModelRegistryFile.model_validate(bad)


def test_rejects_deprecated_tier_default() -> None:
    bad = {
        "tier_defaults": {
            "cheap": "a",
            "standard": "b",
            "frontier": "c",
        },
        "models": [
            {
                "dgraph_model_id": "a",
                "provider": "openai",
                "vendor_model_id": "m1",
                "deprecated": True,
                "max_input_tokens": 1,
                "max_output_tokens": 1,
                "reasoning_effort": None,
                "input_price_per_million_usd": 0,
                "output_price_per_million_usd": 0,
                "cache_read_price_per_million_usd": None,
                "cache_write_price_per_million_usd": None,
                "capabilities": [],
                "rate_limits": {},
            },
            {
                "dgraph_model_id": "b",
                "provider": "openai",
                "vendor_model_id": "m2",
                "deprecated": False,
                "max_input_tokens": 1,
                "max_output_tokens": 1,
                "reasoning_effort": None,
                "input_price_per_million_usd": 0,
                "output_price_per_million_usd": 0,
                "cache_read_price_per_million_usd": None,
                "cache_write_price_per_million_usd": None,
                "capabilities": [],
                "rate_limits": {},
            },
            {
                "dgraph_model_id": "c",
                "provider": "openai",
                "vendor_model_id": "m3",
                "deprecated": False,
                "max_input_tokens": 1,
                "max_output_tokens": 1,
                "reasoning_effort": None,
                "input_price_per_million_usd": 0,
                "output_price_per_million_usd": 0,
                "cache_read_price_per_million_usd": None,
                "cache_write_price_per_million_usd": None,
                "capabilities": [],
                "rate_limits": {},
            },
        ],
    }
    with pytest.raises(ValueError, match="deprecated"):
        ModelRegistryFile.model_validate(bad)


def test_json_roundtrip_tier_defaults_keys() -> None:
    raw = json.dumps(
        {
            "tier_defaults": {"cheap": "a", "standard": "b", "frontier": "c"},
            "models": [
                {
                    "dgraph_model_id": "a",
                    "provider": "openai",
                    "vendor_model_id": "m1",
                    "deprecated": False,
                    "max_input_tokens": 1,
                    "max_output_tokens": 1,
                    "reasoning_effort": None,
                    "input_price_per_million_usd": 0,
                    "output_price_per_million_usd": 0,
                    "cache_read_price_per_million_usd": None,
                    "cache_write_price_per_million_usd": None,
                    "capabilities": ["tools"],
                    "rate_limits": {"rpm": 1, "tpm": None},
                },
                {
                    "dgraph_model_id": "b",
                    "provider": "openai",
                    "vendor_model_id": "m2",
                    "deprecated": False,
                    "max_input_tokens": 1,
                    "max_output_tokens": 1,
                    "reasoning_effort": None,
                    "input_price_per_million_usd": 0,
                    "output_price_per_million_usd": 0,
                    "cache_read_price_per_million_usd": None,
                    "cache_write_price_per_million_usd": None,
                    "capabilities": [],
                    "rate_limits": {},
                },
                {
                    "dgraph_model_id": "c",
                    "provider": "openai",
                    "vendor_model_id": "m3",
                    "deprecated": False,
                    "max_input_tokens": 1,
                    "max_output_tokens": 1,
                    "reasoning_effort": None,
                    "input_price_per_million_usd": 0,
                    "output_price_per_million_usd": 0,
                    "cache_read_price_per_million_usd": None,
                    "cache_write_price_per_million_usd": None,
                    "capabilities": [],
                    "rate_limits": {},
                },
            ],
        }
    )
    ModelRegistryFile.model_validate_json(raw)
