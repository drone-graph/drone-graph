"""Fetch and cache official vendor doc pages for model-registry enrichment.

Static HTTP fetch + on-disk TTL cache (no per-model web search). Uses a browser-like
User-Agent because some doc sites return 403 for default Python clients.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from drone_graph.drones.providers import Provider

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 DroneGraphRegistry/0.1"
)

# Small allowlist: pricing + deprecations cover most registry fields.
_OPENAI_DOC_URLS: tuple[str, ...] = (
    "https://platform.openai.com/docs/pricing/",
    "https://platform.openai.com/docs/deprecations/",
)

_ANTHROPIC_DOC_URLS: tuple[str, ...] = (
    "https://docs.anthropic.com/en/docs/about-claude/pricing",
    "https://docs.anthropic.com/en/docs/resources/model-deprecations",
)

# After HTML→text, cap each page so concatenated corpus stays bounded.
_MAX_TEXT_CHARS_PER_URL = 350_000
# If combined text is shorter than this, callers should fall back to web search.
MIN_USABLE_CORPUS_CHARS = 8_000
_FETCH_TIMEOUT_SEC = 45


def doc_urls_for_provider(provider: Provider) -> tuple[str, ...]:
    if provider == Provider.openai:
        return _OPENAI_DOC_URLS
    return _ANTHROPIC_DOC_URLS


def default_vendor_doc_cache_root() -> Path:
    """Directory for cached vendor-doc text (override with DRONE_GRAPH_VENDOR_DOC_CACHE)."""
    raw = os.environ.get("DRONE_GRAPH_VENDOR_DOC_CACHE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(tempfile.gettempdir()) / "drone-graph" / "vendor-docs"


def vendor_doc_cache_max_age_seconds() -> int:
    v = os.environ.get("DRONE_GRAPH_VENDOR_DOC_CACHE_MAX_AGE_HOURS", "").strip()
    try:
        hours = float(v) if v else 24.0
    except ValueError:
        hours = 24.0
    return max(60, int(hours * 3600))


class _StripHTMLParser(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self._SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if t in {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "th", "td"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0 and data and not data.isspace():
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = re.sub(r"[ \t\f\v]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str) -> str:
    p = _StripHTMLParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)[:_MAX_TEXT_CHARS_PER_URL]
    return p.text()


def _http_get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": _USER_AGENT}, method="GET")
    with urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
        return cast(bytes, resp.read())


def _cache_file_for_url(cache_root: Path, provider: Provider, url: str) -> Path:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    safe = re.sub(r"[^a-z0-9]+", "-", url.lower())[:60].strip("-") or "page"
    sub = cache_root / provider.value
    sub.mkdir(parents=True, exist_ok=True)
    return sub / f"{safe}-{h}.txt"


def _read_cached_text(path: Path, *, max_age_seconds: int) -> str | None:
    if not path.is_file():
        return None
    age = time.time() - path.stat().st_mtime
    if age > max_age_seconds:
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def fetch_and_store_url(
    url: str,
    *,
    provider: Provider,
    cache_root: Path,
    max_age_seconds: int,
) -> str:
    """Return plain text for ``url``, using disk cache when fresh enough."""
    path = _cache_file_for_url(cache_root, provider, url)
    cached = _read_cached_text(path, max_age_seconds=max_age_seconds)
    if cached is not None:
        return cached
    try:
        body = _http_get(url)
    except (HTTPError, URLError, TimeoutError, OSError) as e:
        msg = f"GET {url!r} failed: {e}"
        raise RuntimeError(msg) from e
    html = body.decode("utf-8", errors="replace")
    text = html_to_text(html)
    if len(text) > _MAX_TEXT_CHARS_PER_URL:
        text = text[:_MAX_TEXT_CHARS_PER_URL] + "\n\n[truncated]\n"
    path.write_text(text, encoding="utf-8")
    return text


def load_provider_doc_corpus(
    provider: Provider,
    *,
    cache_root: Path | None = None,
    max_age_seconds: int | None = None,
) -> str:
    """Fetch (or load from cache) all configured URLs for ``provider`` and concatenate."""
    root = cache_root or default_vendor_doc_cache_root()
    ttl = max_age_seconds if max_age_seconds is not None else vendor_doc_cache_max_age_seconds()
    parts: list[str] = []
    for url in doc_urls_for_provider(provider):
        chunk = fetch_and_store_url(url, provider=provider, cache_root=root, max_age_seconds=ttl)
        parts.append(f"=== SOURCE: {url} ===\n{chunk}")
    return "\n\n".join(parts)


def corpus_is_usable(corpus: str) -> bool:
    return len(corpus.strip()) >= MIN_USABLE_CORPUS_CHARS


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0])
    merged: list[tuple[int, int]] = [intervals[0]]
    for start, end in intervals[1:]:
        ps, pe = merged[-1]
        if start <= pe + 1:
            merged[-1] = (ps, max(pe, end))
        else:
            merged.append((start, end))
    return merged


def excerpt_for_model_id(
    corpus: str,
    vendor_model_id: str,
    *,
    max_chars: int = 28_000,
    window: int = 12_000,
) -> str:
    """Pick a bounded slice of ``corpus`` centered on ``vendor_model_id`` mentions."""
    if len(corpus) <= max_chars:
        return corpus
    n = len(corpus)
    spans: list[tuple[int, int]] = []
    for needle in (vendor_model_id, vendor_model_id.lower()):
        if not needle:
            continue
        start = 0
        while True:
            i = corpus.find(needle, start)
            if i < 0:
                break
            lo = max(0, i - window)
            hi = min(n, i + len(needle) + window)
            spans.append((lo, hi))
            start = i + max(1, len(needle) // 2)
    merged = _merge_intervals(spans)
    if not merged:
        head = max_chars // 2
        tail = max_chars - head
        mid = "\n\n[… middle omitted …]\n\n"
        body = corpus[:head] + mid + corpus[-tail:]
        return body[:max_chars]

    pieces: list[str] = []
    used = 0
    for lo, hi in merged:
        chunk = corpus[lo:hi]
        if used + len(chunk) + 2 > max_chars:
            remain = max_chars - used - 2
            if remain <= 0:
                break
            chunk = chunk[:remain]
        pieces.append(chunk)
        used += len(chunk) + 2
        if used >= max_chars:
            break
    return "\n\n---\n\n".join(pieces)[:max_chars]
