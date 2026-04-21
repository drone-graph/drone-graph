from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from drone_graph.drones import Provider
from drone_graph.gaps import Gap, GapStore, ModelTier
from drone_graph.model_registry.generate import generate_registry_file
from drone_graph.orchestrator import EventTape, default_tape_path, run_forever
from drone_graph.substrate import Substrate

_DEFAULT_REGISTRY_OUT = Path("model_registry.json")

_REGISTRY_OUT_OPTION = typer.Option(
    _DEFAULT_REGISTRY_OUT,
    "--output",
    "-o",
    help="Where to write the generated registry JSON (default: model_registry.json).",
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
_DOC_ENRICH_WEB_SEARCH_OPTION = typer.Option(
    None,
    "--doc-enrich-web-search/--no-doc-enrich-web-search",
    help=(
        "Use one hosted web search per model (legacy). Default is cached official "
        "pricing/deprecation pages (no per-model web search). When omitted, "
        "DRONE_GRAPH_DOC_ENRICH_WEB_SEARCH=1 still enables web search."
    ),
)

app = typer.Typer(no_args_is_help=True)
gap_app = typer.Typer(no_args_is_help=True, help="Inspect gaps in the substrate.")
app.add_typer(gap_app, name="gap")


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
    model: str = typer.Option("claude-sonnet-4-6", "--model"),
    provider: Provider = typer.Option(Provider.anthropic, "--provider"),
    poll_interval_s: float = typer.Option(2.0, "--poll-interval"),
) -> None:
    """Start the orchestrator loop."""
    substrate = _substrate()
    tape = EventTape(default_tape_path())
    typer.echo(f"tape: {tape.path}")
    run_forever(
        substrate,
        provider=provider,
        model=model,
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


@app.command("generate-model-registry")
def generate_model_registry(
    output: Path = _REGISTRY_OUT_OPTION,
    verbose: bool = _VERBOSE_OPTION,
    doc_enrich_web_search: bool | None = _DOC_ENRICH_WEB_SEARCH_OPTION,
) -> None:
    """List models, doc enrichment, write JSON. Keys pick enrichment backend.

    **Today:** Default enrichment uses **cached** official vendor docs (HTTP fetch once per
    provider, TTL on disk). Pass ``--doc-enrich-web-search`` for one hosted web search
    per model (legacy). **Future:** same work runs as a **Drone**; web search is a skill.
    See architecture-notes/model-registry.md (section “Current vs future”).
    """
    reg_verbose = verbose or os.environ.get("DRONE_GRAPH_REGISTRY_VERBOSE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    try:
        data = generate_registry_file(
            output=output,
            show_progress=sys.stderr.isatty(),
            verbose=reg_verbose,
            doc_enrich_web_search=doc_enrich_web_search,
        )
    except ValueError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from e

    typer.echo(
        f"wrote {output} ({len(data.models)} models). "
        "Review pricing and tier_defaults before production use."
    )


if __name__ == "__main__":
    app()
