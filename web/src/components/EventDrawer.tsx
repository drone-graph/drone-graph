import { For, Show, createMemo, createSignal } from "solid-js";

import { store } from "../state";

const KIND_TAG: Record<string, string> = {
  "user.prompt": "cobalt",
  "user.retire": "copper",
  "user.rewrite_intent": "cobalt",
  "drone.spawn": "cobalt",
  "drone.reaped": "graphite",
  "drone.cancel_signaled": "copper",
  "drone.hard_killed": "copper",
  "scheduler.start": "teal",
  "scheduler.stop": "graphite",
  "scheduler.cost_locked": "copper",
  "scheduler.tick_cadence": "amber",
  "install.pending": "amber",
  "install.resolved": "teal",
  "controller.paused": "amber",
  "controller.resumed": "cobalt",
};

export function EventDrawer() {
  const [open, setOpen] = createSignal(false);
  const [filter, setFilter] = createSignal("");
  const filtered = createMemo(() => {
    const f = filter().trim().toLowerCase();
    const events = store.events_tail;
    if (!f) return events.slice(-200);
    return events.filter((e) => JSON.stringify(e).toLowerCase().includes(f)).slice(-200);
  });
  const latest = createMemo(
    () => store.events_tail[store.events_tail.length - 1],
  );

  return (
    <div class="drawer" classList={{ open: open() }}>
      <div class="strip" onClick={() => setOpen((v) => !v)}>
        <Show when={latest()}>
          <span class={`tag ${KIND_TAG[latest()!.event as string] ?? "graphite"}`}>
            {latest()!.event}
          </span>
          <span class="dim" style={{ "margin-left": "8px" }}>
            {summary(latest()!)}
          </span>
        </Show>
        <span class="hint faint" style={{ "margin-left": "auto" }}>
          {open() ? "▼ close" : "▲ event tape"}
        </span>
      </div>
      <Show when={open()}>
        <div class="body">
          <div class="filter">
            <input
              placeholder="filter events…"
              value={filter()}
              onInput={(e) => setFilter(e.currentTarget.value)}
            />
          </div>
          <div class="rows">
            <For each={filtered()}>
              {(e) => (
                <div class="event">
                  <span class={`tag ${KIND_TAG[e.event as string] ?? "graphite"}`}>
                    {e.event}
                  </span>
                  <span class="faint mono" style={{ "min-width": "70px" }}>
                    {(e.ts as string | undefined)?.slice(11, 19) ?? ""}
                  </span>
                  <span class="summary">{summary(e)}</span>
                </div>
              )}
            </For>
          </div>
        </div>
      </Show>
      <style>{`
        .drawer {
          position: absolute;
          left: 0;
          right: 0;
          bottom: 0;
          background: var(--bg-1);
          border-top: 1px solid var(--border);
          z-index: 4;
          transition: height 240ms var(--ease);
          height: var(--drawer-h);
          overflow: hidden;
        }
        .drawer.open { height: var(--drawer-h-open); }
        .strip {
          display: flex;
          align-items: center;
          gap: 8px;
          height: var(--drawer-h);
          padding: 0 14px;
          cursor: pointer;
          font-size: var(--fs-sm);
        }
        .strip:hover { background: var(--bg-2); }
        .body {
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding: 8px 14px 14px;
          height: calc(var(--drawer-h-open) - var(--drawer-h));
          overflow: hidden;
        }
        .filter {
          flex: 0 0 auto;
        }
        .filter input {
          font-size: var(--fs-sm);
          padding: 4px 8px;
        }
        .rows {
          flex: 1;
          overflow-y: auto;
          display: flex;
          flex-direction: column;
          gap: 3px;
        }
        .event {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: var(--fs-xs);
          padding: 2px 0;
        }
        .event .summary {
          color: var(--fg-1);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
      `}</style>
    </div>
  );
}

function summary(e: { event?: string;[k: string]: unknown }): string {
  // Pull out a few high-signal fields for the strip view.
  const k = String(e.event ?? "");
  if (k === "drone.spawn") {
    return `${e.role} on ${truncate(String(e.gap_id ?? ""), 12)} (tick ${e.tick ?? "?"})`;
  }
  if (k === "drone.reaped") {
    return `${e.role} → ${e.outcome} after ${e.latency_s ?? "?"}s`;
  }
  if (k === "user.prompt") {
    return truncate(String(e.summary ?? ""), 90);
  }
  if (k === "user.retire") {
    return `gap ${truncate(String(e.gap_id ?? ""), 10)} retired (${e.reason})`;
  }
  if (k === "scheduler.tick_cadence") {
    return e.resting ? "resting (8s tick)" : "active (1s tick)";
  }
  if (k === "scheduler.cost_locked") {
    return `cost ceiling reached at $${e.spent_usd}`;
  }
  if (k === "install.pending") {
    return `${e.tool_name} requested by ${truncate(String(e.requested_by_drone_id ?? ""), 12)}`;
  }
  return JSON.stringify(Object.fromEntries(Object.entries(e).filter(([key]) => !key.startsWith("_") && key !== "event" && key !== "ts"))).slice(0, 120);
}
function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}
