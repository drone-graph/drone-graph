from __future__ import annotations

import pytest

from drone_graph.drones.providers import (
    Provider,
    drone_messages_to_openai_chat,
    drone_tools_to_openai_functions,
    make_client,
    resolve_orchestrator_provider_model,
)


def test_drone_messages_to_openai_chat_basic() -> None:
    msgs = [
        {"role": "user", "content": "gap body"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "ok"},
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "terminal_run",
                    "input": {"cmd": "echo hi"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": '{"stdout":"hi\\n"}'},
                {"type": "text", "text": "[turns remaining: 9]"},
            ],
        },
    ]
    oai = drone_messages_to_openai_chat(msgs)
    assert oai[0] == {"role": "user", "content": "gap body"}
    assert oai[1]["role"] == "assistant"
    assert oai[1]["content"] == "ok"
    assert len(oai[1]["tool_calls"]) == 1
    assert oai[1]["tool_calls"][0]["function"]["name"] == "terminal_run"
    assert '"cmd": "echo hi"' in oai[1]["tool_calls"][0]["function"]["arguments"]
    assert oai[2]["role"] == "tool"
    assert oai[2]["tool_call_id"] == "tu_1"
    assert oai[3] == {"role": "user", "content": "[turns remaining: 9]"}


def test_drone_messages_to_openai_chat_tools_only_assistant() -> None:
    msgs = [
        {"role": "user", "content": "x"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_abc",
                    "name": "cm_read_gap",
                    "input": {},
                },
            ],
        },
    ]
    oai = drone_messages_to_openai_chat(msgs)
    assert oai[1]["role"] == "assistant"
    assert oai[1]["content"] is None
    assert oai[1]["tool_calls"][0]["id"] == "call_abc"


def test_drone_tools_to_openai_functions_maps_schema() -> None:
    tools = [
        {
            "name": "terminal_run",
            "description": "run",
            "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        }
    ]
    oai = drone_tools_to_openai_functions(tools)
    assert oai[0]["type"] == "function"
    assert oai[0]["function"]["name"] == "terminal_run"
    assert oai[0]["function"]["parameters"]["properties"]["cmd"]["type"] == "string"


def test_make_client_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    c = make_client(Provider.openai, "gpt-4o")
    assert c.provider is Provider.openai
    assert c.model == "gpt-4o"


def test_resolve_only_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    p, m = resolve_orchestrator_provider_model(None, None)
    assert p is Provider.openai
    assert m == "gpt-4o"


def test_resolve_only_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    p, m = resolve_orchestrator_provider_model(None, None)
    assert p is Provider.anthropic
    assert m == "claude-sonnet-4-6"


def test_resolve_both_keys_defaults_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    p, m = resolve_orchestrator_provider_model(None, None)
    assert p is Provider.anthropic
    assert m == "claude-sonnet-4-6"


def test_resolve_explicit_openai_with_both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    p, m = resolve_orchestrator_provider_model(Provider.openai, None)
    assert p is Provider.openai
    assert m == "gpt-4o"


def test_resolve_custom_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    p, m = resolve_orchestrator_provider_model(None, "gpt-4o-mini")
    assert p is Provider.openai
    assert m == "gpt-4o-mini"


def test_resolve_no_keys_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="No API key"):
        resolve_orchestrator_provider_model(None, None)


def test_resolve_explicit_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p, m = resolve_orchestrator_provider_model(Provider.openai, "gpt-4o")
    assert p is Provider.openai
    assert m == "gpt-4o"
