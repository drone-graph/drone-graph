// Thin fetch wrapper over the FastAPI mission-control API.
// Returns parsed JSON; throws on non-2xx.

import type {
  Finding,
  Gap,
  InboxItem,
  InboxResolveRequest,
  LaunchResponse,
  ModelRegistry,
  PendingInstall,
  PermissionPrompt,
  SettingsPatch,
  SettingsView,
  Snapshot,
  SwarmStatus,
  Tool,
} from "./types";

interface HttpInit {
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
}

async function http<T>(path: string, init?: HttpInit): Promise<T> {
  const opts: RequestInit = {
    method: init?.method,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  };
  if (init?.body !== undefined) {
    opts.body =
      typeof init.body === "string" ? init.body : JSON.stringify(init.body);
  }
  const r = await fetch(path, opts);
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status} ${r.statusText}: ${text}`);
  }
  if (r.status === 204) return undefined as unknown as T;
  return (await r.json()) as T;
}

export const api = {
  // ---- Reads --------------------------------------------------------------
  snapshot: () => http<Snapshot>("/api/snapshot"),
  status: () => http<SwarmStatus>("/api/status"),
  gap: (id: string) => http<Gap>(`/api/gaps/${encodeURIComponent(id)}`),
  findingsForGap: (id: string, limit = 50) =>
    http<Finding[]>(
      `/api/gaps/${encodeURIComponent(id)}/findings?limit=${limit}`,
    ),
  tools: () => http<Tool[]>("/api/tools"),
  pendingInstalls: () => http<PendingInstall[]>("/api/installs/pending"),

  // ---- Chat & gap edits ---------------------------------------------------
  chat: (message: string, target_gap_id?: string | null) =>
    http<{ finding_id: string }>("/api/edit/chat", {
      method: "POST",
      body: { message, target_gap_id: target_gap_id ?? null },
    }),
  retire: (gap_id: string, reason: string) =>
    http("/api/edit/gaps/" + encodeURIComponent(gap_id) + "/retire", {
      method: "POST",
      body: { reason },
    }),
  rewrite: (
    gap_id: string,
    new_intent: string,
    new_criteria: string,
    rationale = "Rewritten by user via mission control",
  ) =>
    http("/api/edit/gaps/" + encodeURIComponent(gap_id) + "/rewrite", {
      method: "POST",
      body: { new_intent, new_criteria, rationale },
    }),
  reopen: (gap_id: string, reason: string) =>
    http("/api/edit/gaps/" + encodeURIComponent(gap_id) + "/reopen", {
      method: "POST",
      body: { reason },
    }),
  cancelDrone: (gap_id: string, reason = "user_cancelled") =>
    http("/api/edit/drones/" + encodeURIComponent(gap_id) + "/cancel", {
      method: "POST",
      body: { reason },
    }),
  setTrustTier: (name: string, tier: Tool["trust_tier"]) =>
    http("/api/edit/tools/" + encodeURIComponent(name) + "/trust", {
      method: "POST",
      body: { tier },
    }),
  flagTool: (name: string) =>
    http("/api/edit/tools/" + encodeURIComponent(name) + "/flag", {
      method: "POST",
    }),
  unflagTool: (name: string) =>
    http("/api/edit/tools/" + encodeURIComponent(name) + "/unflag", {
      method: "POST",
    }),
  resolveInstall: (id: string, approve: boolean) =>
    http("/api/edit/installs/" + encodeURIComponent(id), {
      method: "POST",
      body: { approve },
    }),

  // ---- Control ------------------------------------------------------------
  pause: () => http("/api/control/pause", { method: "POST" }),
  resume: () => http("/api/control/resume", { method: "POST" }),
  setCeiling: (ceiling_usd: number | null) =>
    http("/api/control/ceiling", {
      method: "POST",
      body: { ceiling_usd },
    }),
  setParanoid: (enabled: boolean) =>
    http("/api/control/paranoid", {
      method: "POST",
      body: { enabled },
    }),
  forceTick: (role: "gap_finding" | "alignment") =>
    http("/api/control/force-tick", {
      method: "POST",
      body: { role },
    }),
  restartSwarm: () =>
    http<{ ok: boolean; started: boolean }>("/api/control/restart", {
      method: "POST",
    }),

  // ---- Settings -----------------------------------------------------------
  settings: () => http<SettingsView>("/api/settings"),
  updateSettings: (patch: SettingsPatch) =>
    http<SettingsView>("/api/settings", { method: "POST", body: patch }),
  models: () => http<ModelRegistry>("/api/models"),

  // ---- Action inbox -------------------------------------------------------
  inbox: () => http<InboxItem[]>("/api/inbox"),
  resolveInbox: (finding_id: string, req: InboxResolveRequest) =>
    http(`/api/inbox/${encodeURIComponent(finding_id)}/resolve`, {
      method: "POST",
      body: req,
    }),

  unpauseGap: (gap_id: string) =>
    http<{ finding: unknown }>(
      `/api/edit/gaps/${encodeURIComponent(gap_id)}/unpause`,
      { method: "POST" },
    ),

  // ---- Synchronous permission prompts -------------------------------------
  pendingPermissions: () =>
    http<PermissionPrompt[]>("/api/permissions/pending"),
  grantPermission: (id: string, note?: string | null) =>
    http<PermissionPrompt>(
      `/api/permissions/${encodeURIComponent(id)}/grant`,
      { method: "POST", body: { note: note ?? null } },
    ),
  denyPermission: (id: string, note?: string | null) =>
    http<PermissionPrompt>(
      `/api/permissions/${encodeURIComponent(id)}/deny`,
      { method: "POST", body: { note: note ?? null } },
    ),

  // ---- Drone-attached chat + computer-use --------------------------------
  chatWithDrone: (gap_id: string, text: string) =>
    http<{ ok: boolean; finding_id: string }>(
      `/api/chat/drone/${encodeURIComponent(gap_id)}`,
      { method: "POST", body: { text } },
    ),
  // Preferred shape: chat lives on the gap. Same backend behavior,
  // cleaner mental model.
  chatGap: (gap_id: string, text: string) =>
    http<{ ok: boolean; finding_id: string }>(
      `/api/chat/gap/${encodeURIComponent(gap_id)}`,
      { method: "POST", body: { text } },
    ),
  chatGapHistory: (gap_id: string, limit = 100) =>
    http<{
      gap_id: string;
      messages: {
        id: string;
        author: string;
        text: string;
        ts: string;
        tick: number;
      }[];
    }>(
      `/api/chat/gap/${encodeURIComponent(gap_id)}?limit=${limit}`,
    ),
  browserState: (gap_id: string) =>
    http<BrowserState>(`/api/drones/${encodeURIComponent(gap_id)}/browser-state`),
  screenshot: (gap_id: string) =>
    http<{ b64: string; ts: string; url: string; title: string; action: string }>(
      `/api/drones/${encodeURIComponent(gap_id)}/screenshot`,
    ),

  // ---- Browser launcher ---------------------------------------------------
  profileLaunch: (profile_name: string) =>
    http<LaunchResponse>(`/api/profiles/launch?profile_name=${encodeURIComponent(profile_name)}`, {
      method: "POST",
    }),

  // ---- Authenticated profile ----------------------------------------------
  authenticatedProfileSetup: () =>
    http<{message: string; cdp_port: number}>("/api/profiles/authenticated/setup", {method: "POST"}),
  authenticatedProfileStatus: () =>
    http<{has_profile: boolean; cdp_running: boolean}>("/api/profiles/authenticated/status"),
  authenticatedProfileStart: () =>
    http<{message: string}>("/api/profiles/authenticated/start", {method: "POST"}),
  authenticatedProfileStop: () =>
    http<{message: string}>("/api/profiles/authenticated/stop", {method: "POST"}),
  authenticatedProfileConfig: () =>
    http<AuthenticatedConfig>("/api/profiles/authenticated/config"),
  authenticatedProfileUpdateConfig: (cfg: Partial<AuthenticatedConfig>) =>
    http<AuthenticatedConfig>("/api/profiles/authenticated/config", {
      method: "PUT",
      body: JSON.stringify(cfg),
      headers: {"Content-Type": "application/json"},
    }),
};

export interface AuthenticatedConfig {
  cdp_port: number;
  authenticated_domains: string[];
  chrome_path: string | null;
}

export interface BrowserState {
  active: boolean;
  drone_id?: string;
  profile?: string;
  url?: string;
  title?: string;
  action?: string;
  screenshot_path?: string;
  screenshot_b64?: string;
  screenshot_bytes?: number;
  ts?: string;
}
