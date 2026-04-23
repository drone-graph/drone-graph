from __future__ import annotations
import json
from collections.abc import Iterator
from typing import Any

from anthropic import Anthropic


def _iter_sync_pages(page: Any) -> Iterator[Any]:
    cur: Any = page
    while True:
        yield cur
        if not cur.has_next_page():
            break
        cur = cur.get_next_page()


def fetch_anthropic_models_list_json_dump(
    api_key: str,
    *,
    max_chars: int = 600_000,
    list_limit: int = 100,
) -> str:
    """JSON array of every ``ModelInfo`` from ``client.models.list()`` across all pages.

    Each element is ``model_dump(mode="json")`` (``id``, ``capabilities``, ``max_input_tokens``,
    ``max_tokens``, ``display_name``, ``type``, …) — the same structure as iterating ``page.data``
    in the Anthropic Python SDK and printing ``model_dump()``.
    """
    client = Anthropic(api_key=api_key)
    payloads: list[dict[str, Any]] = []
    root = client.models.list(limit=list_limit)
    for pg in _iter_sync_pages(root):
        for m in pg.data:
            if getattr(m, "type", None) != "model":
                continue
            payloads.append(m.model_dump(mode="json"))
    raw = json.dumps(payloads, indent=2, default=str)
    if len(raw) > max_chars:
        return raw[:max_chars] + "\n\n[ANTHROPIC_MODELS_LIST_JSON truncated]\n"
    return raw
