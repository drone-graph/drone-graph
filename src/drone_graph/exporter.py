"""Run export — bundle a session into a self-contained directory (or zip)
for sharing or archival.

A bundle contains everything needed to review a session post-hoc:

  * ``manifest.json`` — run_id, timestamps, server version, file inventory
  * ``scheduler-tape.jsonl`` — scheduler event log
  * ``drones/<drone_id>.jsonl`` — per-drone tapes
  * ``substrate/gaps.jsonl`` — every gap node as JSON
  * ``substrate/findings.jsonl`` — every finding as JSON
  * ``substrate/tools.jsonl`` — every tool node as JSON
  * ``substrate/edges.jsonl`` — parent-of edges
  * ``artefacts/<finding_id>/<filename>`` — copies of files referenced by
    findings' ``artefact_paths``, when accessible
  * ``summary.md`` — auto-generated readable overview

The exporter never deletes anything; it just copies. Safe to run while a
swarm is active.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from drone_graph.gaps import Gap, GapStore
from drone_graph.substrate import Substrate
from drone_graph.tools import ToolStore


def find_latest_run_id() -> str | None:
    """Locate the most recent ``mission-control-*`` run directory under
    ``var/runs/`` and return its run_id. ``None`` if no runs exist."""
    runs_root = Path("var") / "runs"
    if not runs_root.exists():
        return None
    candidates = sorted(
        (d for d in runs_root.iterdir() if d.is_dir() and d.name.startswith("mission-control-")),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    # The directory name is ``mission-control-<run_id>``; run_id is what comes after.
    return candidates[0].name[len("mission-control-"):]


def export_run(
    *,
    run_id: str,
    out_dir: Path,
    include_artefacts: bool = True,
    substrate: Substrate | None = None,
) -> Path:
    """Export a run to ``out_dir``. Creates ``out_dir`` if it doesn't exist.
    Returns the path to the bundle directory."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "drones").mkdir(exist_ok=True)
    (out_dir / "substrate").mkdir(exist_ok=True)
    if include_artefacts:
        (out_dir / "artefacts").mkdir(exist_ok=True)

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "exported_at": datetime.now(UTC).isoformat(),
        "exporter_version": "0.1.0",
        "files": [],
    }

    # ---- Scheduler tape -------------------------------------------------
    run_dir = Path("var") / "runs" / f"mission-control-{run_id}"
    sched_tape = run_dir / "scheduler-tape.jsonl"
    if sched_tape.exists():
        dst = out_dir / "scheduler-tape.jsonl"
        shutil.copy2(sched_tape, dst)
        manifest["files"].append({"role": "scheduler_tape", "path": "scheduler-tape.jsonl"})

    # ---- Per-drone tapes ------------------------------------------------
    drone_dir = Path("var") / "tapes" / run_id
    if drone_dir.exists():
        for tape in sorted(drone_dir.glob("*.jsonl")):
            dst = out_dir / "drones" / tape.name
            shutil.copy2(tape, dst)
            manifest["files"].append({"role": "drone_tape", "path": f"drones/{tape.name}"})

    # ---- Substrate snapshot --------------------------------------------
    if substrate is not None:
        store = GapStore(substrate)
        tool_store = ToolStore(substrate)

        gaps_path = out_dir / "substrate" / "gaps.jsonl"
        with gaps_path.open("w", encoding="utf-8") as f:
            for g in store.all_gaps():
                f.write(_serialize_gap(g) + "\n")
        manifest["files"].append({"role": "gaps", "path": "substrate/gaps.jsonl"})

        findings_path = out_dir / "substrate" / "findings.jsonl"
        finding_count = 0
        artefact_paths: list[tuple[str, str]] = []  # (finding_id, path)
        with findings_path.open("w", encoding="utf-8") as f:
            for fnd in store.all_findings():
                f.write(_serialize_finding(fnd) + "\n")
                finding_count += 1
                for p in fnd.artefact_paths:
                    artefact_paths.append((fnd.id, p))
        manifest["files"].append({"role": "findings", "path": "substrate/findings.jsonl"})

        tools_path = out_dir / "substrate" / "tools.jsonl"
        with tools_path.open("w", encoding="utf-8") as f:
            for t in tool_store.all_tools():
                f.write(_serialize_tool(t) + "\n")
        manifest["files"].append({"role": "tools", "path": "substrate/tools.jsonl"})

        edges_path = out_dir / "substrate" / "edges.jsonl"
        with edges_path.open("w", encoding="utf-8") as f:
            for p, c in store.parent_edges():
                f.write(json.dumps({"parent": p, "child": c}) + "\n")
        manifest["files"].append({"role": "edges", "path": "substrate/edges.jsonl"})

        # ---- Artefacts (best-effort copy) -------------------------------
        copied_artefacts = 0
        skipped_artefacts: list[str] = []
        if include_artefacts:
            for finding_id, path_str in artefact_paths:
                # Skip pseudo-paths (e.g. ``inbox-resolution:<id>``).
                if ":" in path_str and not path_str.startswith("/") and not Path(path_str).exists():
                    continue
                src = Path(path_str)
                if not src.exists() or not src.is_file():
                    skipped_artefacts.append(path_str)
                    continue
                dst_dir = out_dir / "artefacts" / finding_id
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / src.name
                try:
                    shutil.copy2(src, dst)
                    copied_artefacts += 1
                except OSError:
                    skipped_artefacts.append(path_str)
        manifest["artefacts"] = {
            "copied": copied_artefacts,
            "skipped_paths": skipped_artefacts[:50],
            "skipped_count": len(skipped_artefacts),
        }

        # ---- Auto-generated summary -------------------------------------
        (out_dir / "summary.md").write_text(
            _summary_markdown(run_id, store, tool_store, finding_count),
            encoding="utf-8",
        )
        manifest["files"].append({"role": "summary", "path": "summary.md"})

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return out_dir


def zip_bundle(bundle_dir: Path, out_zip: Path) -> Path:
    """Zip a bundle directory into a single archive. The directory itself
    is preserved as the top-level inside the zip."""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(out_zip, "w", ZIP_DEFLATED, compresslevel=6) as zf:
        for p in bundle_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(bundle_dir.parent))
    return out_zip


# ---- Serialization helpers ------------------------------------------------


def _serialize_gap(g: Gap) -> str:
    data = g.model_dump(mode="json")
    return json.dumps(data, default=str)


def _serialize_finding(f: Any) -> str:
    data = f.model_dump(mode="json")
    return json.dumps(data, default=str)


def _serialize_tool(t: Any) -> str:
    data = t.model_dump(mode="json")
    return json.dumps(data, default=str)


def _summary_markdown(
    run_id: str,
    store: GapStore,
    tool_store: ToolStore,
    finding_count: int,
) -> str:
    gaps = store.all_gaps()
    emergent = [g for g in gaps if not g.preset_kind]
    by_status = {
        "unfilled": sum(1 for g in emergent if g.status.value == "unfilled"),
        "filled": sum(1 for g in emergent if g.status.value == "filled"),
        "retired": sum(1 for g in emergent if g.status.value == "retired"),
    }
    by_author: dict[str, int] = {}
    for f in store.all_findings():
        by_author[f.author.value] = by_author.get(f.author.value, 0) + 1
    installed_tools = [t for t in tool_store.all_tools() if t.kind.value == "installed"]
    lines = [
        f"# Drone Graph — run {run_id}",
        "",
        f"Exported at {datetime.now(UTC).isoformat()}.",
        "",
        "## Substrate counts",
        f"- {len(emergent)} emergent gaps "
        f"({by_status['unfilled']} unfilled · {by_status['filled']} filled · "
        f"{by_status['retired']} retired)",
        f"- {finding_count} findings total",
        f"- {len(installed_tools)} drone-installed tools",
        "",
        "## Findings by author",
    ]
    for author, n in sorted(by_author.items(), key=lambda kv: -kv[1]):
        lines.append(f"- **{author}**: {n}")
    if installed_tools:
        lines += ["", "## Drone-installed tools"]
        for t in installed_tools:
            tag = f" (trust={t.trust_tier.value})"
            if t.deprecated_at:
                tag += " [deprecated]"
            lines.append(f"- `{t.name}`{tag} — {t.description[:80]}")
    return "\n".join(lines) + "\n"


# ---- Iteration helpers (unused but exported for future tooling) ----------


def list_runs() -> Iterable[str]:
    """Yield run_ids in mtime-desc order."""
    runs_root = Path("var") / "runs"
    if not runs_root.exists():
        return []
    return [
        d.name[len("mission-control-"):]
        for d in sorted(
            (d for d in runs_root.iterdir() if d.is_dir() and d.name.startswith("mission-control-")),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
    ]
