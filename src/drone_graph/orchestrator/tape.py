from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EventTape:
    """Append-only JSONL log of drone lifecycle events.

    One line per event. Safe for concurrent writers within a single process;
    cross-process concurrency is a Phase 3 concern.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, event: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
        }
        record.update(fields)
        line = json.dumps(record, default=str)
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def default_tape_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("var") / "tapes" / f"orchestrator-{stamp}.jsonl"
