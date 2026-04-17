from importlib.resources import files


def load_hivemind() -> str:
    return files("drone_graph.prompts").joinpath("hivemind.md").read_text(encoding="utf-8")


__all__ = ["load_hivemind"]
