import { For, Show, createEffect, createResource } from "solid-js";

import { api } from "../api";
import { selectGap, store } from "../state";
import type { Finding, Gap } from "../types";

import { GapChat } from "./GapChat";

export function GapDetailOverlay() {
  const gap = () => {
    const id = store.selected_gap_id;
    if (!id) return null;
    return store.gaps.find((g) => g.id === id) ?? null;
  };

  const [findings, { refetch }] = createResource<Finding[], string | null>(
    () => store.selected_gap_id,
    async (id: string | null) => {
      if (!id) return [];
      try {
        return await api.findingsForGap(id, 60);
      } catch {
        return [];
      }
    },
  );

  createEffect(() => {
    // Auto-refresh when new findings stream in for this gap.
    void store.recent_findings.length;
    void refetch();
  });

  return (
    <Show when={gap()}>
      <div class="gap-detail">
        <div class="head">
          <div class="row" style={{ "justify-content": "space-between", width: "100%" }}>
            <div class="row">
              <span class={`tag ${statusTag(gap()!.status)}`}>{gap()!.status}</span>
              <Show when={gap()!.preset_kind}>
                <span class="tag amber">preset:{gap()!.preset_kind}</span>
              </Show>
              <Show when={gap()!.paused}>
                <span class="tag copper" title="operator paused this gap">paused</span>
              </Show>
              <Show when={gap()!.uses_operator_identity}>
                <Show
                  when={gap()!.identity_approved}
                  fallback={
                    <span class="tag amber" title="awaiting your approval in the action inbox">
                      identity: pending
                    </span>
                  }
                >
                  <span class="tag teal">identity: approved</span>
                </Show>
              </Show>
              <span class="mono faint" style={{ "font-size": "var(--fs-xs)" }}>
                {gap()!.id}
              </span>
            </div>
            <button class="ghost" onClick={() => selectGap(null)}>
              close
            </button>
          </div>
        </div>
        <div class="body">
          <Section title="intent">
            <p class="whitespace">{gap()!.intent}</p>
          </Section>
          <Section title="criteria">
            <p class="whitespace">{gap()!.criteria}</p>
          </Section>
          <Show when={gap()!.retire_reason}>
            <Section title="retire reason">
              <p class="whitespace dim">{gap()!.retire_reason}</p>
            </Section>
          </Show>
          <Show when={gap()!.tool_loadout.length > 0}>
            <Section title="tool loadout">
              <div class="row" style={{ "flex-wrap": "wrap", gap: "4px" }}>
                <For each={gap()!.tool_loadout}>
                  {(t) => <span class="tag graphite">{t}</span>}
                </For>
              </div>
            </Section>
          </Show>
          <Section title="chat with this gap">
            <GapChat gapId={gap()!.id} />
          </Section>
          <Section title="findings on this gap">
            <Show
              when={(findings() ?? []).length > 0}
              fallback={<p class="faint">none yet.</p>}
            >
              <div class="findings">
                <For each={findings()}>
                  {(f) => <FindingRow f={f} />}
                </For>
              </div>
            </Show>
          </Section>
        </div>
        <div class="actions">
          <Actions g={gap()!} />
        </div>
        <style>{`
          .gap-detail {
            position: absolute;
            right: var(--rail-w-right);
            /* Sit below the top bar (and action banner if present). The
             * containing block is the .dashboard element which spans the
             * full viewport, so top:0 would put us behind the top bar. */
            top: var(--topbar-h);
            bottom: var(--drawer-h);
            width: min(560px, 50%);
            background: var(--bg-1);
            border-left: 1px solid var(--border-strong);
            display: flex;
            flex-direction: column;
            z-index: 6;
            box-shadow: -8px 0 24px rgba(0, 0, 0, 0.5);
          }
          .gap-detail .head {
            padding: 12px 14px;
            border-bottom: 1px solid var(--border);
            background: var(--bg-2);
          }
          .gap-detail .body {
            flex: 1;
            overflow-y: auto;
            padding: 14px;
            display: flex;
            flex-direction: column;
            gap: 16px;
          }
          .gap-detail .actions {
            padding: 10px 14px;
            border-top: 1px solid var(--border);
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
          }
          .whitespace { white-space: pre-wrap; line-height: 1.55; margin: 0; }
          .findings {
            display: flex;
            flex-direction: column;
            gap: 6px;
          }
        `}</style>
      </div>
    </Show>
  );
}

function Section(props: { title: string; children: unknown }) {
  return (
    <div class="section">
      <div
        class="dim"
        style={{
          "font-size": "var(--fs-xs)",
          "letter-spacing": "0.08em",
          "margin-bottom": "4px",
        }}
      >
        {props.title.toUpperCase()}
      </div>
      <div>{props.children as never}</div>
    </div>
  );
}

function FindingRow(props: { f: Finding }) {
  return (
    <div class="frow">
      <div class="row">
        <span class={`tag ${authorClass(props.f.author)}`}>{props.f.author}</span>
        <span class="dim mono" style={{ "font-size": "var(--fs-xs)" }}>
          tick {props.f.tick}
        </span>
        <span class="dim mono" style={{ "font-size": "var(--fs-xs)" }}>
          {props.f.kind}
        </span>
      </div>
      <div class="summary">{props.f.summary}</div>
      <style>{`
        .frow {
          background: var(--bg-2);
          border: 1px solid var(--border);
          padding: 6px 8px;
          border-radius: 3px;
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .frow .summary {
          font-size: var(--fs-sm);
          line-height: 1.5;
          white-space: pre-wrap;
          color: var(--fg-1);
          max-height: 160px;
          overflow-y: auto;
        }
      `}</style>
    </div>
  );
}

function Actions(props: { g: Gap }) {
  async function rewrite() {
    const intent = window.prompt("New intent:", props.g.intent);
    if (!intent || !intent.trim()) return;
    const criteria = window.prompt("New criteria:", props.g.criteria);
    if (!criteria || !criteria.trim()) return;
    try {
      await api.rewrite(props.g.id, intent, criteria);
    } catch (e) {
      window.alert(String(e));
    }
  }
  async function retire() {
    const reason = window.prompt("Retire reason:", "user requested");
    if (!reason) return;
    try {
      await api.retire(props.g.id, reason);
    } catch (e) {
      window.alert(String(e));
    }
  }
  async function reopen() {
    const reason = window.prompt("Reopen reason:", "user disagrees");
    if (!reason) return;
    try {
      await api.reopen(props.g.id, reason);
    } catch (e) {
      window.alert(String(e));
    }
  }
  async function unpause() {
    try {
      await api.unpauseGap(props.g.id);
    } catch (e) {
      window.alert(String(e));
    }
  }
  return (
    <>
      <Show when={props.g.paused}>
        <button class="primary" onClick={unpause} title="resume after 'not right now'">
          resume
        </button>
      </Show>
      <Show when={!props.g.preset_kind && props.g.status === "unfilled"}>
        <button onClick={rewrite}>rewrite…</button>
      </Show>
      <Show when={!props.g.preset_kind && props.g.status !== "retired"}>
        <button class="danger" onClick={retire}>
          retire…
        </button>
      </Show>
      <Show when={!props.g.preset_kind && props.g.status === "filled"}>
        <button onClick={reopen}>reopen…</button>
      </Show>
    </>
  );
}

function statusTag(s: Gap["status"]): string {
  return { unfilled: "cobalt", filled: "teal", retired: "graphite" }[s];
}
function authorClass(a: Finding["author"]): string {
  return {
    user: "cobalt",
    gap_finding: "cobalt",
    alignment: "amber",
    worker: "teal",
    system: "graphite",
  }[a];
}
