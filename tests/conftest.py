from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from drone_graph.substrate import Substrate


@pytest.fixture
def substrate() -> Iterator[Substrate]:
    """Real Neo4j substrate, wiped before each test. Skipped if Neo4j is
    unreachable OR if the database appears to hold a live swarm session
    (any ``:Persona`` node — these are minted by ``init_collective_mind``
    only when the real server boots, so their presence is a strong
    signal that wiping would destroy the operator's work).

    Override the safety check by setting ``DRONE_GRAPH_TESTS_ALLOW_WIPE=1``
    in the env — CI runs against a dedicated Neo4j and is happy to wipe.
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
            # Heuristic: any Persona node means init_collective_mind ran,
            # which means this is a real session. Refuse to wipe.
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
        yield s
    finally:
        s.close()
