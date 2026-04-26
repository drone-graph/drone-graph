from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, cast


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


# Transient error classes that are worth retrying. Kept as a name list so we
# don't hard-import provider SDKs at module load — some environments skip
# installing openai/anthropic transitively.
_TRANSIENT_ERROR_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "APIStatusError",  # covers transient 5xx under the SDK
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "ConnectError",
        "ReadTimeout",
        "ConnectTimeout",
        "ConnectionError",
    }
)

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_S = 1.5  # 1.5, 3, 6 ... seconds
DEFAULT_BACKOFF_CAP_S = 30.0


def _is_transient(exc: BaseException) -> bool:
    return type(exc).__name__ in _TRANSIENT_ERROR_NAMES


def _retryable_chat(
    fn: Any,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_s: float = DEFAULT_BACKOFF_BASE_S,
    cap_s: float = DEFAULT_BACKOFF_CAP_S,
) -> Any:
    """Wrap a ``chat(...)`` bound method with bounded exponential backoff.

    Only transient network / rate-limit errors are retried. Every other
    exception propagates unchanged so bugs aren't swallowed.
    """

    def _wrapped(
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatResponse:
        attempt = 0
        while True:
            try:
                return cast(ChatResponse, fn(system, messages, tools))
            except Exception as e:  # noqa: BLE001 - filter by transient class below
                if not _is_transient(e) or attempt >= max_retries:
                    raise
                # Exponential backoff with jitter.
                delay = min(cap_s, base_s * (2**attempt))
                delay *= 0.8 + 0.4 * random.random()
                time.sleep(delay)
                attempt += 1

    return _wrapped


# USD per 1M tokens (input, output). Update as pricing changes.
_PRICING: dict[tuple[Provider, str], tuple[float, float]] = {
    (Provider.anthropic, "claude-sonnet-4-6"): (3.0, 15.0),
    (Provider.anthropic, "claude-opus-4-7"): (15.0, 75.0),
    (Provider.anthropic, "claude-haiku-4-5-20251001"): (1.0, 5.0),
    (Provider.openai, "gpt-4o"): (2.5, 10.0),
    (Provider.openai, "gpt-4o-mini"): (0.15, 0.6),
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
            messages=cast(Any, messages),
            tools=cast(Any, tools),
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


def drone_messages_to_openai_chat(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn Anthropic-shaped runtime messages into OpenAI ``chat.completions`` messages."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
            elif isinstance(content, list):
                for block in content:
                    btype = block.get("type")
                    if btype == "tool_result":
                        out.append(
                            {
                                "role": "tool",
                                "tool_call_id": str(block["tool_use_id"]),
                                "content": str(block["content"]),
                            }
                        )
                    elif btype == "text":
                        out.append({"role": "user", "content": str(block["text"])})
                    else:
                        raise ValueError(f"unknown user content block type: {btype!r}")
            else:
                raise TypeError(f"unexpected user content type: {type(content)}")
        elif role == "assistant":
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                text_parts: list[str] = []
                oai_tool_calls: list[dict[str, Any]] = []
                for block in content:
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(str(block.get("text", "")))
                    elif btype == "tool_use":
                        tid = str(block["id"])
                        name = str(block["name"])
                        inp = block.get("input", {})
                        oai_tool_calls.append(
                            {
                                "id": tid,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(inp),
                                },
                            }
                        )
                    else:
                        raise ValueError(f"unknown assistant content block type: {btype!r}")
                text_joined = "".join(text_parts)
                entry: dict[str, Any] = {"role": "assistant"}
                if text_joined:
                    entry["content"] = text_joined
                if oai_tool_calls:
                    entry["tool_calls"] = oai_tool_calls
                if "tool_calls" in entry and "content" not in entry:
                    entry["content"] = None
                if "content" not in entry and "tool_calls" not in entry:
                    entry["content"] = ""
                out.append(entry)
            else:
                raise TypeError(f"unexpected assistant content type: {type(content)}")
        else:
            raise ValueError(f"unknown message role: {role!r}")
    return out


def drone_tools_to_openai_functions(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map runtime tool defs (Anthropic ``input_schema``) to OpenAI function tools."""
    out: list[dict[str, Any]] = []
    for spec in tools:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec.get("description", ""),
                    "parameters": spec.get(
                        "input_schema",
                        {"type": "object", "properties": {}},
                    ),
                },
            }
        )
    return out


class OpenAIClient:
    provider = Provider.openai

    def __init__(self, model: str) -> None:
        from openai import OpenAI

        self.model = model
        self._client = OpenAI()

    def chat(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatResponse:
        oai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        oai_messages.extend(drone_messages_to_openai_chat(messages))
        oai_tools = drone_tools_to_openai_functions(tools)
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=cast(Any, oai_messages),
            tools=cast(Any, oai_tools),
            tool_choice="auto",
            parallel_tool_calls=True,
            max_tokens=4096,
        )
        choice = resp.choices[0]
        msg = choice.message
        text = msg.content or ""
        raw_content: list[dict[str, Any]] = []
        tool_calls: list[ToolCall] = []
        if text:
            raw_content.append({"type": "text", "text": text})
        for tc_raw in msg.tool_calls or []:
            tc = cast(Any, tc_raw)
            fn = tc.function
            if fn is None:
                continue
            raw_args = fn.arguments or "{}"
            try:
                parsed: dict[str, Any] = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
            name = str(fn.name)
            tid = str(tc.id)
            tool_calls.append(ToolCall(id=tid, name=name, input=parsed))
            raw_content.append(
                {
                    "type": "tool_use",
                    "id": tid,
                    "name": name,
                    "input": parsed,
                },
            )
        usage_obj = resp.usage
        tokens_in = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage_obj, "completion_tokens", 0) or 0)
        return ChatResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "",
            usage=Usage(tokens_in=tokens_in, tokens_out=tokens_out),
            raw_assistant_content=raw_content,
        )


def resolve_orchestrator_provider_model(
    provider: Provider | None,
    model: str | None,
) -> tuple[Provider, str]:
    """Pick provider/model for the orchestrator when CLI omits them.

    If ``provider`` is ``None``: use Anthropic when only that key is set, OpenAI when
    only that key is set, and Anthropic when both are set. If ``model`` is ``None``,
    use a sensible default for the chosen provider.
    """
    akey = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    okey = os.environ.get("OPENAI_API_KEY", "").strip()
    has_a = bool(akey)
    has_o = bool(okey)

    if provider is None:
        if has_a and not has_o:
            chosen = Provider.anthropic
        elif has_o and not has_a:
            chosen = Provider.openai
        elif has_a and has_o:
            chosen = Provider.anthropic
        elif has_o:
            chosen = Provider.openai
        elif has_a:
            chosen = Provider.anthropic
        else:
            raise ValueError(
                "No API key found: set ANTHROPIC_API_KEY and/or OPENAI_API_KEY "
                "(or pass --provider explicitly once a key is set).",
            )
    else:
        chosen = provider

    if model is None:
        defaults = {
            Provider.anthropic: "claude-sonnet-4-6",
            Provider.openai: "gpt-4o",
        }
        resolved_model = defaults[chosen]
    else:
        resolved_model = model
    return chosen, resolved_model


def make_client(provider: Provider, model: str) -> ChatClient:
    client: ChatClient
    if provider is Provider.anthropic:
        client = AnthropicClient(model)
    elif provider is Provider.openai:
        client = OpenAIClient(model)
    else:
        raise NotImplementedError(f"provider {provider} not wired yet")
    # Wrap the raw chat() method in bounded exponential backoff for transient
    # network / rate-limit errors. Non-transient exceptions still propagate.
    client.chat = _retryable_chat(client.chat)  # type: ignore[method-assign]
    return client
