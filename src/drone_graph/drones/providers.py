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
        max_tokens: int | None = None,
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
        max_tokens: int | None = None,
    ) -> ChatResponse:
        attempt = 0
        while True:
            try:
                return cast(ChatResponse, fn(system, messages, tools, max_tokens))
            except Exception as e:  # noqa: BLE001 - filter by transient class below
                if not _is_transient(e) or attempt >= max_retries:
                    raise
                # Exponential backoff with jitter.
                delay = min(cap_s, base_s * (2**attempt))
                delay *= 0.8 + 0.4 * random.random()
                time.sleep(delay)
                attempt += 1

    return _wrapped


# Hardcoded fallback pricing in case the model registry can't be loaded.
# Kept in sync with the registry JSON at packaging time. The registry is the
# source of truth — see ``cost_usd`` below.
#
# WARNING: do not edit these without also updating
# ``src/drone_graph/model_registry/model_registry.json``. Drift between the
# two caused a 3× over-count of Opus turns in the past (we had Opus listed at
# $15/$75 when the actual Anthropic rate for the 4.5+ generation is $5/$25).
_PRICING_FALLBACK: dict[tuple[Provider, str], tuple[float, float]] = {
    (Provider.anthropic, "claude-sonnet-4-6"): (3.0, 15.0),
    (Provider.anthropic, "claude-opus-4-7"): (5.0, 25.0),
    (Provider.anthropic, "claude-opus-4-6"): (5.0, 25.0),
    (Provider.anthropic, "claude-haiku-4-5-20251001"): (1.0, 5.0),
    (Provider.openai, "gpt-4o"): (2.5, 10.0),
    (Provider.openai, "gpt-4o-mini"): (0.15, 0.6),
}


# Cache the registry across calls — loading the JSON for every drone turn
# would be wasteful. ``None`` means "not yet attempted"; an explicit empty
# dict means "registry unavailable, use the fallback table forever."
_REGISTRY_PRICE_CACHE: dict[tuple[Provider, str], tuple[float, float]] | None = None


def _load_pricing() -> dict[tuple[Provider, str], tuple[float, float]]:
    """Build a ``(provider, vendor_model_id) → (in_rate, out_rate)`` table
    from the packaged model registry. Costs are in USD per 1M tokens. The
    registry is authoritative; this is just an indexed mirror so cost
    lookups are O(1)."""
    global _REGISTRY_PRICE_CACHE
    if _REGISTRY_PRICE_CACHE is not None:
        return _REGISTRY_PRICE_CACHE
    try:
        # Lazy import — avoids a cycle (model_registry imports from gaps which
        # is imported here).
        from drone_graph.model_registry.registry import ModelRegistry

        reg = ModelRegistry.load_auto()
        out: dict[tuple[Provider, str], tuple[float, float]] = {}
        for entry in reg._data.models:  # noqa: SLF001 — single internal read
            out[(entry.provider, entry.vendor_model_id)] = (
                entry.input_price_per_million_usd,
                entry.output_price_per_million_usd,
            )
        _REGISTRY_PRICE_CACHE = out
    except Exception:
        _REGISTRY_PRICE_CACHE = {}
    return _REGISTRY_PRICE_CACHE


def cost_usd(provider: Provider, model: str, usage: Usage) -> float:
    """Estimate USD cost for one turn. Reads rates from the model registry
    (authoritative) with a small hardcoded fallback for the case where the
    registry can't be loaded.

    Note: this does not yet model Anthropic's cache-read discount ($0.30/M
    for cached input tokens on Sonnet). The Anthropic API returns
    ``cache_read_input_tokens`` separately in ``usage``; until we wire that
    through the ``Usage`` dataclass, the meter slightly *over*-estimates
    real cost. That's the right way to be wrong (operator sees a safe
    upper bound)."""
    rates = _load_pricing().get((provider, model))
    if rates is None:
        rates = _PRICING_FALLBACK.get((provider, model), (0.0, 0.0))
    in_rate, out_rate = rates
    return (usage.tokens_in * in_rate + usage.tokens_out * out_rate) / 1_000_000


def _annotate_last_message_for_cache(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add a ``cache_control: ephemeral`` annotation to the last content
    block of the last message. Anthropic treats this as a cache breakpoint —
    every prior message + content block becomes part of the cached prefix
    on subsequent calls within the 5-minute cache lifetime.

    On a 20-turn worker this is the largest single cost lever: each turn
    re-sends the full conversation, and without a breakpoint every token
    pays full input rate every call (quadratic growth). With a breakpoint
    on the tail, all prior turns read from cache.
    """
    if not messages:
        return messages
    out: list[dict[str, Any]] = [dict(m) for m in messages]
    last = out[-1]
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content:
        new_blocks = [dict(b) for b in content]
        new_blocks[-1] = {**new_blocks[-1], "cache_control": {"type": "ephemeral"}}
        last["content"] = new_blocks
    return out


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
        max_tokens: int | None = None,
    ) -> ChatResponse:
        # Ephemeral prompt caching: the system prompt, tools list, and prior
        # message history all repeat verbatim every turn within a drone run.
        # Marking them as cache breakpoints converts subsequent reads from
        # full input price ($3/M for Sonnet) to ~$0.30/M — a ~10× reduction
        # on the cached prefix and the dominant cost optimisation for
        # multi-turn agents. Cache lifetime is 5 minutes, which comfortably
        # covers any drone's full turn budget.
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(max_tokens) if max_tokens else 4096,
            "system": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": cast(Any, _annotate_last_message_for_cache(messages)),
        }
        if tools:
            cached_tools = list(tools)
            if cached_tools:
                # Caches the entire tools list up to (and including) the
                # last tool definition. Per Anthropic, the breakpoint
                # caches every block above it.
                cached_tools[-1] = {
                    **cached_tools[-1],
                    "cache_control": {"type": "ephemeral"},
                }
            kwargs["tools"] = cast(Any, cached_tools)
            # Align with run_drone: every turn must invoke at least one tool.
            kwargs["tool_choice"] = cast(Any, {"type": "any"})
        resp = self._client.messages.create(**kwargs)
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
        max_tokens: int | None = None,
    ) -> ChatResponse:
        oai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        oai_messages.extend(drone_messages_to_openai_chat(messages))
        oai_tools = drone_tools_to_openai_functions(tools)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": cast(Any, oai_messages),
            "max_tokens": int(max_tokens) if max_tokens else 4096,
        }
        if oai_tools:
            kwargs["tools"] = cast(Any, oai_tools)
            # run_drone rejects turns with no tool_calls; "auto" lets GPT answer
            # with plain text only — use required when tools exist.
            kwargs["tool_choice"] = "required"
            kwargs["parallel_tool_calls"] = True
        resp = self._client.chat.completions.create(**kwargs)
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
