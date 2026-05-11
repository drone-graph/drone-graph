import { For, Show, createMemo } from "solid-js";

import { api } from "../api";
import { focusDrone, selectGap, setView, store } from "../state";
import type { ActiveDrone, Tool } from "../types";

export function ActiveDronesRail() {
  const recentTools = createMemo<Tool[]>(() => {
    return [...store.tools]
      .sort(
        (a, b) =>
          new Date(b.last_used_at).getTime() -
          new Date(a.last_used_at).getTime(),
      )
      .slice(0, 8);
  });

  return (
    <aside class="drones-rail">
      <div class="head">
        <span class="dim" style={{ "font-size": "var(--fs-xs)", "letter-spacing": "0.08em" }}>
          ACTIVE DRONES · {store.active_drones.length}
        </span>
      </div>
      <div class="drones">
        <Show when={store.active_drones.length > 0} fallback={
          <div class="faint empty">
            <Show
              when={store.status?.state === "resting"}
              fallback="no drones in flight."
            >
              the hivemind is at rest.
            </Show>
          </div>
        }>
          <For each={store.active_drones}>
            {(d) => <DroneRow d={d} />}
          </For>
        </Show>
      </div>
      <div class="tool-head">
        <span class="dim" style={{ "font-size": "var(--fs-xs)", "letter-spacing": "0.08em" }}>
          RECENT TOOLS
        </span>
        <a class="faint link" onClick={() => setView("marketplace")}>
          marketplace →
        </a>
      </div>
      <div class="tools">
        <For each={recentTools()}>
          {(t) => (
            <div class="tool" onClick={() => setView("marketplace")}>
              <div class="row" style={{ "justify-content": "space-between" }}>
                <span style={{ "font-size": "var(--fs-sm)" }}>{t.name}</span>
                <span class={`tag ${tierTag(t.trust_tier)}`}>{t.trust_tier}</span>
              </div>
              <div class="faint" style={{ "font-size": "var(--fs-xs)" }}>
                {oneLine(t.description)}
              </div>
            </div>
          )}
        </For>
      </div>
      <style>{`
        .drones-rail {
          display: flex;
          flex-direction: column;
          width: var(--rail-w-right);
          background: var(--bg-1);
          border-left: 1px solid var(--border);
          height: 100%;
          min-height: 0;
        }
        .head {
          padding: 12px 14px 8px;
          border-bottom: 1px solid var(--border);
          flex-shrink: 0;
        }
        .drones {
          flex: 1 1 60%;
          min-height: 0;
          overflow-y: auto;
          padding: 8px 10px;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .drones .empty {
          padding: 30px 8px;
          text-align: center;
          font-size: var(--fs-sm);
        }
        .tool-head {
          padding: 8px 14px;
          border-top: 1px solid var(--border);
          border-bottom: 1px solid var(--border);
          display: flex;
          justify-content: space-between;
          align-items: center;
          flex-shrink: 0;
        }
        .tool-head .link { cursor: pointer; text-decoration: underline dotted; font-size: var(--fs-xs); }
        .tools {
          flex: 0 1 38%;
          min-height: 0;
          overflow-y: auto;
          padding: 8px 10px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .tool {
          padding: 6px 8px;
          border: 1px solid var(--border);
          border-radius: 3px;
          cursor: pointer;
          transition: background-color 120ms var(--ease);
        }
        .tool:hover { background: var(--bg-3); }
      `}</style>
    </aside>
  );
}

function DroneRow(props: { d: ActiveDrone }) {
  const turn = () => `${props.d.turn ?? "—"}/${props.d.max_turns ?? "?"}`;
  const cost = () => (props.d.cost_usd ?? 0).toFixed(3);
  const browserSnap = () => store.browser_state[props.d.gap_id] ?? null;
  const awaiting = () => !!browserSnap()?.awaiting_prompt;
  return (
    <div
      class="drone-row"
      classList={{
        cancelling: props.d.cancel_signaled,
        "has-browser": !!browserSnap(),
        awaiting: awaiting(),
      }}
      onClick={() => {
        selectGap(props.d.gap_id);
        focusDrone(props.d.gap_id);
      }}
    >
      <div class="row" style={{ "justify-content": "space-between" }}>
        <span class={`tag ${roleTag(props.d.role)}`}>{shortRole(props.d.role)}</span>
        <div class="row" style={{ gap: "4px" }}>
          <Show when={browserSnap()}>
            <span
              class="browser-indicator"
              classList={{ awaiting: awaiting() }}
              title={
                awaiting()
                  ? `drone needs you: ${browserSnap()?.awaiting_prompt ?? ""}`
                  : `live browser · ${browserSnap()?.url ?? ""}`
              }
            >
              ◐
            </span>
          </Show>
          <button
            class="ghost danger"
            onClick={(e) => {
              e.stopPropagation();
              void api.cancelDrone(props.d.gap_id);
            }}
            title="cancel drone"
            style={{ padding: "1px 6px", "font-size": "var(--fs-xs)" }}
          >
            ✕
          </button>
        </div>
      </div>
      <div class="intent" title={props.d.gap_id}>
        {props.d.gap_id.slice(0, 8)}
      </div>
      <Show when={props.d.model}>
        <div class="faint" style={{ "font-size": "var(--fs-xs)" }} title={`${props.d.provider ?? ""} · ${props.d.model_tier ?? ""}`}>
          {props.d.model}
        </div>
      </Show>
      <div class="row vitals">
        <span class="faint">turn {turn()}</span>
        <span class="faint">·</span>
        <span class="faint">${cost()}</span>
      </div>
      <Show when={(props.d.last_tool_calls?.length ?? 0) > 0}>
        <div class="now" title="tools called on the drone's most recent turn">
          now: <span class="now-tools">{(props.d.last_tool_calls ?? []).join(", ")}</span>
        </div>
      </Show>
      <Show when={props.d.last_command}>
        <div class="cmd" title={props.d.last_command ?? ""}>
          $ {oneLine(props.d.last_command ?? "")}
        </div>
      </Show>
      <Show when={props.d.tail_lines.length > 0}>
        <pre class="tail">{props.d.tail_lines.slice(-3).join("\n")}</pre>
      </Show>
      <style>{`
        .drone-row {
          background: var(--bg-2);
          border: 1px solid var(--border);
          border-radius: 3px;
          padding: 6px 8px;
          cursor: pointer;
          display: flex;
          flex-direction: column;
          gap: 3px;
          font-size: var(--fs-xs);
        }
        .drone-row:hover { border-color: var(--cobalt); }
        .drone-row.cancelling {
          border-color: var(--copper);
          opacity: 0.6;
        }
        .drone-row.has-browser {
          border-left: 2px solid var(--cobalt);
        }
        .drone-row.awaiting {
          border-color: var(--copper);
          animation: rowPulse 1.4s ease-in-out infinite;
        }
        @keyframes rowPulse {
          0%, 100% { background: var(--bg-2); }
          50% { background: rgba(200, 128, 40, 0.18); }
        }
        .browser-indicator {
          color: var(--cobalt-soft);
          font-size: 14px;
          line-height: 1;
          display: inline-flex;
          align-items: center;
        }
        .browser-indicator.awaiting {
          color: var(--copper);
        }
        .intent {
          font-size: var(--fs-sm);
          color: var(--fg-0);
        }
        .vitals { font-size: var(--fs-xs); }
        .now {
          font-size: 10.5px;
          color: var(--fg-1);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .now-tools {
          color: var(--cobalt-soft);
          font-family: var(--font-mono);
        }
        .cmd {
          color: var(--cobalt-soft);
          font-size: var(--fs-xs);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .tail {
          margin: 0;
          padding: 3px 4px;
          background: var(--bg-0);
          border: 1px solid var(--border);
          font-size: 10px;
          line-height: 1.3;
          max-height: 48px;
          overflow: hidden;
          color: var(--fg-1);
          white-space: pre-wrap;
        }
      `}</style>
    </div>
  );
}

function roleTag(r: ActiveDrone["role"]): string {
  if (r === "preset:gap_finding") return "cobalt";
  if (r === "preset:alignment") return "amber";
  return "teal";
}
function shortRole(r: ActiveDrone["role"]): string {
  return { "preset:gap_finding": "GF", "preset:alignment": "ALIGN", worker: "WORK" }[r];
}
function tierTag(t: Tool["trust_tier"]): string {
  return { high: "teal", standard: "cobalt", low: "amber", blocked: "copper" }[t];
}
function oneLine(s: string): string {
  const i = s.indexOf("\n");
  return (i === -1 ? s : s.slice(0, i)).trim();
}
