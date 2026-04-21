from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from drone_graph.substrate import Substrate


@pytest.fixture
def substrate() -> Iterator[Substrate]:
    """Real Neo4j substrate, wiped before each test. Skipped if Neo4j is unreachable."""
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "drone-graph-dev")
    try:
        s = Substrate(uri, user, password)
        s.init_schema()
    except Exception as e:
        pytest.skip(f"neo4j not available: {e}")
    try:
        s.execute_write("MATCH (n) DETACH DELETE n")
        yield s
    finally:
        s.close()
