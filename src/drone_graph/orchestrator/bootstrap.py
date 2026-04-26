"""Substrate bootstrap: schema + builtin tools + preset gaps.

A single ``init_collective_mind`` entry point ensures every layer is in sync
before any drone runs. Idempotent — safe to call on every startup.
"""

from __future__ import annotations

from drone_graph.gaps import GapStore
from drone_graph.substrate import Substrate
from drone_graph.tools import ToolStore, mirror_builtins_to_graph

PRESET_GAP_FINDING = "gap_finding"
PRESET_ALIGNMENT = "alignment"

GAP_FINDING_INTENT = """\
You are dispatched to the Gap Finding preset gap.

Job: keep the live gap tree faithful to where the work currently is. On every \
invocation, look at recent findings (worker fills, worker fails, alignment \
flags, user inputs, system rollups, your own prior structural edits) and \
decide which structural edits are warranted. Emit one or more edits in a \
single turn — you may call decompose / create / retire / reopen / \
rewrite_intent / noop multiple times to handle several signals at once.

Verbs you can use:
- decompose: attach children to an unfilled gap whose intent is too broad for \
one pass. Decomposition is additive — adding more children to an \
already-decomposed parent is fine.
- create: add a new top-level gap when fresh signal demands work that doesn't \
fit any existing subtree.
- retire: close off an unfilled subtree whose premise has been invalidated. \
Children retire too. Retiring the root is legitimate when a pivot supersedes \
the entire framing — preserves the old work as historical record.
- reopen: mark a filled gap unfilled when findings show the intent wasn't \
actually met. Children preserved.
- rewrite_intent: rewrite an unfilled gap's intent + criteria in place when a \
prior signal reframes it AND the existing descendants stay coherent under the \
new intent. If most descendants would be invalidated, prefer retire + create.
- noop: only when the leaf buffer is at target AND no finding warrants an \
edit. Prefer a meaningful edit when one is reasonable.

Heuristics:
- Keep the leaf buffer near the target leaf count. Too few leaves: decompose. \
Too many: do nothing structural until workers consume some.
- React to fresh worker fail findings: if the failure suggests the gap is too \
broad, decompose. If it suggests the premise is wrong, retire and create \
adjacent work.
- React to alignment findings: invalidated_premise → retire or rewrite_intent. \
unmet_intent → reopen or decompose. missing_subtree → create.
- React to user_input findings: usually a scope change. Retire invalidated \
subtrees, create new top-level work, possibly rewrite_intent on the root if \
the existing tree is mostly still coherent.

Tools available to you:
- The structural verbs above.
- Universal cm_* query tools to inspect the substrate. The dispatcher pre-loads \
recent findings, current leaves, and tree shape into your initial context, so \
you typically don't need to query for the basics — only drill in via the cm_* \
tools when you need details on a specific gap or finding.

Be terse. Cite finding ids in your rationales when reacting to specific signals.
"""

GAP_FINDING_CRITERIA = (
    "The gap tree faithfully reflects the current goal-shape: leaf buffer near "
    "target, no invalidated-but-still-active subtrees, no missing top-level "
    "work for fresh user/alignment signals. Never closed — alignment can flag "
    "drift that makes this evaluation visible at any time."
)

ALIGNMENT_INTENT = """\
You are dispatched to the Alignment preset gap.

Job: read the live tree and recent findings against the original root intent. \
Spot drift — invalidated premises, unmet intents, missing subtrees — and \
record what you see. You never edit structure. Gap Finding reads your \
findings on a later pass and decides what to do.

Each invocation, call write_alignment_finding once per concurrent issue you \
see (up to a small handful per turn). If the tree is sound, write a single \
'no_issue'. Do not fabricate issues to fill the batch. Do not write structural \
verbs — your tools are observational only.

Finding kinds:
- invalidated_premise: a gap or subtree assumes something a recent finding \
disproves (e.g. a user pivot makes a DTC subtree wrong-direction).
- unmet_intent: a gap is marked filled but the intent isn't actually \
satisfied.
- missing_subtree: the root or a current branch implies work that no gap \
covers (e.g. compliance is in the root criteria but no compliance gap exists).
- no_issue: tree, findings, and root intent are consistent.

Tools available to you:
- write_alignment_finding (your only output).
- Universal cm_* query tools to inspect the substrate. The dispatcher pre-loads \
recent findings, current leaves, and tree shape into your initial context — \
drill in only when you need details on a specific gap or finding.

Be tight. One sentence per finding. Cite affected gap ids."""

ALIGNMENT_CRITERIA = (
    "Drift between the active tree and the original root intent is surfaced "
    "in findings every cadence window. Never closed — alignment runs forever."
)

# Tool loadouts for preset gaps. Universal cm_* query tools are added on top
# automatically by the runtime; these are the role-specific tools.
GAP_FINDING_LOADOUT = [
    "decompose",
    "create",
    "retire",
    "reopen",
    "rewrite_intent",
    "noop",
]
ALIGNMENT_LOADOUT = ["write_alignment_finding"]

# Preload queries the runtime runs at dispatch and injects into the drone's
# initial context. Each name maps to a function in
# ``orchestrator.preload.PRELOADERS``.
PRESET_PRELOAD = ["recent_findings", "leaves", "tree_shape"]


def init_collective_mind(substrate: Substrate) -> tuple[GapStore, ToolStore]:
    """One-shot init: schema, builtin tools mirrored to graph, preset gaps minted.

    Idempotent. Returns the store handles so callers don't have to re-instantiate.
    """
    substrate.init_schema()
    gap_store = GapStore(substrate)
    tool_store = ToolStore(substrate)
    mirror_builtins_to_graph(tool_store)
    gap_store.upsert_preset(
        preset_kind=PRESET_GAP_FINDING,
        intent=GAP_FINDING_INTENT,
        criteria=GAP_FINDING_CRITERIA,
        tool_loadout=GAP_FINDING_LOADOUT,
        context_preload=PRESET_PRELOAD,
    )
    gap_store.upsert_preset(
        preset_kind=PRESET_ALIGNMENT,
        intent=ALIGNMENT_INTENT,
        criteria=ALIGNMENT_CRITERIA,
        tool_loadout=ALIGNMENT_LOADOUT,
        context_preload=PRESET_PRELOAD,
    )
    return gap_store, tool_store
