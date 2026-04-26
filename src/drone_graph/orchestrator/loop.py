"""Unified orchestrator loop.

Every drone is the same drone — ``run_drone(gap)``. This loop dispatches:

  1. Scheduled scenario events (injected as findings, not drone runs).
  2. The Alignment preset drone, every ``align_every`` Gap Finding cycles.
  3. The Gap Finding preset drone every cycle.
  4. Optionally, a worker drone against the oldest unattempted active leaf
     every ``worker_every`` Gap Finding cycles.

Stops when Gap Finding hits three consecutive no-op cycles AND there are no
unattempted active leaves, when ``max_gf`` Gap Finding runs is reached, or
when too many consecutive client errors surface.

Usage as a module:
    python -m drone_graph.orchestrator.loop --scenario coffee-pivot-b2b
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from drone_graph.drones import (
    ChatClient,
    Provider,
    make_client,
    resolve_orchestrator_provider_model,
    run_drone,
)
from drone_graph.gaps import Finding, FindingAuthor, Gap, GapStore
from drone_graph.orchestrator.bootstrap import (
    PRESET_ALIGNMENT,
    PRESET_GAP_FINDING,
    init_collective_mind,
)
from drone_graph.orchestrator.rendering import render_findings, render_tree
from drone_graph.orchestrator.scenarios import (
    available_scenarios,
    inject_event,
    load_root_seed,
    load_scenario,
)
from drone_graph.orchestrator.tape import EventTape, default_tape_path
from drone_graph.substrate import Substrate
from drone_graph.tools import ToolStore

DEFAULT_TARGET_LEAVES = 5
DEFAULT_ALIGNMENT_EVERY = 3
DEFAULT_MAX_GF = 15
DEFAULT_WORKER_EVERY = 0  # 0 = do not spawn real workers
DEFAULT_WORKER_MAX_TURNS = 20
DEFAULT_PRESET_MAX_TURNS = 6  # preset drones rarely need more
MAX_CONSECUTIVE_GF_ERRORS = 3


def _findings_at_tick(store: GapStore, tick: int) -> list[Finding]:
    return [f for f in store.all_findings() if f.tick == tick]


def _pick_worker_target(store: GapStore, already_attempted: set[str]) -> Gap | None:
    """Pick an emergent leaf to work on.

    Priority:
      1. Oldest active leaf with no prior worker attempt this run.
      2. Oldest active leaf whose last worker attempt was a fail AND at least
         one non-fail finding has been written since (progress made).
    """
    leaves = store.leaves()
    for leaf in leaves:
        if leaf.id not in already_attempted:
            return leaf
    all_findings = store.all_findings()
    for leaf in leaves:
        if leaf.id not in already_attempted:
            continue
        last_fail_tick: int | None = None
        for f in all_findings:
            if (
                f.kind.value == "fail"
                and f.author.value == "worker"
                and leaf.id in f.affected_gap_ids
            ):
                last_fail_tick = f.tick
        if last_fail_tick is None:
            continue
        if any(f.tick > last_fail_tick and f.kind.value != "fail" for f in all_findings):
            return leaf
    return None


def run_combined_loop(
    *,
    substrate: Substrate,
    client: ChatClient,
    scenario_name: str | None = None,
    out_dir: Path | None = None,
    tape: EventTape | None = None,
    target_leaves: int = DEFAULT_TARGET_LEAVES,
    align_every: int = DEFAULT_ALIGNMENT_EVERY,
    max_gf: int = DEFAULT_MAX_GF,
    worker_every: int = DEFAULT_WORKER_EVERY,
    worker_max_turns: int = DEFAULT_WORKER_MAX_TURNS,
    preset_max_turns: int = DEFAULT_PRESET_MAX_TURNS,
    reset: bool | None = None,
) -> Path | None:
    """Run the unified GF + Alignment + Worker loop.

    If ``scenario_name`` is given, reset+seed the substrate and inject the
    scenario's scheduled events. Otherwise run against the current graph state.
    Preset gaps are minted (or refreshed) on every call via
    ``init_collective_mind`` — idempotent, safe to re-run.
    """
    scenario: dict[str, Any] | None = None
    pending_events: list[dict[str, Any]] = []
    if scenario_name is not None:
        scenario = load_scenario(scenario_name)
        align_every = int(scenario.get("alignment_every_n_gf", align_every))
        max_gf = int(scenario.get("max_gf_invocations", max_gf))
        target_leaves = int(scenario.get("target_leaves", target_leaves))
        pending_events = sorted(
            list(scenario.get("events", [])),
            key=lambda e: e["at_gf_tick"],
        )

    if reset is None:
        reset = scenario_name is not None
    if reset:
        # Reset before init so preset gaps get re-minted fresh.
        substrate.execute_write("MATCH (n) DETACH DELETE n")

    store, tool_store = init_collective_mind(substrate)

    if scenario is not None:
        intent, criteria = load_root_seed(scenario["root"])
        store.create_root(intent=intent, criteria=criteria)

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    if tape is None and out_dir is not None:
        tape = EventTape(out_dir / "tape.jsonl")

    timeline: list[dict[str, Any]] = []
    total_cost = 0.0
    gf_count = 0
    align_count = 0
    consecutive_noops = 0
    consecutive_gf_errors = 0
    events_fired = 0
    gf_verbs: dict[str, int] = {}
    align_kinds: dict[str, int] = {}
    worker_outcomes: dict[str, int] = {}
    worker_count = 0
    attempted_gap_ids: set[str] = set()
    last_alignment_gf = -1
    stop_reason = ""
    tick = 0

    label = f"scenario={scenario_name}" if scenario_name else "persistent"
    print(f"orchestrator.loop: {label}")
    print(f"  alignment every {align_every} GF calls, max {max_gf} GF calls")
    if worker_every > 0:
        print(f"  worker every {worker_every} GF calls (max_turns={worker_max_turns})")
    print(f"  events scheduled: {len(pending_events)}")
    if out_dir is not None:
        print(f"  out: {out_dir}")
    print()

    def log(actor: str, event: str, summary: str, cost: float) -> None:
        timeline.append(
            {
                "tick": tick,
                "actor": actor,
                "event": event,
                "summary": summary,
                "cost_usd": round(cost, 4),
            }
        )

    events_log = (out_dir / "events.jsonl").open("w") if out_dir is not None else None

    def ev_write(record: dict[str, Any]) -> None:
        if events_log is not None:
            events_log.write(json.dumps(record) + "\n")
            events_log.flush()

    gf_preset = store.get_preset(PRESET_GAP_FINDING)
    align_preset = store.get_preset(PRESET_ALIGNMENT)
    if gf_preset is None or align_preset is None:
        raise RuntimeError("preset gaps not minted; init_collective_mind failed")

    try:
        while gf_count < max_gf:
            # 1. Inject scheduled events.
            while pending_events and pending_events[0]["at_gf_tick"] <= gf_count + 1:
                ev = pending_events.pop(0)
                tick += 1
                f = inject_event(store, ev, tick=tick)
                events_fired += 1
                preview = ev["summary"][:160]
                log("injected", f"{ev['author']}:{ev['kind']}", preview, 0.0)
                ev_write({"phase": "inject", "tick": tick, **ev})
                if tape is not None:
                    tape.emit(
                        "scenario.inject",
                        tick=tick,
                        author=ev["author"],
                        kind=ev["kind"],
                        finding_id=f.id,
                    )
                print(f"  [tick {tick}] INJECT   {ev['author']}:{ev['kind']}")
                print(f"              {preview}")

            # 2. Alignment cycle every N GF cycles.
            ran_alignment = (
                gf_count > 0
                and gf_count % align_every == 0
                and last_alignment_gf != gf_count
            )
            if ran_alignment:
                last_alignment_gf = gf_count
                tick += 1
                align_count += 1
                t0 = time.time()
                alignment_result = run_drone(
                    align_preset,
                    store=store,
                    tool_store=tool_store,
                    client=client,
                    tick=tick,
                    max_turns=preset_max_turns,
                    tape=tape,
                )
                dt = time.time() - t0
                total_cost += alignment_result.cost_usd

                if alignment_result.outcome == "error":
                    err = alignment_result.error or "unknown"
                    print(f"  [tick {tick}] ALIGN    ERROR: {err}")
                    log("alignment", "error", err, alignment_result.cost_usd)
                    ev_write(
                        {
                            "phase": "alignment",
                            "tick": tick,
                            "error": err,
                            "cost_usd": alignment_result.cost_usd,
                        }
                    )
                else:
                    align_findings = [
                        f
                        for f in _findings_at_tick(store, tick)
                        if f.author is FindingAuthor.alignment
                    ]
                    for idx, f in enumerate(align_findings, start=1):
                        align_kinds[f.kind.value] = align_kinds.get(f.kind.value, 0) + 1
                        preview = f.summary[:160]
                        log("alignment", f.kind.value, preview, 0.0)
                        ev_write(
                            {
                                "phase": "alignment",
                                "tick": tick,
                                "batch_idx": idx,
                                "finding_id": f.id,
                                "kind": f.kind.value,
                                "summary": f.summary,
                                "affected_gap_ids": f.affected_gap_ids,
                            }
                        )
                        print(
                            f"  [tick {tick}] ALIGN    edit {idx}/{len(align_findings)}  "
                            f"{f.kind.value:<32}  {preview}"
                        )
                    print(
                        f"  [tick {tick}] ALIGN    batch of {len(align_findings)}  "
                        f"${alignment_result.cost_usd:.3f}  {dt:.1f}s"
                    )

            # 3. Gap Finding cycle.
            tick += 1
            gf_count += 1
            leaves_before = len(store.leaves())
            t0 = time.time()
            gf_result = run_drone(
                gf_preset,
                store=store,
                tool_store=tool_store,
                client=client,
                tick=tick,
                max_turns=preset_max_turns,
                tape=tape,
            )
            dt = time.time() - t0
            total_cost += gf_result.cost_usd

            if gf_result.outcome == "error":
                err = gf_result.error or "unknown"
                log("gap_finding", "error", err, gf_result.cost_usd)
                ev_write(
                    {
                        "phase": "gap_finding",
                        "tick": tick,
                        "gf_count": gf_count,
                        "error": err,
                        "cost_usd": gf_result.cost_usd,
                    }
                )
                print(f"  [tick {tick}] GF#{gf_count:<2} ERROR: {err}")
                consecutive_gf_errors += 1
                if consecutive_gf_errors >= MAX_CONSECUTIVE_GF_ERRORS:
                    stop_reason = (
                        f"{MAX_CONSECUTIVE_GF_ERRORS} consecutive gap finding errors "
                        f"(last: {err})"
                    )
                    break
                store.apply_noop(rationale=f"gap finding error: {err}", tick=tick)
                continue

            consecutive_gf_errors = 0
            gf_findings = [
                f
                for f in _findings_at_tick(store, tick)
                if f.author is FindingAuthor.gap_finding
            ]
            for idx, f in enumerate(gf_findings, start=1):
                gf_verbs[f.kind.value] = gf_verbs.get(f.kind.value, 0) + 1
                preview = f.summary[:140]
                log("gap_finding", f.kind.value, preview, 0.0)
                ev_write(
                    {
                        "phase": "gap_finding",
                        "tick": tick,
                        "gf_count": gf_count,
                        "batch_idx": idx,
                        "verb": f.kind.value,
                        "finding_id": f.id,
                        "summary": f.summary,
                        "affected_gap_ids": f.affected_gap_ids,
                    }
                )
                print(
                    f"  [tick {tick}] GF#{gf_count:<2} edit {idx}/{len(gf_findings)}  "
                    f"{f.kind.value:<14} {preview}"
                )
            print(
                f"  [tick {tick}] GF#{gf_count:<2} batch of {len(gf_findings)}  "
                f"leaves {leaves_before}→{len(store.leaves())}  "
                f"${gf_result.cost_usd:.3f}  {dt:.1f}s"
            )

            # Track noops only if the entire batch was noop (or empty).
            non_noop_present = any(
                f.kind.value not in ("noop",) for f in gf_findings
            )
            if not gf_findings or not non_noop_present:
                consecutive_noops += 1
                workers_have_work = (
                    worker_every > 0
                    and _pick_worker_target(store, attempted_gap_ids) is not None
                )
                if consecutive_noops >= 3 and not workers_have_work:
                    stop_reason = "3 consecutive noops"
                    break
            else:
                consecutive_noops = 0

            # 4. Optional worker cycle against an emergent leaf.
            if worker_every > 0 and gf_count % worker_every == 0:
                target = _pick_worker_target(store, attempted_gap_ids)
                if target is None:
                    print(f"  [tick {tick}] WORKER   (no unattempted active leaf; skipping)")
                else:
                    tick += 1
                    worker_count += 1
                    attempted_gap_ids.add(target.id)
                    t0 = time.time()
                    try:
                        w = run_drone(
                            target,
                            store=store,
                            tool_store=tool_store,
                            client=client,
                            tick=tick,
                            max_turns=worker_max_turns,
                            tape=tape,
                        )
                    except Exception as e:  # noqa: BLE001
                        dt = time.time() - t0
                        err_msg = f"{type(e).__name__}: {e}"
                        log("worker", "error", err_msg, 0.0)
                        ev_write(
                            {
                                "phase": "worker",
                                "tick": tick,
                                "gap_id": target.id,
                                "error": err_msg,
                                "latency_s": round(dt, 2),
                            }
                        )
                        print(f"  [tick {tick}] WORKER   ERROR on {target.id[:8]}: {err_msg}")
                        stop_reason = f"worker error: {err_msg}"
                        break
                    dt = time.time() - t0
                    total_cost += w.cost_usd
                    worker_outcomes[w.outcome] = worker_outcomes.get(w.outcome, 0) + 1
                    summary = f"{w.outcome} on {target.id[:8]}: {target.intent[:60]}"
                    log("worker", w.outcome, summary, w.cost_usd)
                    ev_write(
                        {
                            "phase": "worker",
                            "tick": tick,
                            "gap_id": target.id,
                            "drone_id": w.drone_id,
                            "outcome": w.outcome,
                            "finding_id": w.finding_id,
                            "turns_used": w.turns_used,
                            "tokens_in": w.tokens_in,
                            "tokens_out": w.tokens_out,
                            "cost_usd": w.cost_usd,
                            "error": w.error,
                            "latency_s": round(dt, 2),
                        }
                    )
                    print(
                        f"  [tick {tick}] WORKER   {w.outcome:<12} {target.id[:8]}  "
                        f"turns={w.turns_used}/{worker_max_turns}  "
                        f"${w.cost_usd:.3f}  {dt:.1f}s"
                    )
                    print(f"              {target.intent[:80]}")
                    if w.error:
                        print(f"              error: {w.error}")
        else:
            stop_reason = f"max_gf_invocations ({max_gf})"

        # Final alignment pass on clean exits to capture end-of-run drift.
        if not (
            stop_reason.startswith("worker error")
            or stop_reason.startswith("3 consecutive gap finding errors")
        ):
            tick += 1
            align_count += 1
            t0 = time.time()
            final_result = run_drone(
                align_preset,
                store=store,
                tool_store=tool_store,
                client=client,
                tick=tick,
                max_turns=preset_max_turns,
                tape=tape,
            )
            dt = time.time() - t0
            total_cost += final_result.cost_usd
            if final_result.outcome != "error":
                final_findings = [
                    f
                    for f in _findings_at_tick(store, tick)
                    if f.author is FindingAuthor.alignment
                ]
                for idx, f in enumerate(final_findings, start=1):
                    align_kinds[f.kind.value] = align_kinds.get(f.kind.value, 0) + 1
                    preview = f.summary[:160]
                    log("alignment", f.kind.value, preview, 0.0)
                    ev_write(
                        {
                            "phase": "alignment_final",
                            "tick": tick,
                            "batch_idx": idx,
                            "finding_id": f.id,
                            "kind": f.kind.value,
                            "summary": f.summary,
                            "affected_gap_ids": f.affected_gap_ids,
                        }
                    )
                    print(
                        f"  [tick {tick}] ALIGN    edit {idx}/{len(final_findings)}  "
                        f"{f.kind.value:<32} (final)  {preview}"
                    )
                if final_findings:
                    print(
                        f"  [tick {tick}] ALIGN    batch of {len(final_findings)} (final)  "
                        f"${final_result.cost_usd:.3f}  {dt:.1f}s"
                    )
    finally:
        if events_log is not None:
            events_log.close()

    if out_dir is not None:
        _write_run_artifacts(
            out_dir=out_dir,
            scenario_name=scenario_name,
            store=store,
            timeline=timeline,
            gf_count=gf_count,
            align_count=align_count,
            worker_count=worker_count,
            events_fired=events_fired,
            gf_verbs=gf_verbs,
            align_kinds=align_kinds,
            worker_outcomes=worker_outcomes,
            total_cost=total_cost,
            stop_reason=stop_reason,
            model=client.model,
            final_tick=tick,
        )

    print(
        f"\ndone. gf={gf_count}  align={align_count}  "
        f"workers={worker_count}  events={events_fired}"
    )
    print(f"  gf verbs: {gf_verbs}")
    print(f"  alignment kinds: {align_kinds}")
    if worker_count:
        print(f"  worker outcomes: {worker_outcomes}")
    print(f"  total cost: ${total_cost:.3f}")
    print(f"  stop reason: {stop_reason}")
    if out_dir is not None:
        print(f"\ninspect:\n  {out_dir / 'timeline.md'}\n  {out_dir / 'tree.md'}")
    return out_dir


def _write_run_artifacts(
    *,
    out_dir: Path,
    scenario_name: str | None,
    store: GapStore,
    timeline: list[dict[str, Any]],
    gf_count: int,
    align_count: int,
    worker_count: int,
    events_fired: int,
    gf_verbs: dict[str, int],
    align_kinds: dict[str, int],
    worker_outcomes: dict[str, int],
    total_cost: float,
    stop_reason: str,
    model: str,
    final_tick: int,
) -> None:
    tree_path = out_dir / "tree.md"
    timeline_path = out_dir / "timeline.md"
    summary_path = out_dir / "summary.md"

    tree_path.write_text(
        f"# {scenario_name or 'persistent'}\n\n"
        f"Model: `{model}`   Stop: {stop_reason}\n"
        f"Final tick: {final_tick}   GF cycles: {gf_count}   "
        f"Alignment cycles: {align_count}   "
        f"Worker drones: {worker_count}   Events fired: {events_fired}\n"
        f"Active leaves: {len(store.leaves())}\n\n"
        f"## Tree\n\n```\n{render_tree(store)}\n```\n\n"
        f"## Findings\n\n```\n{render_findings(store, limit=9999)}\n```\n"
    )

    lines = [
        f"# timeline — {scenario_name or 'persistent'}\n",
        f"Model: `{model}`\n",
        "",
        "| tick | actor | event | cost | summary |",
        "|------|-------|-------|------|---------|",
    ]
    for row in timeline:
        summary_cell = row["summary"].replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row['tick']} | {row['actor']} | `{row['event']}` | "
            f"${row['cost_usd']:.3f} | {summary_cell} |"
        )
    timeline_path.write_text("\n".join(lines) + "\n")

    all_gaps = store.all_gaps()
    summary_path.write_text(
        f"# Run summary\n\n"
        f"- scenario: `{scenario_name or 'persistent'}`\n"
        f"- model: `{model}`\n"
        f"- stop reason: {stop_reason}\n"
        f"- GF cycles: {gf_count}\n"
        f"- Alignment cycles: {align_count}\n"
        f"- Worker drones: {worker_count}\n"
        f"- events fired: {events_fired}\n"
        f"- final active leaves: {len(store.leaves())}\n"
        f"- total gaps: {len(all_gaps)} "
        f"(filled: {sum(1 for g in all_gaps if g.status.value == 'filled')}, "
        f"retired: {sum(1 for g in all_gaps if g.status.value == 'retired')}, "
        f"preset: {sum(1 for g in all_gaps if g.preset_kind is not None)})\n"
        f"- GF verb histogram: {gf_verbs}\n"
        f"- Alignment kind histogram: {align_kinds}\n"
        f"- Worker outcome histogram: {worker_outcomes}\n"
        f"- cost: ${total_cost:.3f}\n"
    )


def _resolve_substrate() -> Substrate:
    import os

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "drone-graph-dev")
    return Substrate(uri, user, password)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--scenario",
        default=None,
        help=(
            "Scenario stem (e.g. coffee-pivot-b2b). Omit to run against the "
            "current persistent graph. Available: "
            + ", ".join(available_scenarios())
        ),
    )
    ap.add_argument(
        "--model",
        default=None,
        help="Vendor model id (default: claude-sonnet-4-6 or gpt-4o from --provider).",
    )
    ap.add_argument("--provider", default=None, choices=[p.value for p in Provider])
    ap.add_argument("--out", default=None, help="Output dir (default: var/runs/<scenario>-<ts>).")
    ap.add_argument("--target-leaves", type=int, default=DEFAULT_TARGET_LEAVES)
    ap.add_argument("--max-gf", type=int, default=DEFAULT_MAX_GF)
    ap.add_argument("--align-every", type=int, default=DEFAULT_ALIGNMENT_EVERY)
    ap.add_argument(
        "--worker-every",
        type=int,
        default=DEFAULT_WORKER_EVERY,
        help=(
            "Spawn one real worker drone on the oldest unattempted active leaf "
            "every N GF cycles (0 = off)."
        ),
    )
    ap.add_argument(
        "--worker-max-turns",
        type=int,
        default=DEFAULT_WORKER_MAX_TURNS,
        help="Max model turns per spawned worker drone.",
    )
    ap.add_argument(
        "--preset-max-turns",
        type=int,
        default=DEFAULT_PRESET_MAX_TURNS,
        help="Max model turns per preset (Gap Finding / Alignment) drone cycle.",
    )
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    provider = Provider(args.provider) if args.provider else None
    resolved_provider, resolved_model = resolve_orchestrator_provider_model(
        provider, args.model
    )

    if args.out is None:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        label = args.scenario or "persistent"
        out_dir = Path("var") / "runs" / f"{label}-{resolved_model}-{ts}"
    else:
        out_dir = Path(args.out)

    substrate = _resolve_substrate()
    client = make_client(resolved_provider, resolved_model)
    run_combined_loop(
        substrate=substrate,
        client=client,
        scenario_name=args.scenario,
        out_dir=out_dir,
        target_leaves=args.target_leaves,
        align_every=args.align_every,
        max_gf=args.max_gf,
        worker_every=args.worker_every,
        worker_max_turns=args.worker_max_turns,
        preset_max_turns=args.preset_max_turns,
    )


if __name__ == "__main__":
    main()
