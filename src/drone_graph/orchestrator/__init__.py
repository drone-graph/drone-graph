from __future__ import annotations

from typing import Any

from drone_graph.orchestrator.bootstrap import (
    PRESET_ALIGNMENT,
    PRESET_GAP_FINDING,
    init_collective_mind,
)
from drone_graph.orchestrator.preload import PRELOADERS, render_preloads
from drone_graph.orchestrator.scenarios import (
    available_roots,
    available_scenarios,
    inject_event,
    load_root_seed,
    load_scenario,
)
from drone_graph.orchestrator.tape import EventTape, default_tape_path

__all__ = [
    "EventTape",
    "PRELOADERS",
    "PRESET_ALIGNMENT",
    "PRESET_GAP_FINDING",
    "available_roots",
    "available_scenarios",
    "default_tape_path",
    "init_collective_mind",
    "inject_event",
    "load_root_seed",
    "load_scenario",
    "render_preloads",
    "run_combined_loop",
]


def __getattr__(name: str) -> Any:
    """Lazy export so ``python -m drone_graph.orchestrator.loop`` does not load
    ``loop`` while importing this package (avoids runpy double-import warning).
    """
    if name == "run_combined_loop":
        from drone_graph.orchestrator.loop import run_combined_loop as _run_combined_loop

        globals()["run_combined_loop"] = _run_combined_loop
        return _run_combined_loop
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
