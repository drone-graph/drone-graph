"""Identity redaction filter for drone tool results.

A safety net to keep the operator's name / email / handle / hostname
from showing up in drone-visible text *even if* a drone managed to
shell-out to something that read them (a stray `git log`, a CI helper
that prints `$USER`, an MCP client that includes hostname in a header).

How it works:

  * At drone-runtime boot we read the redaction-patterns list from the
    env (mirrored by the API server from Settings).
  * Each pattern is compiled into a regex. Strings shorter than 4
    characters are skipped — they'd match too many common words.
  * Names (no ``@``, no dot) are bordered by ``\\b`` so they only match
    on word boundaries.
  * Anything matching is replaced with ``<redacted>``.
  * The runtime calls ``redact`` on the ``content`` of every
    ``ToolResult`` before sending it back to the model.

Operator-identity-granted drones don't get redacted — the whole point of
that mode is "act as the operator," and the substrate already records
that decision per-gap.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, Pattern

_MIN_PATTERN_LEN = 4
_REPLACEMENT = "<redacted>"
_ENV_VAR = "DRONE_GRAPH_REDACT_PATTERNS"
_IDENTITY_MODE_VAR = "DRONE_GRAPH_IDENTITY_MODE"


def _looks_like_email(p: str) -> bool:
    return "@" in p


def _compile_one(p: str) -> Pattern[str] | None:
    p = p.strip()
    if len(p) < _MIN_PATTERN_LEN:
        return None
    esc = re.escape(p)
    # Emails / things with @ or . don't need word-boundary; they're
    # specific enough on their own and \b doesn't work great around @.
    if _looks_like_email(p) or "." in p:
        return re.compile(esc, re.IGNORECASE)
    return re.compile(rf"\b{esc}\b", re.IGNORECASE)


def _compile(patterns: Iterable[str]) -> list[Pattern[str]]:
    out: list[Pattern[str]] = []
    for p in patterns:
        c = _compile_one(p)
        if c is not None:
            out.append(c)
    return out


# Compiled at module load from env; refresh-able if tests want.
_PATTERNS: list[Pattern[str]] = _compile(
    (os.environ.get(_ENV_VAR, "") or "").split("\x1f")
)


def set_patterns(patterns: Iterable[str]) -> None:
    """Replace the active pattern list (for tests / live reconfigure)."""
    global _PATTERNS
    _PATTERNS = _compile(patterns)


def redact(text: str) -> str:
    """Mask any active identity patterns in ``text``.

    No-op when the drone is in operator-identity mode — that drone is
    explicitly authorized to act as the operator, so masking would just
    confuse it.
    """
    if not text or not _PATTERNS:
        return text
    if os.environ.get(_IDENTITY_MODE_VAR, "") == "operator":
        return text
    out = text
    for pat in _PATTERNS:
        out = pat.sub(_REPLACEMENT, out)
    return out


def is_active() -> bool:
    """Whether redaction is currently doing anything (used by tests
    and a status field the runtime emits in spawn events)."""
    return (
        bool(_PATTERNS)
        and os.environ.get(_IDENTITY_MODE_VAR, "") != "operator"
    )
