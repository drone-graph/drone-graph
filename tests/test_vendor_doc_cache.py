from __future__ import annotations

from drone_graph.drones.providers import Provider
from drone_graph.model_registry.vendor_doc_cache import (
    corpus_is_usable,
    excerpt_for_model_id,
    html_to_text,
)


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
    assert corpus_is_usable("x" * 8001)


def test_doc_urls_openai_and_anthropic() -> None:
    from drone_graph.model_registry.vendor_doc_cache import doc_urls_for_provider

    assert len(doc_urls_for_provider(Provider.openai)) >= 1
    assert len(doc_urls_for_provider(Provider.anthropic)) >= 1
