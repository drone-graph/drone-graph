"""Unit tests for allowlisted Crawl4AI page fetch (no live browser in default suite)."""

from __future__ import annotations

import pytest

from drone_graph.skills_marketplace.tool.crawl4ai_page_tool import (
    fetch_allowed_page_markdown,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/foo",
        "http://evil.com/https://developers.openai.com/",
        "https://sub.developers.openai.com/api/docs/models/gpt-4o",
        "ftp://developers.openai.com/api/docs/models/gpt-4o",
    ],
)
def test_fetch_rejects_non_allowlisted_url(url: str) -> None:
    with pytest.raises(ValueError, match=r"allowlisted|Unsupported URL scheme"):
        fetch_allowed_page_markdown(url)
