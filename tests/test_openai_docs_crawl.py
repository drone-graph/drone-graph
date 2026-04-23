"""Unit tests for OpenAI docs crawl helpers (no live browser)."""

from __future__ import annotations

import pytest

from drone_graph.skills_marketplace.tool import openai_docs_crawl as odc


def test_marker_from_model_url() -> None:
    assert odc.marker_from_model_url(
        "https://developers.openai.com/api/docs/models/gpt-4.1-mini"
    ) == "![gpt-4.1-mini]"


def test_trim_before_marker() -> None:
    text = "noise\n![gpt-4o]\n# Real\ncontent"
    assert odc.trim_before_marker(text, "![gpt-4o]") == "![gpt-4o]\n# Real\ncontent"


def test_normalize_pricing_strips_copy_page() -> None:
    raw = "Copy Page\n\n# Something Pricing here\n\n| a | b |\n"
    out = odc.normalize_pricing_markdown(raw)
    assert "Copy Page" not in out
    assert "# Pricing" in out


def test_normalize_deprecations_heading() -> None:
    raw = "Copy Page\n\n# Foo Deprecations bar\n\nbody"
    out = odc.normalize_deprecations_markdown(raw)
    assert "# Deprecations" in out


def test_get_simple_websearch_rejects_disallowed_host() -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        odc.get_simple_websearch("https://evil.com/doc")


def test_get_simple_websearch_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        odc.get_simple_websearch("ftp://platform.claude.com/x")


def test_slice_model_card_body_after_hero_marker() -> None:
    raw = "nav\n![gpt-4o]\n# GPT-4o\n## Summary\n| a | b |\n"
    assert odc._slice_model_card_body(raw, "![gpt-4o]") == "# GPT-4o\n## Summary\n| a | b |\n"


def test_slice_model_card_body_falls_back_to_summary() -> None:
    raw = "[ Home ](x)\n## Summary\n| Intelligence | High |\n"
    assert odc._slice_model_card_body(raw, "![missing-marker]").startswith("## Summary")


def test_extract_model_title_skips_bracket_heading_uses_slug() -> None:
    md = "[ x ](y)\n# [ Home ](https://developers.openai.com/)\nnoise"
    data = odc.extract_model_data(md, model_card_url="https://developers.openai.com/api/docs/models/gpt-x")
    assert data["model_name"] == "gpt-x"

    md2 = "![slug]\n# Real Model Name\n## Summary\n"
    data2 = odc.extract_model_data(md2, model_card_url="https://developers.openai.com/api/docs/models/gpt-x")
    assert data2["model_name"] == "Real Model Name"


def test_raw_fallback_rejects_docs_chrome() -> None:
    url = "https://developers.openai.com/api/docs/models/gpt-3.5-turbo-0125"
    chrome = "## Search the API docs\nPrimary navigation\n[ Home ](https://developers.openai.com/)"
    assert not odc._raw_fallback_looks_like_model_page(chrome, url)
    good = f"Some intro\n## Summary\nText about {url.split('/')[-1]} intelligence\n"
    assert odc._raw_fallback_looks_like_model_page(good, url)
