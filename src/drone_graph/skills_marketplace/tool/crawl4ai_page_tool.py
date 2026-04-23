"""Allowlisted single-page fetch using Crawl4AI (markdown / cleaned HTML).

This is the registry's replacement for OpenAI Responses **hosted** ``web_search`` when
pulling official ``developers.openai.com`` API docs. Only explicit hosts are permitted to
limit SSRF risk.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from tqdm import tqdm

# Hosts we are willing to drive a headless browser against (extend with care).
_OPENAI_DEVELOPERS_HOST = "developers.openai.com"


def _coerce_stdio_utf8() -> None:
    """Avoid Crawl4AI / Rich ``UnicodeEncodeError`` on Windows cp1252 consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf is None:
            continue
        with contextlib.suppress(OSError, ValueError, AttributeError):
            reconf(encoding="utf-8", errors="replace")


def _assert_allowlisted_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        msg = f"Unsupported URL scheme for crawl4ai fetch: {parsed.scheme!r}"
        raise ValueError(msg)
    host = (parsed.hostname or "").lower()
    if host != _OPENAI_DEVELOPERS_HOST:
        msg = (
            "crawl4ai fetch is allowlisted to developers.openai.com only; "
            f"refusing host {host!r}"
        )
        raise ValueError(msg)


async def _crawl_markdown_async(url: str, *, page_timeout_ms: int) -> str:
    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=page_timeout_ms,
    )
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)
    text = (result.markdown or result.cleaned_html or "").strip()
    return text


def fetch_allowed_page_markdown(
    url: str,
    *,
    max_chars: int = 120_000,
    page_timeout_ms: int = 60_000,
    verbose: bool = False,
) -> str:
    """Return markdown (or cleaned HTML) for one **allowlisted** documentation URL.

    Raises ``ValueError`` if the URL is not on the allowlist. On crawl failure, returns a
    short parenthetical message so the LLM can still emit null pricing fields.
    """
    _assert_allowlisted_url(url)
    _coerce_stdio_utf8()
    if verbose:
        tqdm.write(f"[crawl4ai] fetching allowlisted page: {url}")
    try:
        markdown = asyncio.run(_crawl_markdown_async(url, page_timeout_ms=page_timeout_ms))
    except Exception as exc:
        if verbose:
            tqdm.write(f"[crawl4ai] fetch failed: {exc}")
        return f"(crawl4ai fetch failed: {exc})"
    if not markdown.strip():
        return "(crawl4ai returned empty body)"
    if len(markdown) > max_chars:
        return markdown[:max_chars] + "\n\n[page content truncated]\n"
    return markdown
