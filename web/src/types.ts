// Wire types — kept in sync by hand with src/drone_graph/api/models.py.
// The backend is the contract; this file is a typed mirror.

export type GapStatus = "unfilled" | "filled" | "retired";
export type ModelTier = "cheap" | "standard" | "frontier";
export type FindingAuthor =
  | "gap_finding"
  | "alignment"
  | "worker"
  | "user"
  | "system";
export type FindingKind = string; // open enum — see records.py
export type SwarmState =
  | "idle"
  | "active"
  | "paused"
  | "cost_locked"
  | "resting";

export interface Gap {
  id: string;
  intent: string;
  criteria: string;
  status: GapStatus;
  reopen_count: number;
  retire_reason: string | null;
  model_tier: ModelTier;
  created_at: string;
  tool_loadout: string[];
  tool_suggestions: string[];
  context_preload: string[];
  preset_kind: string | null;
  uses_operator_identity?: boolean;
  identity_approved?: boolean;
  identity_denied_reason?: string | null;
  max_output_tokens?: number | null;
  /** True when the operator paused this gap via "not right now" on an
   *  inbox item. The scheduler skips paused gaps until /unpause is hit. */
  paused?: boolean;
}

export interface Finding {
  id: string;
  tick: number;
  author: FindingAuthor;
  kind: FindingKind;
  summary: string;
  affected_gap_ids: string[];
  artefact_paths: string[];
  created_at: string;
  invocation_tool_name: string | null;
  invocation_outcome: string | null;
  invocation_provider: string | null;
  invocation_model: string | null;
  invocation_cost_usd: number | null;
}

export interface Tool {
  name: string;
  description: string;
  kind: "builtin" | "installed";
  trust_tier: "high" | "standard" | "low" | "blocked";
  usage: string;
  install_commands: string[];
  depends_on: string[];
  flagged_by_alignment: boolean;
  needs_venv: boolean;
  last_used_at: string;
  created_at: string;
  deprecated_at: string | null;
  deprecated_reason: string | null;
  installed_by_drone_id: string | null;
  skill_package_id: string | null;
}

export interface ActiveDrone {
  drone_id: string;
  role: "preset:gap_finding" | "preset:alignment" | "worker";
  gap_id: string;
  tick: number;
  spawned_at: string;
  cancel_signaled: boolean;
  tape_path: string | null;
  turn: number | null;
  max_turns: number | null;
  last_command: string | null;
  tail_lines: string[];
  /** Tool names the drone called on its most recent turn. Rendered as
   *  a "now: cm_browser, terminal_run" line on the active-drones rail. */
  last_tool_calls?: string[];
  cost_usd: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  provider: string | null;
  model: string | null;
  model_tier: string | null;
}

export interface Claim {
  kind: string;
  key: string;
  drone_id: string;
  acquired_at: number;
  expires_at: number;
  cancelled: boolean;
  metadata: Record<string, unknown> | null;
}

export interface Install {
  key: string;
  installed_by: string;
  installed_at: number;
  install_commands: string[];
  usage: string | null;
}

export interface PendingInstall {
  id: string;
  tool_name: string;
  description: string;
  install_commands: string[];
  usage: string;
  requested_by_drone_id: string;
  requested_at: string;
}

export interface SwarmStatus {
  state: SwarmState;
  run_id: string;
  provider: string;
  model: string;
  started_at: string;
  paused: boolean;
  paranoid_install: boolean;
  cost_ceiling_usd: number | null;
  cost_spent_usd: number;
  gf_count: number;
  align_count: number;
  worker_count: number;
  consecutive_noops: number;
  active_drones: number;
  last_event_at: string | null;
  tick_seconds: number;
  needs_restart: boolean;
  needs_restart_reason: string | null;
}

export interface Snapshot {
  status: SwarmStatus;
  gaps: Gap[];
  parent_edges: [string, string][];
  recent_findings: Finding[];
  active_drones: ActiveDrone[];
  tools: Tool[];
  claims: Claim[];
  installs: Install[];
}

export interface StreamEvent {
  event: string;
  _seq?: number;
  ts?: string;
  [k: string]: unknown;
}

// ---- Settings + Action Inbox ---------------------------------------------

export interface SettingsView {
  has_anthropic_key: boolean;
  has_openai_key: boolean;
  anthropic_key_hint: string | null;
  openai_key_hint: string | null;
  default_provider: string | null;
  default_model: string | null;
  default_cost_ceiling_usd: number | null;
  cost_ceiling_acknowledged: boolean;
  default_paranoid_install: boolean;
  sound_enabled: boolean;
  tier_overrides: Record<string, Record<string, string>>;
  max_concurrent_browsers: number;
  allow_operator_identity: boolean;
  identity_acknowledged: boolean;
  identity_redaction_patterns: string[];
  settings_path: string;
  updated_at: string;
}

export interface SettingsPatch {
  anthropic_api_key?: string | null;
  openai_api_key?: string | null;
  default_provider?: string | null;
  default_model?: string | null;
  default_cost_ceiling_usd?: number | null;
  cost_ceiling_acknowledged?: boolean | null;
  default_paranoid_install?: boolean | null;
  sound_enabled?: boolean | null;
  tier_overrides?: Record<string, Record<string, string>> | null;
  max_concurrent_browsers?: number | null;
  allow_operator_identity?: boolean | null;
  identity_acknowledged?: boolean | null;
  identity_redaction_patterns?: string[] | null;
}

// ---- Personas -----------------------------------------------------------

export type CapabilityStatus = "pending" | "registered" | "verified";

export interface PersonaCapability {
  key: string;
  desired_value: string | null;
  actual_value: string | null;
  status: CapabilityStatus;
  credential_ref: string | null;
  notes: string | null;
  updated_at: string;
  verified_at: string | null;
}

export interface Persona {
  name: string;
  display_name: string;
  backed_by_real_human: boolean;
  bio: string | null;
  notes: string | null;
  ssh_fingerprint: string | null;
  browser_profiles: string[];
  capabilities: PersonaCapability[];
  created_at: string;
  created_by_drone_id: string | null;
}

export type InboxActionType =
  | "credential"
  | "oauth"
  | "sign_in"
  | "purchase"
  | "approval"
  | "mfa"
  | "identity"
  | "other";

export interface InboxItem {
  finding_id: string;
  tick: number;
  created_at: string;
  summary: string;
  affected_gap_ids: string[];
  action_type: InboxActionType;
  details: Record<string, unknown>;
  artefact_paths: string[];
}

export interface InboxResolveRequest {
  /** Operator intent on inbox items.
   *  resolved → drone proceed.
   *  try_another_way → keep gap, GF decomposes around the rejected route.
   *  dont_do_this → retire affected gap(s).
   *  not_right_now → pause affected gap(s) (resumable later).
   *  declined / skipped → legacy single-button deny, treated as try_another_way.
   */
  outcome:
    | "resolved"
    | "try_another_way"
    | "dont_do_this"
    | "not_right_now"
    | "declined"
    | "skipped";
  note?: string | null;
  external_id?: string | null;
}

// ---- Model registry ------------------------------------------------------

export interface RegistryModel {
  dgraph_model_id: string;
  provider: string;
  vendor_model_id: string;
  deprecated: boolean;
  input_price_per_million_usd: number;
  output_price_per_million_usd: number;
  capabilities: string[];
}

export interface ModelRegistry {
  populated: boolean;
  /** ``tier_defaults_by_provider[provider][tier]`` → dgraph_model_id */
  tier_defaults_by_provider: Record<string, Record<string, string>>;
  /** Tier names in canonical order (nano → frontier). */
  tiers: string[];
  models: RegistryModel[];
}
