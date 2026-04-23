"""Tests for OpenAI deprecations excerpt shaping in doc_enrich."""

from __future__ import annotations

from drone_graph.model_registry.doc_enrich import (
    _OPENAI_DEP_USER_MAX_CHARS,
    _openai_deprecations_user_excerpt,
)


def test_dep_excerpt_short_returns_unchanged() -> None:
    body = "# Deprecations\n\n| x | `my-model` |\n"
    assert _openai_deprecations_user_excerpt(body, "my-model") == body.strip()


def test_dep_excerpt_long_includes_context_around_backticked_id() -> None:
    pad = "x\n" * 20_000
    needle = "| 2099-01-01  | `unique-retire-target`  | `replacement`  |\n"
    tail = "y\n" * 20_000
    body = pad + needle + tail
    out = _openai_deprecations_user_excerpt(body, "unique-retire-target")
    assert "`unique-retire-target`" in out
    assert len(out) <= _OPENAI_DEP_USER_MAX_CHARS


def test_dep_excerpt_long_without_id_truncates_head() -> None:
    body = "z\n" * 100_000
    out = _openai_deprecations_user_excerpt(body, "no-such-model-in-page")
    assert len(out) == _OPENAI_DEP_USER_MAX_CHARS
    assert out.startswith("z\n")
