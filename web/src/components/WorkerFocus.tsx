import { Show, createMemo } from "solid-js";

import { api } from "../api";
import { focusDrone, store } from "../state";

export function WorkerFocus() {
  const drone = createMemo(() => {
    const gid = store.focused_drone_gap_id;
    if (!gid) return null;
    return store.active_drones.find((d) => d.gap_id === gid) ?? null;
  });
  const gap = createMemo(() => {
    const d = drone();
    if (!d) return null;
    return store.gaps.find((g) => g.id === d.gap_id) ?? null;
  });

  return (
    <Show when={drone()}>
      <div class="focus">
        <div class="head">
          <div class="col" style={{ gap: "2px" }}>
            <div class="row">
              <span class={`tag ${roleTag(drone()!.role)}`}>{drone()!.role}</span>
              <span class="dim mono" style={{ "font-size": "var(--fs-xs)" }}>
                {drone()!.drone_id}
              </span>
            </div>
            <div style={{ "font-size": "var(--fs-md)" }}>
              {gap()?.intent ? oneLine(gap()!.intent) : drone()!.gap_id.slice(0, 8)}
            </div>
            <div class="faint" style={{ "font-size": "var(--fs-xs)" }}>
              turn {drone()!.turn ?? "—"}/{drone()!.max_turns ?? "?"}
              {" · $"}{drone()!.cost_usd?.toFixed(3) ?? "—"}
              {" · tokens "}{drone()!.tokens_in ?? "—"}/{drone()!.tokens_out ?? "—"}
            </div>
          </div>
          <div class="row" style={{ "margin-left": "auto" }}>
            <button
              class="danger"
              onClick={() => void api.cancelDrone(drone()!.gap_id)}
            >
              cancel
            </button>
            <button class="ghost" onClick={() => focusDrone(null)}>
              close
            </button>
          </div>
        </div>
        <div class="terminal">
          <Show
            when={(drone()?.tail_lines.length ?? 0) > 0 || drone()?.last_command}
            fallback={
              <div class="faint" style={{ padding: "12px" }}>
                stdout will stream here once the drone runs a terminal command…
              </div>
            }
          >
            <Show when={drone()!.last_command}>
              <div class="cmd">$ {drone()!.last_command}</div>
            </Show>
            <pre class="lines">{drone()!.tail_lines.join("\n")}</pre>
          </Show>
        </div>
        <style>{`
          .focus {
            position: absolute;
            inset: 0;
            background: rgba(7, 10, 15, 0.94);
            backdrop-filter: blur(2px);
            display: flex;
            flex-direction: column;
            z-index: 3;
            border-left: 1px solid var(--cobalt-dim);
          }
          .focus .head {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            padding: 10px 14px;
            border-bottom: 1px solid var(--border);
            background: var(--bg-1);
          }
          .focus .terminal {
            flex: 1;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            padding: 10px 14px;
          }
          .focus .cmd {
            color: var(--cobalt-soft);
            font-size: var(--fs-sm);
            margin-bottom: 6px;
          }
          .focus .lines {
            margin: 0;
            flex: 1;
            overflow-y: auto;
            color: var(--fg-0);
            font-size: 12px;
            line-height: 1.45;
            white-space: pre-wrap;
            background: var(--bg-0);
            border: 1px solid var(--border);
            padding: 8px 10px;
          }
        `}</style>
      </div>
    </Show>
  );
}

function roleTag(r: string): string {
  if (r === "preset:gap_finding") return "cobalt";
  if (r === "preset:alignment") return "amber";
  return "teal";
}
function oneLine(s: string): string {
  const i = s.indexOf("\n");
  return (i === -1 ? s : s.slice(0, i)).trim();
}
