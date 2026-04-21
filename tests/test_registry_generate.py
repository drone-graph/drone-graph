from __future__ import annotations

from anthropic.types import Message, ModelInfo, TextBlock, Usage

from drone_graph.drones.providers import Provider
from drone_graph.gaps.records import ModelTier
from drone_graph.model_registry.doc_enrich import (
    DocOverlay,
    _anthropic_message_text,
    apply_doc_overlays,
    extract_first_json_array,
)
from drone_graph.model_registry.generate import (
    build_registry_file,
    dgraph_model_id,
    finalize_registry,
    is_anthropic_list_model,
    is_current_anthropic_model,
    is_current_openai_chat_model,
    is_openai_api_chat_candidate_broad,
    select_tier_defaults,
    slug_vendor_id,
)
from drone_graph.model_registry.records import ModelRegistryFile


def test_slug_vendor_id() -> None:
    assert slug_vendor_id("gpt-4o-mini") == "gpt-4o-mini"
    assert slug_vendor_id("Claude 3.5 Sonnet") == "claude-3-5-sonnet"


def test_dgraph_model_id_format() -> None:
    assert dgraph_model_id(Provider.openai, "gpt-4o-mini") == "dgraph-openai-gpt-4o-mini"


def test_openai_skips_legacy_chat_ids() -> None:
    assert not is_current_openai_chat_model("gpt-3.5-turbo")
    assert not is_current_openai_chat_model("gpt-4-0613")
    assert not is_current_openai_chat_model("gpt-4")
    assert is_current_openai_chat_model("gpt-4o-mini")
    assert is_current_openai_chat_model("gpt-4-turbo-2024-04-09")
    assert is_current_openai_chat_model("o1-preview")


def test_openai_broad_includes_gpt35() -> None:
    assert is_openai_api_chat_candidate_broad("gpt-3.5-turbo")
    assert not is_openai_api_chat_candidate_broad("text-embedding-3-small")


def test_anthropic_broad_includes_legacy_ids() -> None:
    legacy = ModelInfo.model_construct(
        id="claude-2.1",
        created_at="2020-01-01T00:00:00Z",
        display_name="Claude 2",
        type="model",
    )
    assert not is_anthropic_list_model(legacy, broad=False)
    assert is_anthropic_list_model(legacy, broad=True)


def test_anthropic_skips_legacy_ids() -> None:
    legacy = ModelInfo.model_construct(
        id="claude-2.1",
        created_at="2020-01-01T00:00:00Z",
        display_name="Claude 2",
        type="model",
    )
    assert not is_current_anthropic_model(legacy)
    assert is_current_anthropic_model(
        ModelInfo.model_construct(
            id="claude-3-5-sonnet-20241022",
            created_at="2020-01-01T00:00:00Z",
            display_name="Sonnet",
            type="model",
        )
    )


def test_select_tier_defaults_openai_only() -> None:
    tiers = select_tier_defaults(
        openai_vendor_ids=["gpt-4o-mini", "gpt-4o", "o1"],
        anthropic_vendor_ids=[],
    )
    assert tiers[ModelTier.cheap].endswith("gpt-4o-mini")
    assert tiers[ModelTier.standard].endswith("gpt-4o")
    # Frontier prefers gpt-4o when present (see generator ordering), not o1.
    assert tiers[ModelTier.frontier].endswith("gpt-4o")


def test_select_tier_defaults_mixed_prefers_anthropic_frontier() -> None:
    tiers = select_tier_defaults(
        openai_vendor_ids=["gpt-4o-mini", "gpt-4o"],
        anthropic_vendor_ids=["claude-3-5-sonnet-20241022"],
    )
    assert "openai" in tiers[ModelTier.cheap]
    assert "openai" in tiers[ModelTier.standard]
    assert "anthropic" in tiers[ModelTier.frontier]


def _info(vendor_id: str) -> ModelInfo:
    return ModelInfo.model_construct(
        id=vendor_id,
        created_at="2020-01-01T00:00:00Z",
        display_name=vendor_id,
        type="model",
        capabilities=None,
        max_input_tokens=200_000,
        max_tokens=8192,
    )


def test_build_registry_file_openai_only_roundtrip() -> None:
    data = build_registry_file(
        openai_vendor_ids=["gpt-4o-mini", "gpt-4o"],
        anthropic_infos=None,
    )
    ModelRegistryFile.model_validate_json(data.model_dump_json())


def test_build_registry_file_anthropic_only() -> None:
    data = build_registry_file(
        openai_vendor_ids=None,
        anthropic_infos=[
            _info("claude-3-haiku-20240307"),
            _info("claude-3-5-sonnet-20241022"),
        ],
    )
    assert len(data.models) == 2
    ModelRegistryFile.model_validate_json(data.model_dump_json())


def test_anthropic_message_text_joins_text_blocks() -> None:
    msg = Message.model_construct(
        id="msg_1",
        content=[TextBlock(type="text", text='[{"x": 1}]')],
        model="claude-haiku-4-5",
        role="assistant",
        stop_reason="end_turn",
        type="message",
        usage=Usage.model_construct(input_tokens=1, output_tokens=2),
    )
    assert _anthropic_message_text(msg) == '[{"x": 1}]'


def test_extract_first_json_array_tolerates_prefix() -> None:
    raw = 'Some text\n```json\n[{"a": 1}]\n```\n'
    assert extract_first_json_array(raw) == [{"a": 1}]


def test_doc_overlay_deprecated_null_coerces_false() -> None:
    row = DocOverlay(
        provider=Provider.openai,
        vendor_model_id="gpt-3.5-turbo-16k",
        deprecated=None,  # type: ignore[arg-type]
    )
    assert row.deprecated is False


def test_apply_doc_overlays_drop_deprecated() -> None:
    data = build_registry_file(
        openai_vendor_ids=["gpt-4o-mini", "gpt-4o"],
        anthropic_infos=None,
    )
    overlays = [
        DocOverlay(
            provider=Provider.openai,
            vendor_model_id="gpt-4o-mini",
            deprecated=True,
        ),
        DocOverlay(
            provider=Provider.openai,
            vendor_model_id="gpt-4o",
            deprecated=False,
            input_price_per_million_usd=2.5,
            output_price_per_million_usd=10.0,
        ),
    ]
    merged = apply_doc_overlays(list(data.models), overlays)
    assert len(merged) == 1
    assert merged[0].vendor_model_id == "gpt-4o"
    assert merged[0].input_price_per_million_usd == 2.5
    assert merged[0].output_price_per_million_usd == 10.0
    out = finalize_registry(merged)
    ModelRegistryFile.model_validate_json(out.model_dump_json())


def test_finalize_registry_sorts_by_dgraph_id() -> None:
    data = build_registry_file(
        openai_vendor_ids=["gpt-4o", "gpt-4o-mini"],
        anthropic_infos=None,
    )
    shuffled = [data.models[1], data.models[0]]
    out = finalize_registry(shuffled)
    ids = [m.dgraph_model_id for m in out.models]
    assert ids == sorted(ids)
