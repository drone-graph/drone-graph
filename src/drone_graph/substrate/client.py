from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from neo4j import Driver, GraphDatabase, NotificationDisabledClassification, Session

from drone_graph.substrate.schema import SCHEMA_STATEMENTS


class Substrate:
    def __init__(self, uri: str, user: str, password: str) -> None:
        # UNRECOGNIZED covers "relationship/label/property does not exist" warnings
        # that fire when querying against a fresh graph. They are harmless — the
        # queries still execute — and would swamp real diagnostics.
        self._driver: Driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            notifications_disabled_classifications=[
                NotificationDisabledClassification.UNRECOGNIZED,
            ],
        )

    def close(self) -> None:
        self._driver.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._driver.session() as session:
            yield session

    def init_schema(self) -> None:
        with self.session() as session:
            for stmt in SCHEMA_STATEMENTS:
                session.run(stmt)

    def execute_read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        with self.session() as session:
            result = session.run(cypher, **params)
            return [record.data() for record in result]

    def execute_write(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        with self.session() as session:
            result = session.run(cypher, **params)
            return [record.data() for record in result]
