import { For, Show, createMemo, createSignal, onMount } from "solid-js";

import { api } from "../api";
import { playSound, unlockAudio } from "../sound";
import { inboxResolutions, selectGap, store } from "../state";

export function ChatRail() {
  const [text, setText] = createSignal("");
  const [sending, setSending] = createSignal(false);
  let scrollRef: HTMLDivElement | undefined;

  async function submit(e?: SubmitEvent | KeyboardEvent) {
    if (e) e.preventDefault();
    const v = text().trim();
    if (!v || sending()) return;
    unlockAudio();
    setSending(true);
    try {
      await api.chat(v);
      playSound("prompt");
      setText("");
      // Auto-scroll on send.
      setTimeout(() => scrollRef?.scrollTo(0, scrollRef.scrollHeight), 40);
    } finally {
      setSending(false);
    }
  }

  onMount(() => {
    setTimeout(() => scrollRef?.scrollTo(0, scrollRef.scrollHeight), 50);
  });

  const resolutions = createMemo(() => inboxResolutions());
  // Track which originally-resolved blocks the operator has manually
  // re-expanded by clicking the collapsed row. Keyed by finding_id.
  const [expandedResolved, setExpandedResolved] = createSignal<Set<string>>(
    new Set(),
  );
  function toggleExpand(id: string) {
    setExpandedResolved((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <aside class="chat-rail">
      <div class="chat-head">
        <span class="dim" style={{ "font-size": "var(--fs-xs)", "letter-spacing": "0.08em" }}>
          GLOBAL CHAT · THE HIVEMIND
        </span>
      </div>
      <div class="chat-body" ref={scrollRef}>
        <For each={store.chat}>
          {(m) => {
            const meta = workerLabel(m);
            // Was this action-needed message resolved? If so, render
            // collapsed by default with the operator's decline/resolve
            // reason inline, click-to-expand for the original full text.
            const resolution =
              meta.kind === "action_needed" && m.finding_id
                ? resolutions().get(m.finding_id)
                : undefined;
            const isCollapsed =
              resolution !== undefined &&
              !expandedResolved().has(m.finding_id ?? "");
            if (isCollapsed) {
              return (
                <div
                  class="msg worker collapsed"
                  classList={{
                    "outcome-declined": resolution!.outcome === "declined",
                    "outcome-resolved": resolution!.outcome === "resolved",
                  }}
                  onClick={() => toggleExpand(m.finding_id!)}
                  role="button"
                  tabindex={0}
                  title="click to re-expand"
                >
                  <span class={`tag ${tagForOutcome(resolution!.outcome)}`}>
                    {resolution!.outcome}
                  </span>
                  <span class="collapsed-note">
                    <Show when={resolution!.note} fallback={
                      <span class="dim">(no reason given)</span>
                    }>
                      {resolution!.note}
                    </Show>
                  </span>
                  <span class="faint chev">›</span>
                </div>
              );
            }
            return (
              <div
                class={`msg ${m.author}`}
                classList={{
                  "kind-action": meta.kind === "action_needed",
                  "kind-narrate": meta.kind === "narrate",
                  "kind-realworld": meta.kind === "realworld_action",
                  "outcome-fail": meta.outcomeBad,
                }}
              >
                <div class="meta">
                  <span class={`tag ${meta.tagClass}`}>{meta.label}</span>
                  <Show when={m.affected_gap_ids.length > 0}>
                    <a
                      class="gap-link faint"
                      onClick={() => selectGap(m.affected_gap_ids[0] ?? null)}
                    >
                      → {m.affected_gap_ids[0]!.slice(0, 8)}
                    </a>
                  </Show>
                  <Show when={resolution}>
                    <a
                      class="gap-link faint"
                      onClick={() => toggleExpand(m.finding_id!)}
                    >
                      ↑ collapse ({resolution!.outcome})
                    </a>
                  </Show>
                </div>
                <div class="text">{m.text}</div>
              </div>
            );
          }}
        </For>
        <Show when={store.chat.length === 0}>
          <div class="empty faint">
            no messages yet. type below to send a signal into the swarm.
          </div>
        </Show>
      </div>
      <form onSubmit={submit} class="composer">
        <textarea
          placeholder="speak to the hivemind…"
          value={text()}
          onInput={(e) => setText(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              void submit(e);
            }
          }}
          rows={3}
        />
        <div class="row" style={{ "justify-content": "space-between" }}>
          <span class="faint" style={{ "font-size": "var(--fs-xs)" }}>
            ⌘ enter
          </span>
          <button class="primary" disabled={sending() || !text().trim()}>
            send
          </button>
        </div>
      </form>
      <style>{`
        .chat-rail {
          display: flex;
          flex-direction: column;
          width: var(--rail-w-left);
          background: var(--bg-1);
          border-right: 1px solid var(--border);
          height: 100%;
          min-height: 0;
        }
        .chat-head {
          padding: 12px 14px 8px;
          border-bottom: 1px solid var(--border);
          flex-shrink: 0;
        }
        .chat-body {
          flex: 1 1 0;
          min-height: 0;
          overflow-y: auto;
          padding: 10px 14px;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .chat-body .empty {
          margin-top: 40px;
          text-align: center;
          font-size: var(--fs-sm);
          line-height: 1.6;
        }
        .msg .meta {
          display: flex;
          gap: 8px;
          align-items: center;
          margin-bottom: 3px;
        }
        .msg .text {
          white-space: pre-wrap;
          line-height: 1.55;
          font-size: var(--fs-sm);
          color: var(--fg-0);
        }
        .msg.user .text {
          color: var(--fg-0);
        }
        .msg.alignment .text { color: var(--amber); }
        .msg.gap_finding .text { color: var(--c-gap-finding); }
        .msg.system .text { color: var(--fg-1); }
        /* Calm by default (a normal drone narration is just a status
         * update). Only escalate to amber/copper for action-needed,
         * realworld, and failure cases — these are the ones the operator
         * should actually attend to. */
        .msg.worker .text { color: var(--fg-0); }
        .msg.kind-action,
        .msg.kind-realworld {
          border-left: 2px solid var(--amber);
          padding-left: 8px;
        }
        .msg.kind-narrate {
          border-left: 2px solid var(--teal-dim);
          padding-left: 8px;
        }
        .msg.outcome-fail {
          border-left-color: var(--copper);
        }

        /* Collapsed resolved-block row — one-liner with the operator's
         * decision and reason, click to re-expand the full original
         * action-needed text. */
        .msg.collapsed {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 5px 8px;
          border-left: 2px solid var(--border);
          background: var(--bg-2);
          font-size: var(--fs-xs);
          color: var(--fg-1);
          cursor: pointer;
          opacity: 0.78;
          transition: opacity 100ms var(--ease);
        }
        .msg.collapsed:hover {
          opacity: 1;
        }
        .msg.collapsed.outcome-declined {
          border-left-color: var(--border-strong);
        }
        .msg.collapsed.outcome-resolved {
          border-left-color: var(--teal-dim);
        }
        .msg.collapsed .collapsed-note {
          flex: 1;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .msg.collapsed .chev {
          font-size: var(--fs-md);
          line-height: 1;
        }
        .gap-link {
          cursor: pointer;
          text-decoration: underline dotted;
          font-size: var(--fs-xs);
        }
        .composer {
          padding: 10px 14px;
          border-top: 1px solid var(--border);
          display: flex;
          flex-direction: column;
          gap: 6px;
          flex-shrink: 0;
        }
      `}</style>
    </aside>
  );
}

function authorTagClass(a: string): string {
  return {
    user: "cobalt",
    gap_finding: "cobalt",
    alignment: "amber",
    worker: "amber",
    system: "graphite",
  }[a] ?? "graphite";
}

function tagForOutcome(outcome: string): string {
  return {
    declined: "graphite",
    resolved: "teal",
    skipped: "graphite",
  }[outcome] ?? "graphite";
}

interface WorkerMeta {
  label: string;
  tagClass: string;
  kind?: "action_needed" | "narrate" | "realworld_action";
  outcomeBad: boolean;
}

/** Distinguish the three worker-author chat varieties. Without this, a
 *  benign "I built and sandbox-tested X" narration appears with the same
 *  label as a real action-blocked request — which is what the operator
 *  flagged. */
function workerLabel(m: {
  author: string;
  kind?: "action_needed" | "narrate" | "realworld_action";
  outcome?: string;
}): WorkerMeta {
  if (m.author !== "worker") {
    return { label: m.author, tagClass: authorTagClass(m.author), outcomeBad: false };
  }
  if (m.kind === "action_needed") {
    return { label: "drone · action needed", tagClass: "amber", kind: m.kind, outcomeBad: false };
  }
  if (m.kind === "realworld_action") {
    return { label: "drone · external action", tagClass: "amber", kind: m.kind, outcomeBad: false };
  }
  // narrate (default)
  const outcome = m.outcome ?? "";
  if (outcome === "fail" || outcome === "error") {
    return { label: "drone · failed", tagClass: "copper", kind: m.kind, outcomeBad: true };
  }
  if (outcome === "cancelled") {
    return { label: "drone · cancelled", tagClass: "graphite", kind: m.kind, outcomeBad: true };
  }
  if (outcome === "budget_exceeded") {
    return { label: "drone · over budget", tagClass: "copper", kind: m.kind, outcomeBad: true };
  }
  if (outcome === "requires_user_action" || outcome === "fail_blocked") {
    return { label: "drone · action needed", tagClass: "amber", kind: m.kind, outcomeBad: false };
  }
  // Default: success / fill — a status update, not an alert.
  return { label: "drone", tagClass: "teal", kind: m.kind, outcomeBad: false };
}
