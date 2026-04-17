from drone_graph.gaps import Gap, GapStatus, ModelTier
from drone_graph.prompts import load_hivemind


def test_gap_defaults() -> None:
    gap = Gap(description="test")
    assert gap.status is GapStatus.open
    assert gap.model_tier is ModelTier.standard
    assert gap.id
    assert gap.closed_at is None


def test_hivemind_prompt_loads() -> None:
    prompt = load_hivemind()
    assert "You are a drone." in prompt
    assert "Close the gap." in prompt
