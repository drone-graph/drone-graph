from __future__ import annotations

import os

import typer

from drone_graph.gaps import Gap
from drone_graph.substrate import Substrate

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


if __name__ == "__main__":
    app()
