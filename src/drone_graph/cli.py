from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv

from drone_graph.drones import Provider, resolve_orchestrator_provider_model
from drone_graph.gaps import Gap, GapStore, ModelTier
from drone_graph.model_registry.generate import (
    default_packaged_registry_json_path,
    generate_registry_file,
    sync_registry_file,
    update_registry_file,
)
from drone_graph.orchestrator import EventTape, default_tape_path, run_forever
from drone_graph.substrate import Substrate

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
gap_app = typer.Typer(no_args_is_help=True, help="Inspect gaps in the substrate.")
app.add_typer(gap_app, name="gap")

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
    return output if output is not None else default_packaged_registry_json_path()


def _run_registry_fresh(
    output: Path | None,
    *,
    verbose: bool,
) -> None:
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
    s = Substrate(uri, user, password)
    s.init_schema()
    return s


@app.command("submit-gap")
def submit_gap(
    description: str,
    nl_criteria: str | None = typer.Option(None, "--criteria"),
    blocked_by: list[str] = typer.Option(
        [],
        "--blocked-by",
        help="Gap id this new gap depends on. Repeatable.",
    ),
    tier: ModelTier = typer.Option(ModelTier.standard, "--tier"),
) -> None:
    """Insert a hand-written gap into the substrate."""
    substrate = _substrate()
    store = GapStore(substrate)
    gap = Gap(description=description, nl_criteria=nl_criteria, model_tier=tier)
    store.create(gap, blocked_by=list(blocked_by) or None)
    typer.echo(f"submitted gap {gap.id}")
    if blocked_by:
        typer.echo(f"  blocked_by: {', '.join(blocked_by)}")


@app.command("run-orchestrator")
def run_orchestrator(
    model: str | None = typer.Option(
        None,
        "--model",
        help="Vendor model id (default: claude-sonnet-4-6 or gpt-4o from --provider).",
    ),
    provider: Provider | None = typer.Option(
        None,
        "--provider",
        help=(
            "LLM provider. If omitted: only Anthropic key → anthropic; only OpenAI key → "
            "openai; both keys → anthropic."
        ),
    ),
    poll_interval_s: float = typer.Option(2.0, "--poll-interval"),
) -> None:
    """Start the orchestrator loop."""
    try:
        resolved_provider, resolved_model = resolve_orchestrator_provider_model(
            provider,
            model,
        )
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    substrate = _substrate()
    tape = EventTape(default_tape_path())
    typer.echo(f"tape: {tape.path}")
    typer.echo(f"provider: {resolved_provider.value}  model: {resolved_model}")
    run_forever(
        substrate,
        provider=resolved_provider,
        model=resolved_model,
        poll_interval_s=poll_interval_s,
        tape=tape,
    )


@app.command("reset-db")
def reset_db() -> None:
    """Delete all nodes. Dev convenience."""
    substrate = _substrate()
    substrate.execute_write("MATCH (n) DETACH DELETE n")
    typer.echo("db reset")


@gap_app.command("list")
def gap_list(
    status: str | None = typer.Option(None, "--status", help="Filter by status."),
    tier: ModelTier | None = typer.Option(None, "--tier"),
) -> None:
    """List gaps, newest last."""
    store = GapStore(_substrate())
    gaps = store.by_tier(tier) if tier is not None else store.all_gaps()
    if status is not None:
        gaps = [g for g in gaps if g.status.value == status]
    if not gaps:
        typer.echo("(no gaps)")
        return
    for g in gaps:
        line = f"{g.id[:8]}  {g.status.value:<11}  a={g.attempts}  {g.description}"
        if g.failure_reason:
            line += f"  [reason: {g.failure_reason}]"
        typer.echo(line)


@gap_app.command("show")
def gap_show(gap_id: str) -> None:
    """Show a single gap in detail, including blockers."""
    store = GapStore(_substrate())
    gap = store.get(gap_id)
    if gap is None:
        typer.echo(f"no such gap: {gap_id}")
        raise typer.Exit(code=1)
    typer.echo(f"id:           {gap.id}")
    typer.echo(f"description:  {gap.description}")
    typer.echo(f"status:       {gap.status.value}")
    typer.echo(f"tier:         {gap.model_tier.value}")
    typer.echo(f"attempts:     {gap.attempts}")
    typer.echo(f"created_at:   {gap.created_at.isoformat()}")
    if gap.in_progress_at:
        typer.echo(f"started_at:   {gap.in_progress_at.isoformat()}")
    if gap.closed_at:
        typer.echo(f"closed_at:    {gap.closed_at.isoformat()}")
    if gap.failed_at:
        typer.echo(f"failed_at:    {gap.failed_at.isoformat()}")
    if gap.failure_reason:
        typer.echo(f"reason:       {gap.failure_reason}")
    blockers = store.blockers_of(gap.id)
    if blockers:
        typer.echo(f"blocked_by:   {', '.join(blockers)}")


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


def main() -> None:
    """Console entry: load repo-root ``.env`` then run Typer."""
    load_dotenv()
    app()


if __name__ == "__main__":
    main()
