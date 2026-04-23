"""Tests for Anthropic ``models.list`` JSON dump helper."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from drone_graph.model_registry.anthropic_models_list_dump import (
    fetch_anthropic_models_list_json_dump,
)


def test_fetch_anthropic_models_list_json_dump_returns_array_json() -> None:
    mock_model = MagicMock()
    mock_model.type = "model"
    mock_model.model_dump.return_value = {"id": "claude-test-1", "type": "model", "max_tokens": 1}

    page = MagicMock()
    page.data = [mock_model]
    page.has_next_page.return_value = False

    mock_client = MagicMock()
    mock_client.models.list.return_value = page

    with patch(
        "drone_graph.model_registry.anthropic_models_list_dump.Anthropic",
        return_value=mock_client,
    ):
        out = fetch_anthropic_models_list_json_dump("sk-ant-test")

    mock_client.models.list.assert_called_once()
    mock_model.model_dump.assert_called_once_with(mode="json")
    data = json.loads(out)
    assert isinstance(data, list)
    assert data[0]["id"] == "claude-test-1"
