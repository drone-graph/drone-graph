"""Load and validate skill packages from disk."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from drone_graph.skills_marketplace.skill_packages.records import (
    ParsedSkillPackage,
    SkillPackageError,
    SkillPackageMetadata,
)

_SKILL_MD = "SKILL.md"
_METADATA_JSON = "metadata.json"
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$")


def _strip_utf8_bom(text: str) -> str:
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def _first_heading_title(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _HEADING_RE.match(stripped)
        if m:
            return m.group(1).strip()
        break
    return None


def load_skill_package(package_dir: Path) -> ParsedSkillPackage:
    """Load ``skills/<skill_id>/`` with required SKILL.md and optional metadata.json."""
    resolved = package_dir.resolve()
    if not resolved.is_dir():
        raise SkillPackageError(f"not a directory: {package_dir}")

    skill_md = resolved / _SKILL_MD
    if not skill_md.is_file():
        raise SkillPackageError(f"missing {_SKILL_MD} under {resolved}")

    raw = skill_md.read_text(encoding="utf-8")
    body = _strip_utf8_bom(raw)

    meta_path = resolved / _METADATA_JSON
    meta = SkillPackageMetadata()
    if meta_path.is_file():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            meta = SkillPackageMetadata.model_validate(data)
        except json.JSONDecodeError as e:
            raise SkillPackageError(
                f"invalid JSON in {meta_path}: {e}"
            ) from e
        except ValidationError as e:
            raise SkillPackageError(
                f"invalid metadata in {meta_path}: {e}"
            ) from e

    skill_id = resolved.name

    title = (meta.title or "").strip() if meta.title else ""
    if not title:
        title = _first_heading_title(body) or skill_id

    return ParsedSkillPackage(
        skill_id=skill_id,
        title=title,
        body=body,
        triggers=list(meta.triggers),
        version=meta.version,
        description=meta.description,
    )


def render_skill_preload_section(package_dir: Path) -> str:
    """Format a skill package directory for ``context_preload`` injection.

    On :class:`SkillPackageError`, returns a short error section so the drone
    still runs (mirrors unknown-preload behavior).
    """
    try:
        pkg = load_skill_package(package_dir)
    except SkillPackageError as e:
        return f"## Skill package (error)\n{e!s}\n"
    return f"## Skill: {pkg.title} (`{pkg.skill_id}`)\n\n{pkg.body}\n"
