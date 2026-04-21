from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class Provider(StrEnum):
    anthropic = "anthropic"
    openai = "openai"


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class ChatResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    usage: Usage = field(default_factory=Usage)
    raw_assistant_content: list[dict[str, Any]] = field(default_factory=list)


class ChatClient(Protocol):
    provider: Provider
    model: str

    def chat(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatResponse: ...


# USD per 1M tokens (input, output). Update as pricing changes.
_PRICING: dict[tuple[Provider, str], tuple[float, float]] = {
    (Provider.anthropic, "claude-sonnet-4-6"): (3.0, 15.0),
    (Provider.anthropic, "claude-opus-4-7"): (15.0, 75.0),
    (Provider.anthropic, "claude-haiku-4-5-20251001"): (1.0, 5.0),
}


def cost_usd(provider: Provider, model: str, usage: Usage) -> float:
    in_rate, out_rate = _PRICING.get((provider, model), (0.0, 0.0))
    return (usage.tokens_in * in_rate + usage.tokens_out * out_rate) / 1_000_000


class AnthropicClient:
    provider = Provider.anthropic

    def __init__(self, model: str) -> None:
        from anthropic import Anthropic

        self.model = model
        self._client = Anthropic()

    def chat(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatResponse:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=messages,
            tools=tools,
        )
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_content: list[dict[str, Any]] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
                raw_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=dict(block.input))
                )
                raw_content.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": dict(block.input),
                    }
                )
        return ChatResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason or "",
            usage=Usage(
                tokens_in=resp.usage.input_tokens,
                tokens_out=resp.usage.output_tokens,
            ),
            raw_assistant_content=raw_content,
        )


def make_client(provider: Provider, model: str) -> ChatClient:
    if provider is Provider.anthropic:
        return AnthropicClient(model)
    raise NotImplementedError(f"provider {provider} not wired yet")
