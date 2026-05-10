"""Protocols and constants for embedding providers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# MVP: embed tool name + description; schema allows skill:* scopes later.
SCOPE_DESCRIPTION = "description"


@runtime_checkable
class Embedder(Protocol):
    """Maps text to a dense vector (dimension depends on implementation)."""

    @property
    def model_id(self) -> str: ...

    def embed(self, text: str) -> list[float]:
        """Return embedding; length must be stable for a given model."""
        ...
