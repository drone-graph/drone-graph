"""Skill registry builtin — discover, fetch, install, and link skill packages.

Actions:
  scan_local      — scan a directory tree for SKILL.md packages
  fetch_github    — download a skill package from a GitHub repo
  install         — register a local skill package as an installed :Tool
  link_gap        — record that a skill was used for a specific gap (:Tool)-[:USED_BY]->(:Gap)
  find_for_gap    — find skills already linked to similar gaps or matching intent
  list_installed  — list skills registered by this tool (kind=installed with skill_package_id)

Skills are stored as on-disk folders (SKILL.md + optional metadata.json). Once
``install``-ed they become :Tool nodes so every drone can discover them via
``cm_list_tools`` / ``cm_search_tools``. The ``link_gap`` action records usage
in the graph so future gaps can inherit skill suggestions.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

from drone_graph.gaps.records import FindingAuthor, FindingKind
from drone_graph.skills_marketplace.skill_packages.parse import (
    load_skill_package,
)
from drone_graph.skills_marketplace.skill_packages.paths import (
    SKILL_ROOT_ENV,
    resolve_skill_package_path,
)
from drone_graph.skills_marketplace.skill_packages.records import (
    ParsedSkillPackage,
    SkillPackageError,
)
from drone_graph.tools.records import Tool, ToolKind, TrustTier
from drone_graph.tools.registry import DroneContext, ToolResult, register_tool

# ---------------------------------------------------------------------------
# GitHub raw-content helpers
# ---------------------------------------------------------------------------

_GITHUB_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)(?:/(?P<path>.*))?"
)

_RAW_TEMPLATE = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"


def _github_to_raw(url: str, path: str = "", branch: str = "main") -> str | None:
    m = _GITHUB_RE.match(url.strip())
    if not m:
        return None
    owner, repo = m.group("owner"), m.group("repo")
    repo_path = m.group("path") or ""
    # repo_path may contain /tree/branch or /blob/branch prefix
    if repo_path.startswith("tree/") or repo_path.startswith("blob/"):
        parts = repo_path.split("/", 2)
        if len(parts) >= 2:
            branch = parts[1]
            repo_path = parts[2] if len(parts) > 2 else ""
    # Combine explicit path override with parsed repo path
    full_path = "/".join(p for p in [repo_path, path] if p)
    return _RAW_TEMPLATE.format(owner=owner, repo=repo, branch=branch, path=full_path)


def _http_get_text(url: str, timeout_s: float = 30.0) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "drone-graph-skill-registry/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data: bytes = resp.read()
        return data.decode("utf-8")


# ---------------------------------------------------------------------------
# Local filesystem scanning
# ---------------------------------------------------------------------------

def _scan_for_skills(root: Path) -> list[Path]:
    """Recursively find directories that contain a SKILL.md file."""
    results: list[Path] = []
    if not root.is_dir():
        return results
    for entry in root.iterdir():
        if entry.is_dir():
            skill_md = entry / "SKILL.md"
            if skill_md.is_file():
                results.append(entry)
            # One level deeper (e.g. skills/category/skill_id/)
            for sub in entry.iterdir():
                if sub.is_dir() and (sub / "SKILL.md").is_file():
                    results.append(sub)
    return results


# ---------------------------------------------------------------------------
# Skill → Tool conversion
# ---------------------------------------------------------------------------

def _skill_to_tool(pkg: ParsedSkillPackage, skill_dir: Path, drone_id: str) -> Tool:
    """Convert a parsed skill package into a Tool record ready for registration."""
    # Derive a stable tool name from the skill id.
    name = re.sub(r"[^a-zA-Z0-9_]", "_", pkg.skill_id).lower()
    # Build usage: tell future drones how to load this skill as context.
    usage = (
        f"Load skill '{pkg.title}' as context_preload via skill_package:{skill_dir.name}. "
        f"Use render_skill_preload_section('{skill_dir}') or add "
        f"'skill_package:{skill_dir.name}' to gap.context_preload."
    )
    # The input schema is empty because this tool is documentation/context only.
    schema = {"type": "object", "properties": {}, "required": []}
    return Tool(
        name=name,
        description=f"{pkg.title} — {pkg.description or 'Skill package'}",
        input_schema_json=json.dumps(schema),
        kind=ToolKind.installed,
        usage=usage,
        install_commands=[],
        depends_on=[],
        installed_by_drone_id=drone_id,
        skill_package_path=str(skill_dir.resolve()),
        skill_package_id=pkg.skill_id,
        trust_tier=TrustTier.standard,
    )


# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------

def _action_scan_local(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    raw_path = args.get("path", "")
    path_str = str(raw_path).strip() if raw_path not in (None, "") else ""
    if path_str:
        root = resolve_skill_package_path(path_str)
    else:
        root_env = os.environ.get(SKILL_ROOT_ENV)
        root = Path(root_env) if root_env else Path.cwd() / "skills"
    dirs = _scan_for_skills(root)
    out: list[dict[str, Any]] = []
    for d in dirs:
        try:
            pkg = load_skill_package(d)
        except SkillPackageError as e:
            out.append({"dir": str(d), "error": str(e)})
            continue
        out.append({
            "dir": str(d),
            "skill_id": pkg.skill_id,
            "title": pkg.title,
            "triggers": pkg.triggers,
            "version": pkg.version,
        })
    return ToolResult(content=json.dumps({"root": str(root), "skills": out}))


def _action_fetch_github(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    github_url = str(args.get("github_url", "")).strip()
    if not github_url:
        return ToolResult(content="ERROR: github_url required")
    skill_path = str(args.get("path", "")).strip()
    branch = str(args.get("branch", "main")).strip() or "main"
    raw_base = _github_to_raw(github_url, path=skill_path, branch=branch)
    if raw_base is None:
        return ToolResult(
            content=f"ERROR: could not parse GitHub URL {github_url!r}"
        )
    # Try to fetch SKILL.md
    skill_md_url = raw_base.rstrip("/") + "/SKILL.md" if skill_path else raw_base + "/SKILL.md"
    try:
        skill_md_text = _http_get_text(skill_md_url)
    except HTTPError as e:
        return ToolResult(
            content=f"ERROR: failed to fetch SKILL.md from {skill_md_url}: HTTP {e.code}"
        )
    # Try optional metadata.json
    meta_url = (
        raw_base.rstrip("/") + "/metadata.json"
        if skill_path
        else raw_base + "/metadata.json"
    )
    meta_text: str | None = None
    with contextlib.suppress(HTTPError):
        meta_text = _http_get_text(meta_url)
    # Write to a temp dir under DRONE_GRAPH_SKILL_ROOT or cwd/skills
    root_env = os.environ.get(SKILL_ROOT_ENV)
    base_dir = Path(root_env) if root_env else Path.cwd() / "skills"
    base_dir.mkdir(parents=True, exist_ok=True)
    # Derive a local folder name from the GitHub URL + path
    m = _GITHUB_RE.match(github_url)
    owner = m.group("owner") if m else "unknown"
    repo = m.group("repo") if m else "unknown"
    slug = skill_path.replace("/", "_") if skill_path else repo
    local_dir = base_dir / f"{owner}_{slug}"
    # If it already exists, bump with a number
    if local_dir.exists():
        for i in range(1, 1000):
            candidate = base_dir / f"{owner}_{slug}_{i}"
            if not candidate.exists():
                local_dir = candidate
                break
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "SKILL.md").write_text(skill_md_text, encoding="utf-8")
    if meta_text is not None:
        (local_dir / "metadata.json").write_text(meta_text, encoding="utf-8")
    return ToolResult(
        content=json.dumps({
            "status": "fetched",
            "local_dir": str(local_dir),
            "skill_id": local_dir.name,
            "from": skill_md_url,
        })
    )


def _action_install(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    raw_path = args.get("skill_package_path", "")
    path_str = str(raw_path).strip() if raw_path not in (None, "") else ""
    if not path_str:
        return ToolResult(content="ERROR: skill_package_path required")
    skill_dir = resolve_skill_package_path(path_str)
    try:
        pkg = load_skill_package(skill_dir)
    except SkillPackageError as e:
        return ToolResult(content=f"ERROR: invalid skill package: {e}")
    tool = _skill_to_tool(pkg, skill_dir, ctx.drone_id)
    try:
        ctx.tool_store.register_installed(tool)
    except ValueError as e:
        # Already registered? Return the existing record.
        existing = ctx.tool_store.get(tool.name)
        if existing is not None:
            return ToolResult(
                content=json.dumps({
                    "status": "already_installed",
                    "tool_name": existing.name,
                    "skill_package_id": existing.skill_package_id,
                })
            )
        return ToolResult(content=f"ERROR: registration failed: {e}")
    if ctx.tape is not None:
        ctx.tape.emit(
            "tool.skill_install",
            drone_id=ctx.drone_id,
            tool_name=tool.name,
            skill_package_id=tool.skill_package_id,
        )
    return ToolResult(
        content=json.dumps({
            "status": "installed",
            "tool_name": tool.name,
            "skill_package_id": tool.skill_package_id,
            "skill_package_path": tool.skill_package_path,
        })
    )


def _action_link_gap(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    tool_name = str(args.get("tool_name", "")).strip()
    gap_id = str(args.get("gap_id", "")).strip()
    if not tool_name or not gap_id:
        return ToolResult(content="ERROR: tool_name and gap_id required")
    t = ctx.tool_store.get(tool_name)
    if t is None:
        return ToolResult(content=f"ERROR: no tool named {tool_name!r}")
    g = ctx.store.get(gap_id)
    if g is None:
        return ToolResult(content=f"ERROR: no gap with id {gap_id!r}")
    ctx.tool_store.record_usage(tool_name, gap_id)
    # Also append a finding so the linkage is visible in the audit trail.
    ctx.store.append_finding(
        tick=ctx.tick,
        author=FindingAuthor.worker,
        kind=FindingKind.skill_invocation,
        summary=f"Skill {tool_name!r} linked to gap {gap_id[:8]} by skill_registry",
        affected_gap_ids=[gap_id],
        invocation_tool_name=tool_name,
        invocation_outcome="linked",
    )
    return ToolResult(
        content=json.dumps({
            "status": "linked",
            "tool_name": tool_name,
            "gap_id": g.id,
        })
    )


def _action_find_for_gap(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    """Find skills that have been used for gaps with similar intent."""
    gap_id = str(args.get("gap_id", "")).strip()
    if not gap_id:
        return ToolResult(content="ERROR: gap_id required")
    g = ctx.store.get(gap_id)
    if g is None:
        return ToolResult(content=f"ERROR: no gap with id {gap_id!r}")
    # 1. Skills directly linked to this gap via USED_BY
    direct_rows = ctx.tool_store.substrate.execute_read(
        "MATCH (t:Tool)-[:USED_BY]->(:Gap {id: $gap_id}) RETURN t.name AS name",
        gap_id=g.id,
    )
    direct = [r["name"] for r in direct_rows]
    # 2. Skills linked to gaps whose intent contains overlapping words
    intent_words = set(re.findall(r"[a-zA-Z0-9_]+", g.intent.lower()))
    related: list[dict[str, Any]] = []
    if intent_words:
        # Find other gaps that share at least one significant word
        all_gaps = ctx.store.all_gaps()
        for other in all_gaps:
            if other.id == g.id:
                continue
            other_words = set(re.findall(r"[a-zA-Z0-9_]+", other.intent.lower()))
            if intent_words & other_words:
                rows = ctx.tool_store.substrate.execute_read(
                    "MATCH (t:Tool)-[:USED_BY]->(:Gap {id: $gid}) RETURN t.name AS name",
                    gid=other.id,
                )
                for r in rows:
                    existing = {x["tool_name"] for x in related}
                    if r["name"] not in direct and r["name"] not in existing:
                        related.append({
                            "tool_name": r["name"],
                            "from_gap_id": other.id,
                            "from_intent": other.intent,
                        })
    # 3. All installed tools that carry a skill_package_id
    skill_tools = [
        t for t in ctx.tool_store.all_tools()
        if t.kind is ToolKind.installed and t.skill_package_id is not None
    ]
    return ToolResult(content=json.dumps({
        "gap_id": g.id,
        "gap_intent": g.intent,
        "direct_skills": direct,
        "related_skills": related,
        "all_installed_skills": [
            {"name": t.name, "skill_package_id": t.skill_package_id, "description": t.description}
            for t in skill_tools
        ],
    }))


def _action_list_installed(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    tools = [
        t for t in ctx.tool_store.all_tools()
        if t.kind is ToolKind.installed and t.skill_package_id is not None
    ]
    out = []
    for t in tools:
        # Count how many gaps have used this skill
        rows = ctx.tool_store.substrate.execute_read(
            "MATCH (:Tool {name: $name})-[:USED_BY]->(g:Gap) RETURN count(g) AS c",
            name=t.name,
        )
        usage_count = rows[0]["c"] if rows else 0
        out.append({
            "name": t.name,
            "skill_package_id": t.skill_package_id,
            "skill_package_path": t.skill_package_path,
            "description": t.description,
            "usage_count": usage_count,
            "trust_tier": t.trust_tier.value,
            "flagged_by_alignment": t.flagged_by_alignment,
        })
    return ToolResult(content=json.dumps({"skills": out, "count": len(out)}))


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

@register_tool(
    "cm_skill_registry",
    (
        "Skill registry: discover, fetch, install, and link skill packages. "
        "Skills are on-disk folders (SKILL.md + metadata.json) with step-by-step "
        "instructions for common multi-step tasks. Once installed they become "
        ":Tool nodes discoverable by all drones. "
        "Use link_gap to record which skills were used for which tasks so future "
        "gaps inherit suggestions. "
        "ACCOUNT CREATION AND LOGIN are human-only operations. If your gap "
        "requires signing into a platform (Google, GitHub, Reddit, X/Twitter, "
        "LinkedIn), use cm_chat to ask the operator to sign in manually "
        "through the browser. Do NOT attempt to automate login or account "
        "creation."
    ),
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "scan_local",
                    "fetch_github",
                    "install",
                    "link_gap",
                    "find_for_gap",
                    "list_installed",
                ],
                "description": "Which operation to perform.",
            },
            "path": {
                "type": "string",
                "description": (
                    "For scan_local / install: directory path "
                    "(absolute or relative to DRONE_GRAPH_SKILL_ROOT)."
                ),
            },
            "github_url": {
                "type": "string",
                "description": "For fetch_github: GitHub repo URL, e.g. https://github.com/owner/repo",
            },
            "branch": {
                "type": "string",
                "description": "For fetch_github: git branch (default main).",
            },
            "skill_package_path": {
                "type": "string",
                "description": "For install: path to skill package dir containing SKILL.md.",
            },
            "tool_name": {
                "type": "string",
                "description": "For link_gap: name of the installed skill tool.",
            },
            "gap_id": {
                "type": "string",
                "description": "For link_gap / find_for_gap: gap id to link or query.",
            },
        },
        "required": ["action"],
    },
)
def cm_skill_registry(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    action = str(args.get("action", "")).strip()
    dispatchers: dict[str, Any] = {
        "scan_local": _action_scan_local,
        "fetch_github": _action_fetch_github,
        "install": _action_install,
        "link_gap": _action_link_gap,
        "find_for_gap": _action_find_for_gap,
        "list_installed": _action_list_installed,
    }
    fn = dispatchers.get(action)
    if fn is None:
        valid = ", ".join(dispatchers)
        return ToolResult(content=f"ERROR: unknown action {action!r}. Valid: {valid}")
    result: ToolResult = fn(args, ctx)
    return result
