from __future__ import annotations

from dataclasses import dataclass

from drone_graph.drones.providers import Provider
from drone_graph.gaps import GapStatus
from drone_graph.substrate import Substrate


@dataclass
class DroneResult:
    drone_id: str
    gap_id: str
    status: GapStatus
    finding_id: str | None
    tokens_in: int
    tokens_out: int
    cost_usd: float


def run_drone(
    gap_id: str,
    *,
    substrate: Substrate,
    provider: Provider,
    model: str,
) -> DroneResult:
    raise NotImplementedError(
        "build prompt from gap + hivemind prompt, run tool-use loop with "
        "terminal.run and cm.write_finding, write drone + finding nodes on exit"
    )
