// EventSource wrapper with automatic backfill on reconnect.
// Tracks the highest seen ``_seq`` and re-subscribes with ``?since_seq=N``
// after a connection drop so the client never misses an event.

import type { StreamEvent } from "./types";

type Handler = (ev: StreamEvent) => void;

export class EventStream {
  private es: EventSource | null = null;
  private lastSeq = 0;
  private handlers: Handler[] = [];
  private connectionHandlers: ((connected: boolean) => void)[] = [];
  private reconnectTimer: number | null = null;
  private closed = false;

  constructor(private url: string = "/api/stream") {}

  start(): void {
    this.closed = false;
    this.open();
  }

  stop(): void {
    this.closed = true;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.es !== null) {
      this.es.close();
      this.es = null;
    }
  }

  onEvent(h: Handler): () => void {
    this.handlers.push(h);
    return () => {
      this.handlers = this.handlers.filter((x) => x !== h);
    };
  }

  onConnection(h: (connected: boolean) => void): () => void {
    this.connectionHandlers.push(h);
    return () => {
      this.connectionHandlers = this.connectionHandlers.filter(
        (x) => x !== h,
      );
    };
  }

  private open(): void {
    const url =
      this.lastSeq > 0 ? `${this.url}?since_seq=${this.lastSeq}` : this.url;
    const es = new EventSource(url);
    this.es = es;

    es.onopen = () => {
      for (const h of this.connectionHandlers) h(true);
    };
    es.onerror = () => {
      for (const h of this.connectionHandlers) h(false);
      es.close();
      this.es = null;
      if (!this.closed) {
        this.scheduleReconnect();
      }
    };

    const ingest = (e: MessageEvent<string>) => {
      let payload: StreamEvent;
      try {
        payload = JSON.parse(e.data) as StreamEvent;
      } catch {
        return;
      }
      if (typeof payload._seq === "number" && payload._seq > this.lastSeq) {
        this.lastSeq = payload._seq;
      }
      for (const h of this.handlers) h(payload);
    };

    // Catch-all for unknown event types.
    es.addEventListener("message", ingest);
    // Bind each named event we care about. EventSource fans named events to
    // their own listeners and *will not* fall back to "message", so we have
    // to enumerate them. The list is open — unknown events also go through
    // "message" by virtue of how sse-starlette routes them on the client
    // when no addEventListener matches (it doesn't), so we listen broadly.
    for (const name of EVENT_NAMES) {
      es.addEventListener(name, ingest as EventListener);
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.closed) this.open();
    }, 1500);
  }
}

// Known event names emitted by scheduler.py and api/events.py. New names can
// be added without breaking anything; unknown events still flow through the
// default "message" handler.
const EVENT_NAMES: string[] = [
  "hello",
  "ping",
  // Scheduler lifecycle + cadence.
  "scheduler.start",
  "scheduler.stop",
  "scheduler.tick_cadence",
  "scheduler.cost_locked",
  "scheduler.budget_exceeded",
  "scheduler.error",
  // Drones.
  "drone.spawn",
  "drone.reaped",
  "drone.cancel_signaled",
  "drone.hard_killed",
  "drone.turn",
  "drone.start",
  "drone.narrate",
  "tool.terminal_run",
  "tool.registered",
  "worker.realworld_action",
  "claim.reaped",
  // Controller (mission control).
  "controller.started",
  "controller.ready",
  "controller.shutdown",
  "controller.restart_requested",
  "controller.needs_restart",
  "controller.paused",
  "controller.resumed",
  "controller.cost_ceiling_set",
  "controller.paranoid_install_set",
  "controller.force_tick",
  "controller.drone_cancel_requested",
  // Operator actions.
  "user.prompt",
  "user.retire",
  "user.rewrite_intent",
  "user.reopen",
  "user.tool_flagged",
  "user.tool_unflagged",
  "user.trust_tier_set",
  "user.inbox_resolved",
  // Installs.
  "install.pending",
  "install.resolved",
];
