from importlib.resources import files


def _load(name: str) -> str:
    return files("drone_graph.prompts").joinpath(name).read_text(encoding="utf-8")


def load_hivemind() -> str:
    return _load("hivemind.md")


__all__ = ["load_hivemind"]
