from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol


class Provider(StrEnum):
    anthropic = "anthropic"
    openai = "openai"


class ChatClient(Protocol):
    def chat(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...


def make_client(provider: Provider, model: str) -> ChatClient:
    raise NotImplementedError("wire anthropic and openai SDK calls here")
