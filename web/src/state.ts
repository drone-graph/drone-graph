// Single source of truth for the UI. Holds the substrate snapshot plus a
// rolling buffer of stream events; SSE deltas mutate the signals and the
// components reactively re-render.
//
// We deliberately don't rebuild the full graph on every event — most events
// are observations (drone.turn, tool.terminal_run) that don't change graph
// shape. For events that DO change shape (gap creation, retire, fill,
// rewrite_intent) we cheaply refresh the snapshot from the server. This is
// O(milliseconds) on a local API; the cinematic cost is hidden behind the
// canvas animation budget.

import { batch, createSignal } from "solid-js";
import { createStore, produce } from "solid-js/store";

import { api } from "./api";
import type {
  ActiveDrone,
  Finding,
  Gap,
  InboxItem,
  PendingInstall,
  SettingsView,
  Snapshot,
  StreamEvent,
  SwarmStatus,
  Tool,
} from "./types";
import { playSound } from "./sound";

// ---- Chat messages ---------------------------------------------------------

export interface ChatMessage {
  id: string;
  author: "user" | "system" | "alignment" | "gap_finding" | "worker";
  /** For worker-author messages, a finer-grained classification so the UI
   *  can render an honest tag (a fill-narration is NOT an action request).
   *  ``action_needed`` — drone is blocked on a human action.
   *  ``narrate``       — end-of-drone summary; outcome may be fill/fail/etc.
   *  ``realworld_action`` — real-time external side-effect alert.
   *  Undefined for non-worker messages. */
  kind?: "action_needed" | "narrate" | "realworld_action";
  /** Worker outcome — surfaces only for ``kind=narrate``. */
  outcome?: string;
  text: string;
  ts: string;
  affected_gap_ids: string[];
  finding_id?: string;
}

// ---- Substrate store -------------------------------------------------------

/** Live state for a per-drone Chromium window. Populated from
 *  ``browser.state`` events on the SSE stream and from the
 *  /api/drones/{gap_id}/browser-state poll. ``screenshot_path`` is a path
 *  on the server's disk; the inline ``screenshot_b64`` is what the UI
 *  actually renders. */
export interface BrowserSnapshot {
  drone_id?: string;
  profile?: string;
  url?: string;
  title?: string;
  action?: string;
  ts?: string;
  /** Server-side path; informational, not rendered. */
  screenshot_path?: string;
  /** Base64-encoded PNG body for the latest screenshot. */
  screenshot_b64?: string;
  /** When the drone last emitted ``browser.await_operator`` — the panel
   *  highlights itself and the operator's reply unblocks the drone. */
  awaiting_prompt?: string;
  awaiting_finding_id?: string;
}

/** Per-drone chat thread (operator ↔ specific drone). Keyed by gap_id. */
export interface DroneChatMessage {
  id: string;
  author: "user" | "worker";
  text: string;
  ts: string;
  finding_id?: string;
}

interface SubstrateStore {
  loaded: boolean;
  status: SwarmStatus | null;
  gaps: Gap[];
  parent_edges: [string, string][];
  recent_findings: Finding[];
  active_drones: ActiveDrone[];
  tools: Tool[];
  pending_installs: PendingInstall[];
  chat: ChatMessage[];
  events_tail: StreamEvent[];
  connected: boolean;
  selected_gap_id: string | null;
  selected_finding_id: string | null;
  focused_drone_gap_id: string | null;
  view: "console" | "findings" | "marketplace" | "internals" | "settings";
  flash_gap_id: string | null;
  alignment_pulse_gap_id: string | null;
  settings: SettingsView | null;
  inbox: InboxItem[];
  /** Last-known browser state per gap. ``null`` = drone has no active
   *  Chromium window. The DroneAttachedChat panel reads from here. */
  browser_state: Record<string, BrowserSnapshot | null>;
  /** Operator ↔ drone chat threads. Per-gap (one drone per gap). */
  drone_chat: Record<string, DroneChatMessage[]>;
}

const initial: SubstrateStore = {
  loaded: false,
  status: null,
  gaps: [],
  parent_edges: [],
  recent_findings: [],
  active_drones: [],
  tools: [],
  pending_installs: [],
  chat: [],
  events_tail: [],
  connected: false,
  selected_gap_id: null,
  selected_finding_id: null,
  focused_drone_gap_id: null,
  view: "console",
  flash_gap_id: null,
  alignment_pulse_gap_id: null,
  settings: null,
  inbox: [],
  browser_state: {},
  drone_chat: {},
};

const [store, setStore] = createStore<SubstrateStore>(initial);
export { store };

// ---- Bootstrap -------------------------------------------------------------

export async function loadSnapshot(): Promise<void> {
  const [snap, sv, inbox] = await Promise.all([
    api.snapshot(),
    api.settings().catch(() => null),
    api.inbox().catch(() => [] as InboxItem[]),
  ]);
  applySnapshot(snap);
  if (sv) setStore("settings", sv);
  setStore("inbox", inbox ?? []);
}

export async function refreshSettings(): Promise<void> {
  try {
    setStore("settings", await api.settings());
  } catch {
    /* ignore */
  }
}

export async function refreshInbox(): Promise<void> {
  try {
    setStore("inbox", await api.inbox());
  } catch {
    /* ignore */
  }
}

export function isUnconfigured(): boolean {
  const s = store.status;
  if (!s) return false;
  return s.provider === "unconfigured" || s.run_id === "(not started)";
}

// ---- Inbox resolutions (declined / resolved blocks) ---------------------
//
// When the operator resolves an action item, the substrate writes a
// ``note`` finding with author=user and an artefact path of the form
// ``inbox-resolution:<block_finding_id>``. We index those resolutions
// here so the chat panel can collapse the original action_needed message
// into a single muted "[declined] reason" row instead of leaving the
// full call-to-action visible forever.

export interface InboxResolution {
  outcome: string; // resolved | declined | skipped
  note: string;    // operator's own explanation
  ts: string;
  resolution_finding_id: string;
}

export function inboxResolutions(): Map<string, InboxResolution> {
  // Built from store.recent_findings on demand. Cheap — recent_findings
  // is a short list (<= 200 items typical).
  const out = new Map<string, InboxResolution>();
  for (const f of store.recent_findings) {
    if (f.author !== "user" || f.kind !== "note") continue;
    for (const p of f.artefact_paths) {
      if (typeof p !== "string" || !p.startsWith("inbox-resolution:")) continue;
      const blockId = p.split(":", 2)[1];
      if (!blockId) continue;
      // Parse the user-facing summary the resolve endpoint wrote, which
      // starts with "User responded to block <id>: <outcome>." followed
      // by an optional operator note.
      const summary = f.summary;
      const m = /User responded to block \S+:\s*(\w+)\.?\s*(.*)/s.exec(summary);
      const outcome = (m?.[1] ?? "resolved").toLowerCase();
      const note = (m?.[2] ?? "").trim();
      out.set(blockId, {
        outcome,
        note,
        ts: f.created_at,
        resolution_finding_id: f.id,
      });
    }
  }
  return out;
}

export function applySnapshot(s: Snapshot): void {
  batch(() => {
    setStore("status", s.status);
    setStore("gaps", s.gaps);
    setStore("parent_edges", s.parent_edges);
    setStore("recent_findings", s.recent_findings);
    setStore("active_drones", s.active_drones);
    setStore("tools", s.tools);
    setStore("loaded", true);
    setStore("chat", deriveChatFromFindings(s.recent_findings));
  });
}

// ---- Helpers ---------------------------------------------------------------

function deriveChatFromFindings(findings: Finding[]): ChatMessage[] {
  // Chat is the operator ↔ hivemind conversation surface ONLY.
  //
  // Internal findings (rewrite_intent, alignment_*) are post-it notes the
  // swarm writes to itself, not communication aimed at the operator. We
  // intentionally do NOT surface them in chat — they show up in the event
  // drawer, the gap detail overlay, and the canvas instead. Surfacing them
  // in chat reads as authoritative status, which is misleading: findings
  // are GF's interpretations, not ground truth.
  //
  // The exception is ``requires_user_action`` — that IS a direct message
  // from a worker to the operator, asking for a credential, an OAuth flow,
  // a purchase approval, etc. Those belong in chat.
  const out: ChatMessage[] = [];
  for (const f of findings) {
    if (f.author === "user" && f.kind === "user_input") {
      out.push({
        id: f.id,
        author: "user",
        text: f.summary,
        ts: f.created_at,
        affected_gap_ids: f.affected_gap_ids,
        finding_id: f.id,
      });
    } else if (f.kind === "requires_user_action") {
      out.push({
        id: f.id,
        author: "worker",
        kind: "action_needed",
        text: f.summary,
        ts: f.created_at,
        affected_gap_ids: f.affected_gap_ids,
        finding_id: f.id,
      });
    }
  }
  return out.slice(-80);
}

function oneLine(s: string): string {
  const i = s.indexOf("\n");
  return (i === -1 ? s : s.slice(0, i)).trim();
}

// ---- Event ingest ----------------------------------------------------------

const STRUCTURAL_REFRESH_EVENTS = new Set([
  "drone.reaped",
  // user.prompt counts: the seed-the-swarm flow flips isEmpty() the moment
  // the substrate has a user_input finding, and we need that to land in
  // recent_findings before the next drone.reaped (which can be many
  // seconds away on the first GF tick).
  "user.prompt",
  "user.retire",
  "user.reopen",
  "user.rewrite_intent",
  "scenario.inject",
]);

let refreshScheduled = false;
async function scheduleRefresh(): Promise<void> {
  if (refreshScheduled) return;
  refreshScheduled = true;
  // Coalesce — many events can land in a single tick.
  setTimeout(async () => {
    refreshScheduled = false;
    try {
      const s = await api.snapshot();
      applySnapshot(s);
    } catch {
      // Network blips are normal on local-only dev.
    }
  }, 120);
}

export function ingestEvent(ev: StreamEvent): void {
  setStore(
    produce((d) => {
      d.events_tail.push(ev);
      if (d.events_tail.length > 400) {
        d.events_tail.splice(0, d.events_tail.length - 400);
      }
    }),
  );

  const kind = ev.event;
  if (STRUCTURAL_REFRESH_EVENTS.has(kind)) {
    void scheduleRefresh();
  }
  if (kind === "user.prompt") {
    setStore(
      "chat",
      produce((c) => {
        c.push({
          id: String(ev.finding_id ?? Math.random()),
          author: "user",
          text: String(ev.summary ?? ""),
          ts: String(ev.ts ?? new Date().toISOString()),
          affected_gap_ids: (ev.affected_gap_ids as string[] | undefined) ?? [],
          finding_id: String(ev.finding_id ?? ""),
        });
        if (c.length > 200) c.splice(0, c.length - 200);
      }),
    );
  }
  if (kind === "drone.spawn") {
    void refreshActive();
    playSound("spawn");
  }
  if (kind === "drone.reaped") {
    void refreshActive();
    const outcome = String(ev.outcome ?? "");
    if (outcome === "fill_or_preset_done") playSound("settle");
  }
  if (kind === "drone.cancel_signaled" || kind === "drone.hard_killed") {
    void refreshActive();
    playSound("disperse");
  }
  if (kind === "user.prompt") {
    playSound("prompt");
  }
  if (kind.startsWith("scheduler.")) {
    void refreshStatus();
    if (kind === "scheduler.tick_cadence") {
      // Cadence change — usually resting <-> active. Refresh status to update
      // the top-bar indicator.
    }
    if (kind === "scheduler.cost_locked") {
      playSound("alert");
    }
  }
  if (kind === "controller.paused" || kind === "controller.resumed" || kind === "controller.cost_ceiling_set" || kind === "controller.paranoid_install_set") {
    void refreshStatus();
  }
  if (kind === "install.pending") {
    void refreshPendingInstalls();
  }
  if (kind === "install.resolved") {
    void refreshPendingInstalls();
    void refreshTools();
  }
  if (kind === "tool.registered") {
    void refreshTools();
  }
  if (kind === "worker.realworld_action") {
    // Heuristic side-effect detection from the worker tool. Surface in
    // chat the moment it fires so the operator knows the swarm is doing
    // something external (a deploy, a push, an email, an external POST).
    setStore(
      "chat",
      produce((c) => {
        const desc = String(ev.description ?? "drone is taking an external action");
        const cmd = String(ev.cmd ?? "");
        c.push({
          id: `rwa-${ev._seq ?? Math.random()}`,
          author: "worker",
          kind: "realworld_action",
          text: `${desc}${cmd ? `\n$ ${cmd.slice(0, 200)}` : ""}`,
          ts: String(ev.ts ?? new Date().toISOString()),
          affected_gap_ids:
            typeof ev.gap_id === "string" ? [ev.gap_id] : [],
        });
        if (c.length > 200) c.splice(0, c.length - 200);
      }),
    );
  }
  if (kind === "drone.narrate") {
    // End-of-drone chat summary written by the nano-tier model in the
    // runtime. One per drone exit. Plain English from the drone to the
    // operator. The kind=narrate tag lets the UI label it as a status
    // update rather than an action request — fill outcomes are NOT
    // things the operator needs to do something about.
    const text = String(ev.text ?? "");
    if (text) {
      const outcome =
        typeof ev.outcome === "string" ? ev.outcome : undefined;
      setStore(
        "chat",
        produce((c) => {
          c.push({
            id: `narr-${String(ev.finding_id ?? ev._seq ?? Math.random())}`,
            author: "worker",
            kind: "narrate",
            outcome,
            text,
            ts: String(ev.ts ?? new Date().toISOString()),
            affected_gap_ids:
              typeof ev.gap_id === "string" ? [ev.gap_id] : [],
            finding_id:
              typeof ev.finding_id === "string" ? ev.finding_id : undefined,
          });
          if (c.length > 200) c.splice(0, c.length - 200);
        }),
      );
    }
  }
  if (kind === "browser.state") {
    // A drone took a cm_browser action. We refresh the snapshot lazily
    // via the API endpoint (it returns base64 inline) so we don't have
    // to plumb screenshot bytes through SSE.
    const gid = ev.gap_id as string | undefined;
    if (gid) void refreshBrowserState(gid);
  }
  if (kind === "browser.await_operator") {
    const gid = ev.gap_id as string | undefined;
    const prompt = typeof ev.prompt === "string" ? ev.prompt : "";
    const askId = typeof ev.ask_finding_id === "string" ? ev.ask_finding_id : "";
    if (gid) {
      setStore(
        "browser_state",
        produce((b) => {
          const cur = b[gid] ?? null;
          b[gid] = {
            ...(cur ?? {}),
            awaiting_prompt: prompt,
            awaiting_finding_id: askId,
          };
        }),
      );
      // Drone wrote a chat_with_drone finding (author=worker) — append to
      // the per-drone chat thread immediately for snappy UX.
      setStore(
        "drone_chat",
        produce((c) => {
          const thread = c[gid] ?? [];
          thread.push({
            id: askId || `await-${Math.random()}`,
            author: "worker",
            text: prompt,
            ts: String(ev.ts ?? new Date().toISOString()),
            finding_id: askId || undefined,
          });
          c[gid] = thread.slice(-100);
        }),
      );
      playSound("alert");
    }
  }
  if (kind === "browser.close") {
    const gid = ev.gap_id as string | undefined;
    if (gid) {
      setStore(
        "browser_state",
        produce((b) => {
          b[gid] = null;
        }),
      );
    }
  }
  if (kind === "chat.drone") {
    // Echo of the operator's own message landing in the substrate. Append
    // to the per-drone thread for that gap.
    const gid = ev.gap_id as string | undefined;
    const text = String(ev.text ?? "");
    const findingId =
      typeof ev.finding_id === "string" ? ev.finding_id : undefined;
    if (gid && text) {
      setStore(
        "drone_chat",
        produce((c) => {
          const thread = c[gid] ?? [];
          const exists = findingId
            ? thread.some((m) => m.finding_id === findingId)
            : false;
          if (!exists) {
            thread.push({
              id: findingId ?? `chat-${Math.random()}`,
              author: "user",
              text,
              ts: String(ev.ts ?? new Date().toISOString()),
              finding_id: findingId,
            });
            c[gid] = thread.slice(-100);
          }
        }),
      );
    }
  }
  if (kind === "controller.ready") {
    void refreshStatus();
    void refreshSettings();
  }
  if (kind === "controller.needs_restart") {
    void refreshStatus();
  }
  if (kind === "controller.restart_requested") {
    // Full reload — the controller is brand new with a fresh run_id.
    void loadSnapshot();
  }
  if (kind === "user.inbox_resolved") {
    void refreshInbox();
  }
  // Heuristic: any new finding might be a fresh `requires_user_action`. We
  // re-poll the inbox cheaply; the endpoint is small.
  if (kind === "drone.reaped" || kind === "scenario.inject" || kind === "user.prompt") {
    void refreshInbox();
  }
  // Track alignment findings so the canvas can pulse the affected gap.
  if (kind.startsWith("scheduler.") && typeof ev.gap_id === "string") {
    // no-op placeholder for richer alignment cues
  }
  if (kind === "scheduler.alignment_finding" && typeof ev.gap_id === "string") {
    setStore("alignment_pulse_gap_id", ev.gap_id as string);
    setTimeout(() => setStore("alignment_pulse_gap_id", null), 1800);
    playSound("dissent");
  }
}

async function refreshStatus(): Promise<void> {
  try {
    const s = await api.status();
    setStore("status", s);
  } catch {
    /* ignore */
  }
}

async function refreshActive(): Promise<void> {
  try {
    const drones = (await fetch("/api/drones/active").then((r) =>
      r.json(),
    )) as ActiveDrone[];
    setStore("active_drones", drones);
  } catch {
    /* ignore */
  }
}

async function refreshTools(): Promise<void> {
  try {
    setStore("tools", await api.tools());
  } catch {
    /* ignore */
  }
}

async function refreshPendingInstalls(): Promise<void> {
  try {
    setStore("pending_installs", await api.pendingInstalls());
  } catch {
    /* ignore */
  }
}

export async function refreshBrowserState(gap_id: string): Promise<void> {
  try {
    const s = await api.browserState(gap_id);
    setStore(
      "browser_state",
      produce((b) => {
        if (!s.active) {
          b[gap_id] = null;
          return;
        }
        const existing = b[gap_id] ?? null;
        b[gap_id] = {
          drone_id: s.drone_id,
          profile: s.profile,
          url: s.url,
          title: s.title,
          action: s.action,
          ts: s.ts,
          screenshot_path: s.screenshot_path,
          screenshot_b64: s.screenshot_b64,
          // Preserve await_operator marker if it's outstanding; the
          // resume path clears it explicitly when the operator replies.
          awaiting_prompt: existing?.awaiting_prompt,
          awaiting_finding_id: existing?.awaiting_finding_id,
        };
      }),
    );
  } catch {
    /* ignore — drone may have just exited */
  }
}

export async function sendDroneChat(
  gap_id: string,
  text: string,
): Promise<void> {
  const t = text.trim();
  if (!t) return;
  // Optimistically append to the thread so the input clears instantly.
  setStore(
    "drone_chat",
    produce((c) => {
      const thread = c[gap_id] ?? [];
      thread.push({
        id: `local-${Math.random()}`,
        author: "user",
        text: t,
        ts: new Date().toISOString(),
      });
      c[gap_id] = thread.slice(-100);
    }),
  );
  try {
    const r = await api.chatWithDrone(gap_id, t);
    // Clear the await_operator marker — the drone will pick up the
    // message on its next poll/turn and resume.
    setStore(
      "browser_state",
      produce((b) => {
        const cur = b[gap_id];
        if (cur && cur.awaiting_prompt) {
          b[gap_id] = {
            ...cur,
            awaiting_prompt: undefined,
            awaiting_finding_id: undefined,
          };
        }
      }),
    );
    void r;
  } catch (e) {
    void e;
  }
}

// ---- Connection state ------------------------------------------------------

export function setConnected(c: boolean): void {
  setStore("connected", c);
}

// ---- View / selection actions ---------------------------------------------

export function setView(v: SubstrateStore["view"]): void {
  setStore("view", v);
}

export function selectGap(id: string | null): void {
  setStore("selected_gap_id", id);
}

export function selectFinding(id: string | null): void {
  setStore("selected_finding_id", id);
}

export function focusDrone(gap_id: string | null): void {
  setStore("focused_drone_gap_id", gap_id);
}

// ---- Polling fallback ------------------------------------------------------

const [pollingActive, setPollingActive] = createSignal(false);
export { pollingActive, setPollingActive };

// Periodically resync vitals for active drones even when there are no
// events for them. The per-drone tape file refreshes the tail; this just
// pulls it server-side.
export function startVitalsPolling(): () => void {
  let timer: number | null = window.setInterval(() => {
    void refreshActive();
  }, 1200);
  return () => {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  };
}
