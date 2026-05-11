"""FastAPI mission-control surface over the live substrate.

The API is a thin, read-mostly window onto:
  - ``GapStore`` (gaps + findings in Neo4j)
  - ``ToolStore`` (tool registry)
  - ``SQLiteSignalStore`` (claims, leases, install registry, token bucket,
    swarm cost meter)
  - a ``SwarmController`` that owns one long-lived ``Scheduler`` thread and
    exposes pause/resume/ceiling/paranoid-install controls.

The server tails the scheduler event tape and re-broadcasts events over SSE
so the frontend can render the substrate breathing in near-realtime.
"""

from drone_graph.api.app import build_app, serve

__all__ = ["build_app", "serve"]
