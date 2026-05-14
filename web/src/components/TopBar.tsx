import { Show, createMemo, createSignal } from "solid-js";

import { api } from "../api";
import { InboxBadge } from "./ActionInbox";
import { isSoundEnabled, setSoundEnabled, unlockAudio } from "../sound";
import { setView, store } from "../state";

export function TopBar() {
  const [soundOn, setSoundOn] = createSignal(false);
  const [showCeilingEditor, setShowCeilingEditor] = createSignal(false);
  const status = createMemo(() => store.status);

  // Compact "models currently in flight" summary — replaces the old
  // static model label which only showed the operator's default and not
  // what was actually being charged per drone.
  const activeModelsSummary = createMemo(() => {
    const counts = new Map<string, number>();
    for (const d of store.active_drones) {
      const m = d.model;
      if (!m) continue;
      counts.set(m, (counts.get(m) ?? 0) + 1);
    }
    if (counts.size === 0) return "";
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([m, n]) => (n > 1 ? `${m} ×${n}` : m))
      .join(", ");
  });

  const stateLabel = createMemo(() => {
    const s = status();
    if (!s) return "boot";
    return {
      idle: "◌ idle",
      active: "◉ active",
      paused: "⏸ paused",
      cost_locked: "⏸ ceiling",
      resting: "◌ resting",
      stopped: "✕ stopped",
    }[s.state];
  });

  const stateColor = createMemo(() => {
    const s = status();
    if (!s) return "var(--fg-2)";
    return {
      idle: "var(--fg-2)",
      active: "var(--cobalt)",
      paused: "var(--amber)",
      cost_locked: "var(--copper)",
      resting: "var(--teal-dim)",
      stopped: "var(--copper)",
    }[s.state];
  });

  const spent = createMemo(() => status()?.cost_spent_usd ?? 0);
  const ceiling = createMemo(() => status()?.cost_ceiling_usd ?? null);
  const ratio = createMemo(() => {
    const c = ceiling();
    if (c === null || c <= 0) return 0;
    return Math.max(0, Math.min(1.05, spent() / c));
  });
  const meterColor = createMemo(() => {
    const r = ratio();
    if (r >= 0.95) return "var(--copper)";
    if (r >= 0.85) return "var(--amber)";
    if (r >= 0.7) return "var(--amber)";
    return "var(--cobalt)";
  });

  function toggleSound() {
    unlockAudio();
    const next = !soundOn();
    setSoundOn(next);
    setSoundEnabled(next);
  }

  function togglePause() {
    if (status()?.paused) {
      void api.resume();
    } else {
      void api.pause();
    }
  }

  return (
    <div class="topbar">
      <div class="brand">
        <span class="dot" />
        <span class="title">
          DRONE GRAPH<span class="title-long"> · MISSION CONTROL</span>
        </span>
        <span class="dim" style={{ "margin-left": "10px" }} title="provider — workers and presets route per-tier; hover any active drone in the right rail to see its specific model">
          {status()?.provider ?? "—"}
          <Show when={activeModelsSummary()}>
            <span class="faint" style={{ "margin-left": "6px" }}>
              · {activeModelsSummary()}
            </span>
          </Show>
        </span>
      </div>

      <div class="state" style={{ color: stateColor() }}>
        {stateLabel()}
        <Show when={status()?.tick_seconds && status()!.tick_seconds > 4}>
          <span class="faint" style={{ "margin-left": "6px" }}>
            ({(status()!.tick_seconds).toFixed(0)}s)
          </span>
        </Show>
      </div>

      <CostMeter
        spent={spent()}
        ceiling={ceiling()}
        ratio={ratio()}
        color={meterColor()}
        onClick={() => setShowCeilingEditor((v) => !v)}
      />

      <Show when={showCeilingEditor()}>
        <CeilingEditor close={() => setShowCeilingEditor(false)} />
      </Show>

      <div class="nav">
        <button
          class="ghost"
          onClick={() => setView("console")}
          classList={{ active: store.view === "console" }}
        >
          console
        </button>
        <button
          class="ghost"
          onClick={() => setView("tools")}
          classList={{ active: store.view === "tools" }}
        >
          tools
        </button>
        <button
          class="ghost"
          onClick={() => setView("settings")}
          classList={{ active: store.view === "settings" }}
        >
          settings
        </button>
      </div>

      <div class="actions">
        <InboxBadge />
        <button class="ghost" onClick={togglePause} title="pause / resume">
          {status()?.paused ? "▶" : "❚❚"}
        </button>
        <button class="ghost" onClick={toggleSound} title="sound">
          {soundOn() ? "♪" : "·"}
        </button>
        <span
          class="conn"
          classList={{ live: store.connected, dead: !store.connected }}
          title={store.connected ? "live" : "reconnecting…"}
        />
      </div>

      <style>{TOPBAR_CSS}</style>
    </div>
  );
}

function CostMeter(props: {
  spent: number;
  ceiling: number | null;
  ratio: number;
  color: string;
  onClick: () => void;
}) {
  return (
    <div class="cost" onClick={props.onClick} role="button" tabindex={0}>
      <div class="bar">
        <div
          class="fill"
          style={{
            width: `${Math.min(100, props.ratio * 100)}%`,
            "background-color": props.color,
          }}
        />
      </div>
      <div class="label">
        ${props.spent.toFixed(2)}
        <Show when={props.ceiling !== null} fallback={<span class="dim"> / unlimited</span>}>
          <span class="dim"> / ${props.ceiling!.toFixed(2)}</span>
        </Show>
      </div>
    </div>
  );
}

function CeilingEditor(props: { close: () => void }) {
  const [val, setVal] = createSignal(
    store.status?.cost_ceiling_usd?.toString() ?? "",
  );
  async function save() {
    const raw = val().trim();
    const n = raw === "" ? null : Number(raw);
    if (raw !== "" && !Number.isFinite(n!)) return;
    await api.setCeiling(n);
    props.close();
  }
  return (
    <div class="ceiling-editor" onClick={(e) => e.stopPropagation()}>
      <div class="dim" style={{ "font-size": "var(--fs-xs)" }}>
        SWARM COST CEILING (USD)
      </div>
      <input
        autofocus
        value={val()}
        placeholder="unlimited"
        onInput={(e) => setVal(e.currentTarget.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void save();
          if (e.key === "Escape") props.close();
        }}
      />
      <div class="row" style={{ "justify-content": "flex-end", "margin-top": "6px" }}>
        <button onClick={props.close}>cancel</button>
        <button class="primary" onClick={save}>
          set
        </button>
      </div>
    </div>
  );
}

const TOPBAR_CSS = `
.topbar {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) auto minmax(140px, 220px) auto auto;
  align-items: center;
  gap: 12px;
  padding: 0 12px;
  height: var(--topbar-h);
  background: var(--bg-1);
  border-bottom: 1px solid var(--border);
  font-size: var(--fs-sm);
  position: relative;
  z-index: 5;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
  letter-spacing: 0.08em;
  min-width: 0;
  overflow: hidden;
}
.brand .title { white-space: nowrap; }
.brand > .dim {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Narrow desktop: drop the provider/active-models tail. */
@media (max-width: 1320px) {
  .brand > .dim { display: none; }
}
/* Narrower: shorten the title to "DRONE GRAPH". */
@media (max-width: 1200px) {
  .brand .title-long { display: none; }
}
/* Narrower still: hide the (xx s) tick suffix and the cost label dim suffix. */
@media (max-width: 980px) {
  .state .faint { display: none; }
  .cost .label .dim { display: none; }
}
.brand .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--cobalt);
  box-shadow: 0 0 8px var(--cobalt);
  animation: heartbeat var(--beat-ms) var(--ease) infinite;
}
.brand .title {
  font-size: var(--fs-sm);
}

.state {
  font-size: var(--fs-sm);
  font-weight: 500;
  letter-spacing: 0.04em;
}

.cost {
  display: flex;
  flex-direction: column;
  gap: 3px;
  cursor: pointer;
}
.cost:hover { opacity: 0.85; }
.cost .bar {
  width: 100%;
  height: 6px;
  background: var(--bg-2);
  border-radius: 2px;
  overflow: hidden;
  border: 1px solid var(--border);
}
.cost .fill {
  height: 100%;
  transition: width 400ms var(--ease), background-color 200ms var(--ease);
  box-shadow: 0 0 6px currentColor;
}
.cost .label {
  font-size: var(--fs-xs);
  letter-spacing: 0.02em;
  text-align: right;
}

.ceiling-editor {
  position: absolute;
  top: calc(var(--topbar-h) + 4px);
  right: 200px;
  background: var(--bg-1);
  border: 1px solid var(--border-strong);
  padding: 10px 12px;
  border-radius: 4px;
  min-width: 240px;
  z-index: 10;
}

.nav {
  display: flex;
  gap: 4px;
}
.nav button { padding: 4px 10px; }
.nav button.active {
  color: var(--cobalt-soft);
  background: var(--bg-2);
}

.actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.actions button { padding: 4px 8px; min-width: 28px; }
.actions button.active {
  color: var(--amber);
}

.conn {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-left: 6px;
  border-radius: 50%;
  background: var(--fg-3);
}
.conn.live { background: var(--cobalt); box-shadow: 0 0 6px var(--cobalt); }
.conn.dead { background: var(--copper); animation: blink 1s steps(2, end) infinite; }
`;
