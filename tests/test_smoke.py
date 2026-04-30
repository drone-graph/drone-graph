from drone_graph.gaps import Gap, GapStatus, ModelTier
from drone_graph.prompts import load_hivemind


def test_gap_defaults() -> None:
    gap = Gap(intent="test", criteria="test criteria")
    assert gap.status is GapStatus.unfilled
    assert gap.model_tier is ModelTier.standard
    assert gap.id


def test_hivemind_prompt_loads() -> None:
    prompt = load_hivemind()
    assert "You are a drone." in prompt
    assert "write a `fill` finding" in prompt
