from __future__ import annotations

from drone_graph.drones import DroneResult
from drone_graph.substrate import Substrate


def run_once(substrate: Substrate) -> DroneResult | None:
    raise NotImplementedError(
        "query oldest Gap where status = 'open', mark in_progress, call run_drone, "
        "update status on result"
    )


def run_forever(substrate: Substrate, poll_interval_s: float = 2.0) -> None:
    raise NotImplementedError("loop run_once with a sleep; handle SIGINT")
