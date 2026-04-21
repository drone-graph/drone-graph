from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from drone_graph.gaps import Gap
from drone_graph.model_registry.generate import generate_registry_file
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


def _substrate() -> Substrate:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "drone-graph-dev")
    s = Substrate(uri, user, password)
    s.init_schema()
    return s


@app.command("submit-gap")
def submit_gap(description: str) -> None:
    """Insert a hand-written gap into the substrate."""
    substrate = _substrate()
    gap = Gap(description=description)
    substrate.execute_write(
        "CREATE (g:Gap {id: $id, description: $description, status: $status, "
        "model_tier: $model_tier, created_at: datetime($created_at)})",
        id=gap.id,
        description=gap.description,
        status=gap.status.value,
        model_tier=gap.model_tier.value,
        created_at=gap.created_at.isoformat(),
    )
    typer.echo(f"submitted gap {gap.id}")


@app.command("run-orchestrator")
def run_orchestrator() -> None:
    """Start the orchestrator loop."""
    raise NotImplementedError("wire orchestrator.run_forever once the drone runtime is in place")


@app.command("reset-db")
def reset_db() -> None:
    """Delete all nodes. Dev convenience."""
    substrate = _substrate()
    substrate.execute_write("MATCH (n) DETACH DELETE n")
    typer.echo("db reset")


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
