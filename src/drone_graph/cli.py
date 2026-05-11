from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv

from drone_graph.drones import (
    Provider,
    make_client,
    resolve_orchestrator_provider_model,
    run_drone,
)
from drone_graph.gaps import GapStore
from drone_graph.orchestrator.tape import EventTape
from drone_graph.substrate import Substrate

# model_registry.generate is imported lazily inside the model-registry
# subcommands: the import chain (doc_enrich → openai_docs_crawl → crawl4ai)
# has heavy optional dependencies that are only needed when actually running
# registry enrichment. Keeping them out of top-level imports lets `gap list`,
# `reset-db`, etc. work even when crawl4ai is not installed.

_REGISTRY_OUT_OPTION = typer.Option(
    None,
    "--output",
    "-o",
    help=(
        "Registry JSON path (default: packaged "
        "src/drone_graph/model_registry/model_registry.json)."
    ),
)
_VERBOSE_OPTION = typer.Option(
    False,
    "--verbose",
    "-v",
    help=(
        "Log enrichment: request payload, each web search query/URL/hit, assistant text previews, "
        "then full API JSON. Or set DRONE_GRAPH_REGISTRY_VERBOSE=1 in the environment."
    ),
)

app = typer.Typer(no_args_is_help=True)
gap_app = typer.Typer(no_args_is_help=True, help="Inspect or seed gaps in the substrate.")
app.add_typer(gap_app, name="gap")

finding_app = typer.Typer(
    no_args_is_help=True, help="Inspect findings recorded in the substrate."
)
app.add_typer(finding_app, name="finding")

drone_app = typer.Typer(
    no_args_is_help=True,
    help="Run a worker drone against a single gap (experiment harness).",
)
app.add_typer(drone_app, name="drone")

tools_cli = typer.Typer(
    no_args_is_help=True,
    help="Tool registry maintenance (soft deprecation, stale pruning).",
)
app.add_typer(tools_cli, name="tools")

registry_app = typer.Typer(
    no_args_is_help=True,
    help="Build or refresh packaged model_registry.json (vendor list + doc enrichment).",
)
app.add_typer(registry_app, name="model-registry")


def _registry_verbose(verbose: bool) -> bool:
    return verbose or os.environ.get("DRONE_GRAPH_REGISTRY_VERBOSE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _resolved_registry_output(output: Path | None) -> Path:
    from drone_graph.model_registry.generate import default_packaged_registry_json_path

    return output if output is not None else default_packaged_registry_json_path()


def _run_registry_fresh(
    output: Path | None,
    *,
    verbose: bool,
) -> None:
    from drone_graph.model_registry.generate import generate_registry_file

    out = _resolved_registry_output(output)
    reg_verbose = _registry_verbose(verbose)
    try:
        data = generate_registry_file(
            output=out,
            show_progress=sys.stderr.isatty(),
            verbose=reg_verbose,
        )
    except ValueError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from e

    typer.echo(
        f"wrote {out} ({len(data.models)} models). "
        "Review pricing and tier_defaults before production use."
    )


def _substrate() -> Substrate:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "drone-graph-dev")
    return Substrate(uri, user, password)


def _bootstrap() -> Substrate:
    """Substrate + collective-mind init (schema + builtins + preset gaps)."""
    from drone_graph.orchestrator import init_collective_mind

    s = _substrate()
    init_collective_mind(s)
    return s


@app.command("export-run")
def export_run_cmd(
    run_id: str | None = typer.Argument(
        None,
        help=(
            "Mission-control run id to export (the suffix after "
            "``mission-control-`` in var/runs/). Omit to use the latest."
        ),
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory or zip path. Default: var/exports/run-<run_id>-<ts>/",
    ),
    zip_bundle: bool = typer.Option(
        False,
        "--zip",
        help="Also zip the bundle to <out>.zip.",
    ),
    skip_artefacts: bool = typer.Option(
        False,
        "--skip-artefacts",
        help="Skip copying artefact files referenced by finding.artefact_paths.",
    ),
) -> None:
    """Bundle a swarm session into a self-contained directory for review.

    Snapshots the substrate (gaps, findings, tools, edges), copies the
    scheduler tape + per-drone tapes, and best-effort copies any artefacts
    the findings reference. Writes ``summary.md``.

    Run while the swarm is active or after it's stopped — the exporter only
    reads.
    """
    from drone_graph import exporter as ex

    target_run = run_id or ex.find_latest_run_id()
    if target_run is None:
        typer.secho(
            "no mission-control runs found under var/runs/",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    if out is None:
        from datetime import UTC, datetime

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = Path("var") / "exports" / f"run-{target_run}-{ts}"

    substrate = _substrate()
    bundle = ex.export_run(
        run_id=target_run,
        out_dir=out,
        include_artefacts=not skip_artefacts,
        substrate=substrate,
    )
    typer.echo(f"exported run {target_run} → {bundle}")
    if zip_bundle:
        zip_path = bundle.with_suffix(".zip")
        ex.zip_bundle(bundle, zip_path)
        typer.echo(f"zipped → {zip_path}")


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (local-only by default)."),
    port: int = typer.Option(8765, "--port", help="Bind port."),
    provider: str | None = typer.Option(
        None, "--provider", help="anthropic|openai (defaults to whatever key is set)."
    ),
    model: str | None = typer.Option(
        None, "--model", help="Vendor model id (default: claude-sonnet-4-6 or gpt-4o)."
    ),
    cost_ceiling_usd: float | None = typer.Option(
        None, "--cost-ceiling-usd", help="Swarm cost ceiling at startup; adjustable from the UI.",
    ),
    signal_db: Path | None = typer.Option(
        None, "--signal-db", help="Sidecar SQLite path (default: var/signals.db).",
    ),
    reload: bool = typer.Option(
        False, "--reload", help="Use uvicorn's reload mode (for backend dev).",
    ),
    skip_bringup: bool = typer.Option(
        False,
        "--skip-bringup",
        help="Skip auto-build of the frontend and Neo4j bringup checks.",
    ),
) -> None:
    """Run the mission-control web server (FastAPI + SSE + frontend).

    Local-only by default. Talks to the same Neo4j + sidecar SQLite that the
    CLI does. Opens the persistent substrate as an infinite-mode swarm,
    paused until the operator sends a first prompt from the UI.

    By default this is a one-command boot: it auto-builds the frontend if
    needed, brings Neo4j up via ``docker compose`` if the bolt port isn't
    answering, and tolerates a missing provider key (you enter it in the
    UI Settings panel). Pass ``--skip-bringup`` for fast restarts after
    you've already done the bringup once.
    """
    from drone_graph.api import serve

    serve(
        host=host,
        port=port,
        provider=provider,
        model=model,
        cost_ceiling_usd=cost_ceiling_usd,
        signal_db=signal_db,
        reload=reload,
        skip_bringup=skip_bringup,
    )


@app.command("reset-db")
def reset_db() -> None:
    """Delete all nodes, then re-mint preset gaps + builtin tools. Dev convenience."""
    from drone_graph.orchestrator import init_collective_mind

    substrate = _substrate()
    substrate.execute_write("MATCH (n) DETACH DELETE n")
    init_collective_mind(substrate)
    typer.echo("db reset (preset gaps + tool registry re-minted)")


@app.command("reset-signals")
def reset_signals(
    db_path: Path = typer.Option(
        None,
        "--signal-db",
        help="Sidecar SQLite path (default: var/signals.db).",
    ),
) -> None:
    """Wipe the Phase 3 sidecar (claims, leases, install registry, buckets)."""
    from drone_graph.signals import SQLiteSignalStore, default_db_path

    target = db_path if db_path is not None else default_db_path()
    store = SQLiteSignalStore(target)
    try:
        store.reset_all()
    finally:
        store.close()
    typer.echo(f"signals reset ({target})")


@gap_app.command("list")
def gap_list(
    status: str | None = typer.Option(
        None, "--status", help="Filter by status (unfilled|filled|retired)."
    ),
) -> None:
    """List gaps in the current substrate, oldest first."""
    store = GapStore(_substrate())
    gaps = store.all_gaps()
    if status is not None:
        gaps = [g for g in gaps if g.status.value == status]
    if not gaps:
        typer.echo("(no gaps)")
        return
    for g in gaps:
        intent = g.intent.replace("\n", " ")
        if len(intent) > 140:
            intent = intent[:137] + "…"
        preset_tag = f"[{g.preset_kind}] " if g.preset_kind else ""
        line = (
            f"{g.id[:8]}  {g.status.value:<8}  r={g.reopen_count}  "
            f"{preset_tag}{intent}"
        )
        if g.retire_reason:
            line += f"  [retired: {g.retire_reason}]"
        typer.echo(line)


@gap_app.command("show")
def gap_show(gap_id: str) -> None:
    """Show a single gap in detail, including parent and children."""
    store = GapStore(_substrate())
    gap = store.get(gap_id)
    if gap is None:
        typer.echo(f"no such gap: {gap_id}")
        raise typer.Exit(code=1)
    parent = store.parent_of(gap.id)
    children = store.children_of(gap.id)
    typer.echo(f"id:           {gap.id}")
    typer.echo(f"intent:       {gap.intent}")
    typer.echo(f"criteria:     {gap.criteria}")
    typer.echo(f"status:       {gap.status.value}")
    typer.echo(f"tier:         {gap.model_tier.value}")
    typer.echo(f"reopen_count: {gap.reopen_count}")
    typer.echo(f"created_at:   {gap.created_at.isoformat()}")
    if gap.retire_reason:
        typer.echo(f"retire_reason:{gap.retire_reason}")
    if parent is not None:
        typer.echo(f"parent:       {parent.id}  ({parent.intent[:60]})")
    if children:
        typer.echo("children:")
        for c in children:
            typer.echo(f"  - [{c.status.value}] {c.id[:8]}  {c.intent[:80]}")


@gap_app.command("tree")
def gap_tree() -> None:
    """Render the current gap tree as ASCII, with status and trimmed intent per node."""
    from drone_graph.orchestrator.rendering import render_tree

    store = GapStore(_substrate())
    typer.echo(render_tree(store))


@finding_app.command("list")
def finding_list(
    limit: int = typer.Option(
        30, "--limit", "-n", help="Max findings to show (oldest first)."
    ),
    author: str | None = typer.Option(
        None,
        "--author",
        help="Filter by author (gap_finding|alignment|worker|user|system).",
    ),
    kind: str | None = typer.Option(None, "--kind", help="Filter by finding kind."),
) -> None:
    """List findings in tick order. Handy for drones orienting in their shell."""
    store = GapStore(_substrate())
    findings = store.all_findings()
    if author is not None:
        findings = [f for f in findings if f.author.value == author]
    if kind is not None:
        findings = [f for f in findings if f.kind.value == kind]
    if limit > 0:
        findings = findings[-limit:]
    if not findings:
        typer.echo("(no findings)")
        return
    for f in findings:
        summary = f.summary.replace("\n", " ")
        if len(summary) > 100:
            summary = summary[:97] + "..."
        typer.echo(
            f"{f.id[:8]}  tick={f.tick:<3}  {f.author.value:<11}  "
            f"{f.kind.value:<28}  {summary}"
        )


@finding_app.command("show")
def finding_show(finding_id: str) -> None:
    """Show a single finding in full, including affected gaps."""
    store = GapStore(_substrate())
    matches = [f for f in store.all_findings() if f.id.startswith(finding_id)]
    if not matches:
        typer.secho(f"no finding with id starting {finding_id!r}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if len(matches) > 1:
        typer.secho(
            f"ambiguous id {finding_id!r}: {len(matches)} matches",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    f = matches[0]
    typer.echo(f"id:         {f.id}")
    typer.echo(f"tick:       {f.tick}")
    typer.echo(f"author:     {f.author.value}")
    typer.echo(f"kind:       {f.kind.value}")
    typer.echo(f"created_at: {f.created_at.isoformat()}")
    typer.echo(f"affected:   {', '.join(f.affected_gap_ids) or '(none)'}")
    if f.artefact_paths:
        typer.echo("artefacts:")
        for p in f.artefact_paths:
            typer.echo(f"  - {p}")
    typer.echo("")
    typer.echo(f.summary)


@gap_app.command("create")
def gap_create(
    intent: str = typer.Option(..., "--intent", help="What must be true when the gap is closed."),
    criteria: str = typer.Option(..., "--criteria", help="Acceptance criteria the drone can check."),
) -> None:
    """Seed a synthetic root gap and print its id. Useful for experiments."""
    store = GapStore(_substrate())
    gap = store.create_root(intent=intent, criteria=criteria)
    typer.echo(gap.id)


@drone_app.command("run")
def drone_run(
    gap_id: str | None = typer.Argument(
        None,
        help="Gap id to work on. Omit to auto-pick the oldest active leaf.",
    ),
    provider: str | None = typer.Option(
        None, "--provider", help="anthropic|openai (defaults to whatever key is set)."
    ),
    model: str | None = typer.Option(
        None, "--model", help="Vendor model id (default: claude-sonnet-4-6 or gpt-4o)."
    ),
    max_turns: int = typer.Option(20, "--max-turns", help="Hard cap on model turns."),
    command_timeout: float = typer.Option(
        60.0, "--command-timeout", help="Per terminal command timeout, seconds."
    ),
    tick: int = typer.Option(
        0, "--tick", help="Tick value stamped on any finding the drone writes."
    ),
    tape_path: Path | None = typer.Option(
        None, "--tape", help="JSONL tape file for drone lifecycle events."
    ),
) -> None:
    """Spawn one drone against a gap (preset or emergent) and print the outcome."""
    from drone_graph.tools import ToolStore

    substrate = _bootstrap()
    store = GapStore(substrate)
    tool_store = ToolStore(substrate)

    if gap_id is None:
        leaves = store.leaves()
        if not leaves:
            typer.secho("no active leaves to work on", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
        target = leaves[0]
        typer.echo(f"auto-picked leaf {target.id}: {target.intent[:80]}")
    else:
        target = store.get(gap_id)
        if target is None:
            typer.secho(f"no such gap: {gap_id}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)

    prov = Provider(provider) if provider else None
    resolved_provider, resolved_model = resolve_orchestrator_provider_model(prov, model)
    client = make_client(resolved_provider, resolved_model)

    tape = EventTape(tape_path) if tape_path is not None else None

    typer.echo(f"provider={resolved_provider.value} model={resolved_model} gap={target.id}")
    result = run_drone(
        target,
        store=store,
        tool_store=tool_store,
        client=client,
        tick=tick,
        max_turns=max_turns,
        command_timeout_s=command_timeout,
        tape=tape,
    )

    typer.echo("")
    typer.echo(f"outcome:     {result.outcome}")
    typer.echo(f"drone_id:    {result.drone_id}")
    typer.echo(f"finding_id:  {result.finding_id}")
    typer.echo(f"turns_used:  {result.turns_used}/{max_turns}")
    typer.echo(f"tokens:      in={result.tokens_in}  out={result.tokens_out}")
    typer.echo(f"cost_usd:    ${result.cost_usd:.4f}")
    if result.error:
        typer.echo(f"error:       {result.error}")
    if tape_path is not None:
        typer.echo(f"tape:        {tape_path}")


@registry_app.command("fresh")
def model_registry_fresh(
    output: Path | None = _REGISTRY_OUT_OPTION,
    verbose: bool = _VERBOSE_OPTION,
) -> None:
    """List vendor models from APIs, enrich from docs, overwrite registry JSON (clean build)."""
    _run_registry_fresh(output, verbose=verbose)


@registry_app.command("update")
def model_registry_update(
    output: Path | None = _REGISTRY_OUT_OPTION,
    verbose: bool = _VERBOSE_OPTION,
) -> None:
    """Re-run doc enrichment only on the current registry file (no vendor list refetch)."""
    from drone_graph.model_registry.generate import update_registry_file

    out = _resolved_registry_output(output)
    reg_verbose = _registry_verbose(verbose)
    try:
        data = update_registry_file(
            output=out,
            show_progress=sys.stderr.isatty(),
            verbose=reg_verbose,
        )
    except ValueError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from e

    typer.echo(f"updated {out} ({len(data.models)} models).")


@registry_app.command("sync")
def model_registry_sync(
    output: Path | None = _REGISTRY_OUT_OPTION,
    verbose: bool = _VERBOSE_OPTION,
) -> None:
    """Add newly discovered vendor models, then enrich the full merged list."""
    from drone_graph.model_registry.generate import sync_registry_file

    out = _resolved_registry_output(output)
    reg_verbose = _registry_verbose(verbose)
    try:
        data = sync_registry_file(
            output=out,
            show_progress=sys.stderr.isatty(),
            verbose=reg_verbose,
        )
    except ValueError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from e

    typer.echo(f"synced {out} ({len(data.models)} models).")


@tools_cli.command("deprecate-stale")
def tools_deprecate_stale(
    max_age_days: float = typer.Option(
        90.0,
        "--max-age-days",
        help="Installed tools idle longer than this (by last_used_at) are stale.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="List candidates without writing deprecated_at.",
    ),
    deprecate_flagged: bool = typer.Option(
        True,
        "--flagged/--no-flagged",
        help="Also deprecate alignment-flagged installed tools (default: on).",
    ),
) -> None:
    """Soft-deprecate stale or flagged installed tools (same logic as cm_deprecate_stale_tools)."""
    from drone_graph.tools import ToolStore

    substrate = _bootstrap()
    tool_store = ToolStore(substrate)
    report = tool_store.deprecate_stale_installed_tools(
        max_age_days=max_age_days,
        deprecate_flagged=deprecate_flagged,
        dry_run=dry_run,
    )
    typer.echo(json.dumps(report, indent=2, default=str))


def main() -> None:
    """Console entry: load repo-root ``.env`` then run Typer."""
    load_dotenv()
    app()


if __name__ == "__main__":
    main()
