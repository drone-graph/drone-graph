from __future__ import annotations

from drone_graph.drones.providers import Provider
from drone_graph.model_registry.vendor_doc_cache import (
    bounded_source_section,
    clean_vendor_doc_plaintext,
    corpus_is_usable,
    excerpt_for_cached_enrichment,
    excerpt_for_model_id,
    html_to_text,
)


def test_clean_vendor_doc_plaintext_drops_boilerplate() -> None:
    raw = "Real pricing row for claude-3-5-sonnet\n\nAccept all cookies\n\n$3 / MTok\n"
    cleaned = clean_vendor_doc_plaintext(raw)
    assert "Accept all cookies" not in cleaned
    assert "Real pricing" in cleaned


def test_html_to_text_strips_script() -> None:
    html = "<html><body><script>evil()</script><p>Hello <b>world</b></p></body></html>"
    t = html_to_text(html)
    assert "evil" not in t
    assert "Hello" in t
    assert "world" in t


def test_excerpt_for_model_id_prefers_windows() -> None:
    corpus = "aaa\n" + "x" * 5000 + "\ngpt-4o-mini price row\n" + "y" * 5000 + "\nzzz"
    ex = excerpt_for_model_id(corpus, "gpt-4o-mini", max_chars=2000, window=80)
    assert "gpt-4o-mini" in ex
    assert len(ex) <= 2000


def test_excerpt_head_tail_when_needle_missing() -> None:
    corpus = "A" * 10_000 + "middle" + "B" * 10_000
    ex = excerpt_for_model_id(corpus, "nonexistent-model-id", max_chars=2000)
    assert len(ex) <= 2000
    assert "middle omitted" in ex or ex.startswith("A") or ex.endswith("B")


def test_corpus_is_usable_threshold() -> None:
    assert not corpus_is_usable("short")
    assert corpus_is_usable("x" * 4001)


def test_doc_urls_openai_and_anthropic() -> None:
    from drone_graph.model_registry.vendor_doc_cache import doc_urls_for_provider

    assert len(doc_urls_for_provider(Provider.openai)) >= 1
    assert len(doc_urls_for_provider(Provider.anthropic)) >= 1


def test_bounded_source_section_extracts_block() -> None:
    corpus = (
        "=== SOURCE: https://platform.openai.com/docs/pricing/ ===\n"
        "price table here\n\n"
        "=== SOURCE: https://platform.openai.com/docs/deprecations/ ===\n"
        "old models\n"
    )
    pr = bounded_source_section(
        corpus, "https://platform.openai.com/docs/pricing/", max_chars=500
    )
    assert "price table" in pr
    assert "old models" not in pr
    dep = bounded_source_section(
        corpus, "https://platform.openai.com/docs/deprecations/", max_chars=500
    )
    assert "old models" in dep


def test_excerpt_for_cached_enrichment_openai_includes_sections() -> None:
    corpus = (
        "=== SOURCE: https://developers.openai.com/api/docs/deprecations ===\n"
        "gpt-4-0613 retired\n\n"
        "other doc body mentioning gpt-4o-mini pricing context\n"
    )
    ex = excerpt_for_cached_enrichment(
        corpus, "gpt-4o-mini", provider=Provider.openai, max_chars=8000
    )
    assert "gpt-4o-mini" in ex
    assert "retired" in ex or "gpt-4-0613" in ex
    assert "DEPRECATIONS" in ex or "deprecations" in ex.lower()


def test_excerpt_for_cached_enrichment_anthropic_unchanged_shape() -> None:
    corpus = "aaa\n" + "x" * 5000 + "\nclaude-3-haiku row\n" + "y" * 5000
    ex = excerpt_for_cached_enrichment(
        corpus, "claude-3-haiku", provider=Provider.anthropic, max_chars=15_000
    )
    assert "claude-3-haiku" in ex
    assert len(ex) <= 15_000


def test_excerpt_for_cached_enrichment_anthropic_includes_sections() -> None:
    corpus = (
        "=== SOURCE: https://platform.claude.com/docs/en/about-claude/pricing ===\n"
        "claude-opus price row\n\n"
        "=== SOURCE: https://platform.claude.com/docs/en/about-claude/models/overview ===\n"
        "claude-2 retired\n\n"
    )
    ex = excerpt_for_cached_enrichment(
        corpus, "claude-opus", provider=Provider.anthropic, max_chars=12_000
    )
    assert "claude-opus" in ex
    assert "retired" in ex or "claude-2" in ex
    assert "PRICING" in ex or "pricing" in ex.lower()
