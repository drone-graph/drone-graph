"""Scenario + root-seed loaders and event injection for the decomposition harness.

A **root seed** is a markdown file with ``# Intent`` and ``# Criteria`` sections
that becomes the root gap of a run.

A **scenario** is a JSON file that names a root, sets loop cadences, and lists
scheduled events to inject mid-run (user input, worker fail, worker fill).
Events can target a live leaf by intent substring, so scenarios stay robust to
the dynamic gap ids Gap Finding mints.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from drone_graph.gaps import Finding, FindingAuthor, FindingKind, GapStore

_SEEDS_PACKAGE = "drone_graph.seeds"


def _seed_dir(subdir: str) -> Path:
    return Path(str(files(_SEEDS_PACKAGE).joinpath(subdir)))


def available_roots() -> list[str]:
    return sorted(p.stem for p in _seed_dir("roots").glob("*.md"))


def available_scenarios() -> list[str]:
    return sorted(p.stem for p in _seed_dir("events").glob("*.json"))


def load_root_seed(root_name: str) -> tuple[str, str]:
    """Parse a root markdown file. Returns (intent, criteria)."""
    roots_dir = _seed_dir("roots")
    path = roots_dir / f"{root_name}.md"
    if not path.exists():
        matches = list(roots_dir.glob(f"*{root_name}*.md"))
        if not matches:
            raise FileNotFoundError(f"no root seed matching {root_name!r}")
        if len(matches) > 1:
            raise ValueError(f"ambiguous root {root_name!r}: {[m.name for m in matches]}")
        path = matches[0]
    text = path.read_text()
    return _extract_section(text, "Intent"), _extract_section(text, "Criteria")


def load_scenario(name: str) -> dict[str, Any]:
    events_dir = _seed_dir("events")
    path = events_dir / f"{name}.json"
    if not path.exists():
        matches = list(events_dir.glob(f"*{name}*.json"))
        if not matches:
            raise FileNotFoundError(f"no scenario matching {name!r}")
        if len(matches) > 1:
            raise ValueError(f"ambiguous scenario {name!r}: {[m.name for m in matches]}")
        path = matches[0]
    return json.loads(path.read_text())


def _extract_section(text: str, heading: str) -> str:
    m = re.search(
        rf"^#\s+{re.escape(heading)}\s*\n(.*?)(?=^#\s+|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not m:
        raise ValueError(f"section '# {heading}' not found")
    return m.group(1).strip()


def inject_event(
    store: GapStore,
    event: dict[str, Any],
    *,
    tick: int,
) -> Finding:
    """Inject a scheduled scenario event as a finding.

    Supports:
      - ``target_leaf_intent_match``: resolves a single live leaf by case-insensitive
        substring on ``intent``, overriding ``affected_gap_ids``.
      - ``author=worker`` + ``kind=fill``: routes through ``apply_fill`` so the gap
        actually transitions to ``filled`` (not just a log entry).
      - everything else: appended as a raw finding.
    """
    affected_ids = list(event.get("affected_gap_ids", []))

    needle = event.get("target_leaf_intent_match")
    if needle:
        leaf = store.find_leaf_by_intent_substring(needle)
        if leaf is not None:
            affected_ids = [leaf.id]

    author = FindingAuthor(event["author"])
    kind = FindingKind(event["kind"])
    summary = event["summary"]

    if author is FindingAuthor.worker and kind is FindingKind.fill and affected_ids:
        return store.apply_fill(
            gap_id=affected_ids[0],
            summary=summary,
            tick=tick,
            author=author,
        )

    if author is FindingAuthor.worker and kind is FindingKind.fail and affected_ids:
        return store.apply_fail(
            gap_id=affected_ids[0],
            summary=summary,
            tick=tick,
            author=author,
        )

    return store.append_finding(
        tick=tick,
        author=author,
        kind=kind,
        summary=summary,
        affected_gap_ids=affected_ids,
    )
