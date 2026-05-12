from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from drone_graph.substrate import Substrate


@pytest.fixture
def substrate() -> Iterator[Substrate]:
    """Real Neo4j substrate, wiped before AND after each test.

    Two protections so the live mission-control DB doesn't get
    trashed by an accidental ``pytest`` run from a dev machine:

    1. **Live-session detection.** If the DB shows signs of a real
       session — any ``:Persona`` node (minted by
       ``init_collective_mind``) — skip the test. Set
       ``DRONE_GRAPH_TESTS_ALLOW_WIPE=1`` to override (CI / known-empty
       test DB).
    2. **Symmetric wipe.** Even when the gate lets us through, we wipe
       both before AND after the test so we never leave fixture data
       (e.g. a Gap with ``intent="a"``) lying around for the next
       ``./start`` to render as a "what is this???" mystery.
    """
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "drone-graph-dev")
    try:
        s = Substrate(uri, user, password)
        s.init_schema()
    except Exception as e:
        pytest.skip(f"neo4j not available: {e}")
    try:
        if os.environ.get("DRONE_GRAPH_TESTS_ALLOW_WIPE") != "1":
            try:
                rows = s.execute_read("MATCH (p:Persona) RETURN count(p) AS c")
                persona_count = int(rows[0]["c"]) if rows else 0
            except Exception:
                persona_count = 0
            if persona_count > 0:
                pytest.skip(
                    "live drone-graph substrate detected on this Neo4j "
                    f"({persona_count} Persona node(s) — likely a real "
                    "swarm session). Refusing to wipe. Stop the running "
                    "mission-control server, point NEO4J_URI at a separate "
                    "test database, or set "
                    "DRONE_GRAPH_TESTS_ALLOW_WIPE=1 to override."
                )
        s.execute_write("MATCH (n) DETACH DELETE n")
        try:
            yield s
        finally:
            # Wipe AFTER too — don't leave fixture data in the DB. If
            # the operator later starts a real session against this
            # Neo4j, init_collective_mind will mint presets + personas
            # cleanly with no stray "a" gap to confuse the canvas.
            try:
                s.execute_write("MATCH (n) DETACH DELETE n")
            except Exception:
                pass
    finally:
        s.close()
