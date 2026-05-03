"""Skill package loader — filesystem fixtures only (no graph)."""

from __future__ import annotations

from pathlib import Path

import pytest

from drone_graph.skills_marketplace.skill_packages import (
    ParsedSkillPackage,
    SkillPackageError,
    load_skill_package,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "skill_packages"


def test_load_minimal_derives_title_from_heading() -> None:
    p = load_skill_package(_FIXTURES / "minimal")
    assert isinstance(p, ParsedSkillPackage)
    assert p.skill_id == "minimal"
    assert p.title == "My Skill"
    assert "Do the thing." in p.body
    assert p.triggers == []
    assert p.version is None
    assert p.description is None


def test_load_full_uses_metadata() -> None:
    p = load_skill_package(_FIXTURES / "full")
    assert p.skill_id == "full"
    assert p.title == "Full Metadata Skill"
    assert p.triggers == ["run demo", "demo task"]
    assert p.version == "1.0.0"
    assert p.description == "A fixture with full metadata."
    assert "Body line one." in p.body


def test_bad_metadata_raises() -> None:
    with pytest.raises(SkillPackageError, match="invalid JSON"):
        load_skill_package(_FIXTURES / "bad_metadata")


def test_missing_skill_md_raises(tmp_path: Path) -> None:
    d = tmp_path / "empty_pkg"
    d.mkdir()
    with pytest.raises(SkillPackageError, match=r"missing SKILL\.md"):
        load_skill_package(d)


def test_not_a_directory_raises(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(SkillPackageError, match="not a directory"):
        load_skill_package(f)
