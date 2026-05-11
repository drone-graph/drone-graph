from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class GapStatus(StrEnum):
    unfilled = "unfilled"
    filled = "filled"
    retired = "retired"


class ModelTier(StrEnum):
    """Difficulty tier a gap claims. The scheduler resolves this through
    ``tier_defaults_by_provider`` (or operator-set overrides) to a concrete
    model when spawning a worker.

    Five levels from cheapest/least-capable to most-capable; ``standard`` is
    the default for newly-minted gaps and the right pick when GF is unsure.
    """

    nano = "nano"
    mini = "mini"
    standard = "standard"
    advanced = "advanced"
    frontier = "frontier"


class FindingAuthor(StrEnum):
    gap_finding = "gap_finding"
    alignment = "alignment"
    worker = "worker"
    user = "user"
    # Deterministic system bookkeeping. Today: auto-rollup fills when all of a
    # parent's non-retired children are filled. Alignment can still contest and
    # Gap Finding can reopen, so "system" is never authoritative on its own.
    system = "system"


class FindingKind(StrEnum):
    # Structural edits authored by Gap Finding.
    decompose = "decompose"
    create = "create"
    retire = "retire"
    reopen = "reopen"
    noop = "noop"
    rewrite_intent = "rewrite_intent"
    # Worker outcomes.
    fill = "fill"
    fail = "fail"
    # Worker cancelled mid-flight (gap retired or budget exceeded). Substrate
    # never deletes the work the drone did before the cancel — those findings
    # remain attached to the (now retired) gap as audit.
    cancelled = "cancelled"
    budget_exceeded = "budget_exceeded"
    # Alignment observations.
    alignment_invalidated_premise = "alignment_invalidated_premise"
    alignment_unmet_intent = "alignment_unmet_intent"
    alignment_missing_subtree = "alignment_missing_subtree"
    alignment_no_issue = "alignment_no_issue"
    # External signals.
    user_input = "user_input"
    # Freeform drone note that does not close or abandon a gap.
    note = "note"
    # Record of a tool/skill invocation (Phase 4); see Finding invocation_* fields.
    skill_invocation = "skill_invocation"
    # The drone is blocked on a human action — a credential the operator
    # must paste, an OAuth flow only the human can complete, a purchase
    # approval, an MFA code, etc. The drone emits this finding and exits;
    # the gap stays unfilled. The Mission Control UI surfaces the block in
    # its Action Inbox. When the operator resolves it, they append a
    # ``note`` finding referencing the block; Gap Finding picks it up next
    # tick and re-dispatches.
    requires_user_action = "requires_user_action"
    # Direct chat message between the operator and a specific live drone.
    # author=user: the operator typed this into the drone's chat panel; the
    # drone reads it via cm_browser.await_operator or sees it injected at
    # the next turn boundary. author=worker: the drone wrote this back to
    # the operator (a question, a status). Distinct from ``note`` so the
    # UI can route it to the per-drone chat panel and not bury it in the
    # global findings rail.
    chat_with_drone = "chat_with_drone"


def _now() -> datetime:
    return datetime.now(UTC)


class Gap(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4()))
    intent: str
    criteria: str
    status: GapStatus = GapStatus.unfilled
    reopen_count: int = 0
    retire_reason: str | None = None
    model_tier: ModelTier = ModelTier.standard
    created_at: datetime = Field(default_factory=_now)
    # Tool policy (set by Gap Finding at creation, or by substrate init for
    # preset gaps). All three lists are tool *names* — the registry resolves
    # them to schemas + dispatchers.
    tool_loadout: list[str] = Field(default_factory=list)
    """Always-available tools for the drone working this gap. Empty list means
    "use the default emergent loadout" (terminal_run, cm_read_gap,
    cm_write_finding, plus the universal cm_* query tools)."""
    tool_suggestions: list[str] = Field(default_factory=list)
    """Tools recommended by Gap Finding but not preloaded; the drone can ask
    for them via cm_request_tool(name) during the run."""
    context_preload: list[str] = Field(default_factory=list)
    """Preload entries run at dispatch and injected into the drone's initial
    user message. Known substrate preloaders: ``recent_findings``, ``leaves``,
    ``tree_shape``. Skill packages: ``skill_package:<path>`` (directory with
    ``SKILL.md``). Relative paths resolve under ``DRONE_GRAPH_SKILL_ROOT`` if
    set, else under the process current working directory."""
    # If this gap is a preset (Gap Finding, Alignment, etc.), this is its
    # stable preset id; ``None`` for emergent gaps. Preset gaps are minted at
    # substrate init and are never closed or retired by the loop.
    preset_kind: str | None = None
    # Optional thinking-effort hint for the resolved worker model. ``None``
    # means "N/A" (use the model's default). Values are vendor-agnostic
    # tokens — ``low``, ``medium``, ``high``, ``xhigh``, ``max`` — that get
    # mapped to provider-specific API parameters (Anthropic extended
    # thinking budget; OpenAI ``reasoning_effort``). If the resolved model
    # doesn't support thinking, this value is silently ignored at dispatch
    # time so GF can set it without worrying about model capability.
    reasoning_effort: str | None = None
    # Identity policy. When True, Gap Finding has judged this gap genuinely
    # requires the operator's own identity (their GitHub account, their
    # shell, their cwd, their saved creds) to complete. The scheduler
    # blocks dispatch until either:
    #   * the operator approves via the inbox → ``identity_approved`` flips
    #     to True and the drone runs with real env + real $HOME + real $PWD,
    #   * OR Settings.allow_operator_identity is False → the runtime
    #     silently runs the drone in clean (isolated) mode and emits a
    #     ``policy.identity_denied`` event; ``identity_denied_reason`` is
    #     set so future drones don't keep re-asking.
    # Defaults to False; drones run with throwaway identities.
    uses_operator_identity: bool = False
    identity_approved: bool = False
    identity_denied_reason: str | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4()))
    tick: int
    author: FindingAuthor
    kind: FindingKind
    summary: str
    affected_gap_ids: list[str] = Field(default_factory=list)
    # Paths to files on disk produced or referenced by this finding. The finding
    # stays short; substantive output lives on disk and is pointed at here so
    # other drones and tooling can retrieve it directly.
    artefact_paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    # --- skill_invocation (kind == skill_invocation); None for all other kinds.
    invocation_tool_name: str | None = None
    """Stable tool key; matches :Tool.name when the tool exists in the graph."""
    invocation_outcome: str | None = None
    """e.g. success, failure, partial."""
    invocation_provider: str | None = None
    """LLM vendor when tied to a model call (e.g. anthropic, openai)."""
    invocation_model: str | None = None
    """Vendor model id when applicable."""
    invocation_cost_usd: float | None = None
    """Approximate spend when derived from usage estimates."""
    invocation_metrics_json: str | None = None
    """Optional JSON object string: exit_code, duration_ms, tokens, etc."""
