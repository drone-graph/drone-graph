"""Crawl4AI parsers for OpenAI API docs (model card, pricing, deprecations).

Structured markdown for registry doc-enrichment. ``get_model_card`` validates OpenAI model-card
URLs; ``get_simple_websearch`` is an allowlisted **raw markdown** crawl for a caller-supplied
URL (default / fallback when structured parsing is thin). Hosts: OpenAI developers docs and
Anthropic Claude Console docs.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Final
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from tqdm import tqdm

from drone_graph.skills_marketplace.tool.crawl4ai_page_tool import _coerce_stdio_utf8

DEFAULT_PRICING_URL = "https://developers.openai.com/api/docs/pricing"
DEFAULT_DEPRECATIONS_URL = "https://developers.openai.com/api/docs/deprecations"

_OPENAI_DEVELOPERS_HOST = "developers.openai.com"
_DEFAULT_PAGE_TIMEOUT_MS = 90_000
# Raw ``get_simple_websearch`` allowed hosts (SSRF guard; extend deliberately).
_SIMPLE_WEBSEARCH_ALLOWED_HOSTS: Final[frozenset[str]] = frozenset(
    {
        "developers.openai.com",
        "platform.claude.com",
    }
)
_MODEL_CARD_FALLBACK_MIN_CHARS = 280
# OpenAI model pages are client-rendered; allow main content to hydrate before markdown extract.
_OPENAI_MODEL_CARD_DELAY_S = 2.5


def trim_before_marker(text: str, marker: str) -> str:
    if not marker:
        return text
    _before, sep, after = text.partition(marker)
    return f"{sep}{after}" if sep else text


def marker_from_model_url(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    return f"![{slug}]"


def _openai_model_slug_from_card_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def _slice_model_card_body(cleaned_markdown: str, marker: str) -> str:
    """Keep only the model-doc body for parsing.

    When the hero ``![slug]`` image line is present, content after it is the card. When it is
    missing (SPA shell / timing), fall back to the first ``## Summary`` block — otherwise the
    full-page markdown is mostly nav and ``extract_model_data`` would treat nav links as titles.
    """
    if marker in cleaned_markdown:
        _, _, after = cleaned_markdown.partition(marker)
        return after.lstrip()
    for needle in ("\n## Summary\n", "\n## Summary", "## Summary\n", "## Summary"):
        pos = cleaned_markdown.find(needle)
        if pos != -1:
            return cleaned_markdown[pos:].lstrip()
    return ""


def _model_display_title(compact: list[str], model_card_url: str) -> str:
    """First real ``# `` heading, else URL slug — never ``compact[1]`` (often nav)."""
    for line in compact[:80]:
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2 and not stripped.startswith("# ["):
            return stripped[2:].strip()
        if stripped.startswith("#") and len(stripped) > 1:
            inner = stripped.lstrip("#").strip()
            if inner and not inner.startswith("["):
                return inner
    slug = _openai_model_slug_from_card_url(model_card_url) if model_card_url else ""
    return slug if slug else "Unknown model"


def _raw_fallback_looks_like_model_page(raw: str, model_card_url: str) -> bool:
    """Avoid appending 60k+ of global docs chrome when the crawl never reached the card."""
    head = raw[:60_000].lower()
    slug = _openai_model_slug_from_card_url(model_card_url).lower()
    if "search the api docs" in head and slug and slug not in head:
        return False
    if slug and slug in head:
        return True
    return any(
        phrase in head
        for phrase in (
            "## summary",
            "intelligence",
            "context window",
            "text tokens",
            "modalities",
            "max output tokens",
        )
    )


def configure_utf8_output() -> None:
    """Avoid ``UnicodeEncodeError`` on Windows terminals (legacy encodings)."""
    _coerce_stdio_utf8()


def _assert_simple_websearch_url(url: str) -> None:
    """Allow only known vendor doc hosts for arbitrary-URL crawls."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        msg = f"Unsupported URL scheme for simple websearch: {parsed.scheme!r}"
        raise ValueError(msg)
    host = (parsed.hostname or "").lower()
    if host not in _SIMPLE_WEBSEARCH_ALLOWED_HOSTS:
        msg = (
            "get_simple_websearch is allowlisted to vendor doc hosts only; "
            f"refusing host {host!r} (allowed: {sorted(_SIMPLE_WEBSEARCH_ALLOWED_HOSTS)})"
        )
        raise ValueError(msg)


def _assert_openai_model_card_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        msg = f"Unsupported URL scheme for OpenAI model card: {parsed.scheme!r}"
        raise ValueError(msg)
    if (parsed.hostname or "").lower() != _OPENAI_DEVELOPERS_HOST:
        msg = f"Model card URL must be on {_OPENAI_DEVELOPERS_HOST!r}; got {parsed.hostname!r}"
        raise ValueError(msg)
    path = (parsed.path or "").rstrip("/")
    if not path.startswith("/api/docs/models/") or path == "/api/docs/models":
        msg = f"URL must be .../api/docs/models/<id>; got path {parsed.path!r}"
        raise ValueError(msg)


def normalize_pricing_markdown(markdown: str) -> str:
    section_titles = {
        "Flagship models",
        "Multimodal models",
        "Image generation models",
        "Video generation models",
        "Transcription models",
        "Tools",
        "Specialized models",
        "Finetuning",
    }
    mode_titles = {"Standard", "Batch", "Flex", "Priority"}
    mode_tab_noise = {"StandardBatchFlexPriority", "StandardBatch", "StandardBatchPriority"}

    output: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            if output and output[-1] != "":
                output.append("")
            continue

        if stripped == "Copy Page":
            continue
        if stripped == "All models":
            continue
        if stripped in mode_tab_noise:
            continue

        if stripped.startswith("#") and "Pricing" in stripped:
            output.append("# Pricing")
            continue
        if stripped in section_titles:
            output.append(f"## {stripped}")
            continue
        if stripped in mode_titles:
            output.append(f"### {stripped}")
            continue

        output.append(line)

    normalized = _join_wrapped_table_rows(output)
    while normalized and normalized[-1] == "":
        normalized.pop()
    return "\n".join(normalized)


def _join_wrapped_table_rows(lines: list[str]) -> list[str]:
    merged: list[str] = []
    for line in lines:
        stripped = line.strip()
        if (
            merged
            and merged[-1].lstrip().startswith("|")
            and not merged[-1].rstrip().endswith("|")
            and stripped
            and not stripped.startswith("|")
            and not stripped.startswith("#")
        ):
            merged[-1] = f"{merged[-1].rstrip()} {stripped}"
            continue
        merged.append(line)
    return merged


def normalize_deprecations_markdown(markdown: str) -> str:
    output: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            if output and output[-1] != "":
                output.append("")
            continue

        if stripped == "Copy Page":
            continue

        if stripped.startswith("#") and "Deprecations" in stripped:
            output.append("# Deprecations")
            continue
        if stripped in {"Overview", "Deprecation vs. legacy", "Deprecation history"}:
            output.append(f"## {stripped}")
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}:.+", stripped):
            output.append(f"### {stripped}")
            continue

        output.append(line)

    while output and output[-1] == "":
        output.pop()
    return "\n".join(output)


def parse_key_values(lines: list[str], start_token: str, end_token: str) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        start = lines.index(start_token) + 1
        end = lines.index(end_token, start)
    except ValueError:
        return data

    section = [line.strip() for line in lines[start:end] if line.strip()]
    for i in range(0, len(section) - 1, 2):
        key = section[i]
        value = section[i + 1]
        data[key] = value
    return data


def extract_model_data(trimmed_markdown: str, *, model_card_url: str = "") -> dict[str, Any]:
    lines = [line.rstrip() for line in trimmed_markdown.splitlines()]
    compact = [line.strip() for line in lines if line.strip()]

    model_name = _model_display_title(compact, model_card_url)
    summary: dict[str, str] = {}
    for i, line in enumerate(compact):
        if line == "Intelligence" and i + 5 < len(compact):
            summary["Intelligence"] = compact[i + 1]
            if compact[i + 2] == "Speed":
                summary["Speed"] = compact[i + 3]
            break

    description = ""
    for line in compact:
        if "excels at instruction following" in line:
            description = line
            break

    specs: dict[str, str] = {}
    for line in compact:
        if "context window" in line:
            specs["Context window"] = line
        elif "max output tokens" in line:
            specs["Max output tokens"] = line
        elif "knowledge cutoff" in line:
            specs["Knowledge cutoff"] = line

    pricing: dict[str, str] = {}
    text_pricing_start: int | None = None
    for i, line in enumerate(compact):
        if line == "Text tokens":
            text_pricing_start = i
            break
    if text_pricing_start is not None:
        for i in range(text_pricing_start, len(compact) - 1):
            if compact[i] in {"Input", "Cached input", "Output"}:
                value = compact[i + 1]
                if value.startswith("$"):
                    pricing[compact[i]] = value
            if compact[i] == "Quick comparison":
                break

    if pricing:
        summary["Price (Input)"] = pricing.get("Input", "N/A")
        summary["Price (Cached input)"] = pricing.get("Cached input", "N/A")
        summary["Price (Output)"] = pricing.get("Output", "N/A")

    modalities = parse_key_values(compact, "Modalities", "Endpoints")
    features = parse_key_values(compact, "Features", "Snapshots")

    snapshots: list[str] = []
    try:
        snap_start = compact.index("Snapshots")
        rate_start = compact.index("Rate limits", snap_start)
        for line in compact[snap_start:rate_start]:
            if re.fullmatch(r"gpt-[a-z0-9\.-]+", line):
                snapshots.append(line)
    except ValueError:
        pass

    rate_table_lines: list[str] = []
    for line in lines:
        if line.lstrip().startswith("|"):
            rate_table_lines.append(line)
    rate_limits_table = "\n".join(rate_table_lines).strip()

    return {
        "model_name": model_name,
        "description": description,
        "summary": summary,
        "specs": specs,
        "pricing": pricing,
        "modalities": modalities,
        "features": features,
        "snapshots": snapshots,
        "rate_limits_table": rate_limits_table,
    }


def format_markdown_report(data: dict[str, Any]) -> str:
    lines: list[str] = [f"# {data['model_name']}", ""]

    if data["description"]:
        lines.append(str(data["description"]))
        lines.append("")

    summary = data.get("summary") or {}
    if summary:
        lines.extend(
            [
                "## Summary",
                "| Metric | Value |",
                "| --- | --- |",
            ]
        )
        for key, value in summary.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")

    specs = data.get("specs") or {}
    if specs:
        lines.extend(["## Core Specs"])
        for key, value in specs.items():
            lines.append(f"- **{key}:** {value}")
        lines.append("")

    pricing = data.get("pricing") or {}
    if pricing:
        lines.extend(
            [
                "## Text Token Pricing (Per 1M Tokens)",
                "| Type | Price |",
                "| --- | --- |",
            ]
        )
        for key, value in pricing.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")

    modalities = data.get("modalities") or {}
    if modalities:
        lines.extend(
            [
                "## Modalities",
                "| Modality | Support |",
                "| --- | --- |",
            ]
        )
        for key, value in modalities.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")

    features = data.get("features") or {}
    if features:
        lines.extend(
            [
                "## Features",
                "| Feature | Support |",
                "| --- | --- |",
            ]
        )
        for key, value in features.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")

    snapshots = data.get("snapshots") or []
    if snapshots:
        lines.append("## Snapshots")
        for snapshot in snapshots:
            lines.append(f"- `{snapshot}`")
        lines.append("")

    rate_limits_table = data.get("rate_limits_table") or ""
    if rate_limits_table:
        lines.extend(["## Rate Limits", str(rate_limits_table), ""])

    return "\n".join(lines).strip()


async def model_card_parser(
    url: str,
    *,
    start_marker: str | None = None,
    page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
) -> str:
    marker = start_marker or marker_from_model_url(url)
    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=page_timeout_ms,
        delay_before_return_html=_OPENAI_MODEL_CARD_DELAY_S,
        wait_until="load",
    )
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)
    markdown = result.markdown or ""
    cleaned_markdown = markdown.replace(
        "[](https://developers.openai.com/api/docs/models)",
        "",
    )
    trimmed_markdown = _slice_model_card_body(cleaned_markdown, marker)
    if not trimmed_markdown.strip():
        return (
            "(openai model card: no model body in crawl markdown after hero marker / "
            "## Summary heuristics; page may be a shell or the id may not exist)"
        )
    model_data = extract_model_data(trimmed_markdown, model_card_url=url)
    return format_markdown_report(model_data)


async def pricing_page_parser(
    *,
    start_marker: str = "#  Pricing",
    page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
) -> str:
    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=page_timeout_ms)
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=DEFAULT_PRICING_URL, config=run_cfg)
    markdown = result.markdown or ""
    trimmed_markdown = trim_before_marker(markdown, start_marker)
    return normalize_pricing_markdown(trimmed_markdown)


async def deprecations_page_parser(
    *,
    start_marker: str = "#  Deprecations",
    page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
) -> str:
    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=page_timeout_ms)
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=DEFAULT_DEPRECATIONS_URL, config=run_cfg)
    markdown = result.markdown or ""
    trimmed_markdown = trim_before_marker(markdown, start_marker)
    return normalize_deprecations_markdown(trimmed_markdown)


async def simple_websearch_parser(
    url: str,
    *,
    page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
) -> str:
    """Return raw page markdown (no trimming) for an allowlisted documentation URL."""
    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=page_timeout_ms)
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)
    return (result.markdown or result.cleaned_html or "").strip()


def _maybe_append_simple_websearch_fallback(
    url: str,
    report: str,
    *,
    max_chars: int,
    page_timeout_ms: int,
    verbose: bool,
) -> str:
    """Append raw crawl when structured card is thin or an error parenthetical."""
    s = report.strip()
    needs_fallback = (
        s.startswith("(openai model card")
        or (not s.startswith("(") and len(s) < _MODEL_CARD_FALLBACK_MIN_CHARS)
    )
    if not needs_fallback:
        return report
    if verbose:
        tqdm.write("[openai-docs-crawl] structured card thin or error; simple websearch fallback")
    try:
        raw = asyncio.run(
            simple_websearch_parser(url, page_timeout_ms=page_timeout_ms),
        )
    except Exception as exc:
        if verbose:
            tqdm.write(f"[openai-docs-crawl] simple websearch fallback failed: {exc}")
        return report
    raw = raw.strip()
    if not raw:
        return report
    if not _raw_fallback_looks_like_model_page(raw, url):
        if verbose:
            tqdm.write(
                "[openai-docs-crawl] simple websearch fallback skipped "
                "(crawl looks like global docs chrome, not model card)"
            )
        return report
    combined = (
        f"{s}\n\n## Raw page (Crawl4AI simple websearch fallback)\n\n{raw}".strip()
        if s
        else f"## Raw page (Crawl4AI simple websearch fallback)\n\n{raw}".strip()
    )
    if len(combined) > max_chars:
        return combined[:max_chars] + "\n\n[combined model card + fallback truncated]\n"
    return combined


def get_simple_websearch(
    url: str,
    *,
    max_chars: int = 160_000,
    page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
    verbose: bool = False,
) -> str:
    """Crawl one allowlisted URL and return **raw** markdown (default / generic doc fetch)."""
    _assert_simple_websearch_url(url)
    configure_utf8_output()
    if verbose:
        tqdm.write(f"[openai-docs-crawl] simple websearch: {url}")
    try:
        text = asyncio.run(
            simple_websearch_parser(url, page_timeout_ms=page_timeout_ms),
        )
    except Exception as exc:
        if verbose:
            tqdm.write(f"[openai-docs-crawl] simple websearch failed: {exc}")
        return f"(simple websearch crawl failed: {exc})"
    text = text.strip()
    if not text:
        return "(simple websearch returned empty body)"
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[simple websearch truncated]\n"
    return text


def get_model_card(
    url: str,
    *,
    max_chars: int = 120_000,
    page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
    verbose: bool = False,
) -> str:
    """Crawl a single **model card** URL and return normalized markdown for the doc LLM."""
    _assert_openai_model_card_url(url)
    configure_utf8_output()
    if verbose:
        tqdm.write(f"[openai-docs-crawl] model card: {url}")
    try:
        report = asyncio.run(
            model_card_parser(url, page_timeout_ms=page_timeout_ms),
        )
    except Exception as exc:
        if verbose:
            tqdm.write(f"[openai-docs-crawl] model card failed: {exc}")
        return f"(openai model card crawl failed: {exc})"
    report = report.strip()
    if not report:
        return "(openai model card crawl returned empty body)"
    report = _maybe_append_simple_websearch_fallback(
        url,
        report,
        max_chars=max_chars,
        page_timeout_ms=page_timeout_ms,
        verbose=verbose,
    )
    if not report.strip():
        return "(openai model card crawl returned empty body)"
    if len(report) > max_chars:
        return report[:max_chars] + "\n\n[model card report truncated]\n"
    return report


def get_pricing_page(
    *,
    max_chars: int = 160_000,
    page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
    verbose: bool = False,
) -> str:
    """Crawl the official API **pricing** page; returns normalized markdown."""
    configure_utf8_output()
    if verbose:
        tqdm.write(f"[openai-docs-crawl] pricing: {DEFAULT_PRICING_URL}")
    try:
        text = asyncio.run(pricing_page_parser(page_timeout_ms=page_timeout_ms))
    except Exception as exc:
        if verbose:
            tqdm.write(f"[openai-docs-crawl] pricing failed: {exc}")
        return f"(openai pricing crawl failed: {exc})"
    text = text.strip()
    if not text:
        return "(openai pricing crawl returned empty body)"
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[pricing page truncated]\n"
    return text


def get_deprecations_page(
    *,
    max_chars: int = 120_000,
    page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
    verbose: bool = False,
) -> str:
    """Crawl the official API **deprecations** page; returns normalized markdown."""
    configure_utf8_output()
    if verbose:
        tqdm.write(f"[openai-docs-crawl] deprecations: {DEFAULT_DEPRECATIONS_URL}")
    try:
        text = asyncio.run(deprecations_page_parser(page_timeout_ms=page_timeout_ms))
    except Exception as exc:
        if verbose:
            tqdm.write(f"[openai-docs-crawl] deprecations failed: {exc}")
        return f"(openai deprecations crawl failed: {exc})"
    text = text.strip()
    if not text:
        return "(openai deprecations crawl returned empty body)"
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[deprecations page truncated]\n"
    return text
