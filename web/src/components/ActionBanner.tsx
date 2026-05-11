import { For, Show, createMemo, createSignal } from "solid-js";

import { api } from "../api";
import { refreshInbox, selectGap, setView, store } from "../state";
import type { InboxActionType, InboxItem } from "../types";

/** Centered, compact "action needed" panel that stays on screen until every
 *  block is resolved. Lives above the canvas in z-order so it never gets
 *  occluded by the WorkerFocus pane or the gap-detail overlay.
 *
 *  v2 redesign: less cramped, cleaner typography, action buttons on their
 *  own row so long summaries don't push them off-screen. */
export function ActionBanner() {
  const items = createMemo(() => store.inbox);
  const [focusIndex, setFocusIndex] = createSignal(0);
  const focus = createMemo<InboxItem | null>(() => {
    const list = items();
    if (list.length === 0) return null;
    const i = Math.min(focusIndex(), list.length - 1);
    return list[i];
  });
  const [busy, setBusy] = createSignal(false);
  const [showDetail, setShowDetail] = createSignal(false);

  async function resolve(outcome: "resolved" | "declined") {
    const item = focus();
    if (!item) return;
    const note = window.prompt(
      "Resolution note (what did you do?)",
      outcome === "resolved" ? "completed externally" : "declined",
    );
    if (note === null) return;
    setBusy(true);
    try {
      await api.resolveInbox(item.finding_id, { outcome, note });
      await refreshInbox();
      setFocusIndex(0);
      setShowDetail(false);
    } catch (e) {
      window.alert(String(e));
    } finally {
      setBusy(false);
    }
  }

  function inspectGap() {
    const item = focus();
    if (item && item.affected_gap_ids[0]) {
      selectGap(item.affected_gap_ids[0]);
      setView("console");
    }
  }
  function openSettings() {
    setView("settings");
  }

  return (
    <Show when={items().length > 0 && focus()}>
      <div class="ab-shell">
        <div class="ab-card">
          <header class="ab-head">
            <div class="ab-tags">
              <span class="tag amber pulse">action needed</span>
              <span class={`tag ${typeTag(focus()!.action_type)}`}>
                {focus()!.action_type}
              </span>
            </div>
            <Show when={items().length > 1}>
              <div class="ab-pager">
                <button
                  class="ghost"
                  onClick={() =>
                    setFocusIndex(
                      (i) => (i - 1 + items().length) % items().length,
                    )
                  }
                  title="previous"
                  aria-label="previous"
                >
                  ‹
                </button>
                <span class="ab-pager-counter">
                  {focusIndex() + 1} of {items().length}
                </span>
                <button
                  class="ghost"
                  onClick={() =>
                    setFocusIndex((i) => (i + 1) % items().length)
                  }
                  title="next"
                  aria-label="next"
                >
                  ›
                </button>
              </div>
            </Show>
          </header>

          <div class="ab-body">
            <p class="ab-summary">{focus()!.summary}</p>

            <Show when={extractURL(focus()!.details)}>
              <a
                class="ab-link"
                href={extractURL(focus()!.details)!}
                target="_blank"
                rel="noopener noreferrer"
              >
                {extractURL(focus()!.details)}
              </a>
            </Show>
            <Show when={extractAmount(focus()!.details)}>
              <div class="ab-amount">
                <span class="dim">proposed spend</span>{" "}
                <span class="copper">${extractAmount(focus()!.details)}</span>
              </div>
            </Show>
            <Show when={extractSecretName(focus()!.details)}>
              <div class="ab-hint">
                Drone is asking for credential{" "}
                <span class="mono">{extractSecretName(focus()!.details)}</span>.
                Paste it in Settings → API keys.
                <button class="ab-inline-link" onClick={openSettings}>
                  open settings →
                </button>
              </div>
            </Show>

            <Show when={showDetail()}>
              <pre class="ab-raw">
                {JSON.stringify(focus()!.details, null, 2)}
              </pre>
            </Show>
          </div>

          <footer class="ab-actions">
            <div class="ab-actions-left">
              <button
                class="ghost"
                onClick={() => setShowDetail((v) => !v)}
              >
                {showDetail() ? "hide details" : "details"}
              </button>
              <Show when={focus()!.affected_gap_ids.length > 0}>
                <button class="ghost" onClick={inspectGap}>
                  inspect gap
                </button>
              </Show>
            </div>
            <div class="ab-actions-right">
              <button
                class="ghost"
                disabled={busy()}
                onClick={() => void resolve("declined")}
              >
                decline
              </button>
              <button
                class="primary"
                disabled={busy()}
                onClick={() => void resolve("resolved")}
              >
                mark done
              </button>
            </div>
          </footer>
        </div>
      </div>

      <style>{`
        /* The shell positions the card; the card carries the visual. Two
           layers so we can constrain width without losing the full-bleed
           click-blocker behavior of a top-of-screen alert. */
        .ab-shell {
          position: absolute;
          top: calc(var(--topbar-h) + 8px);
          left: 0;
          right: 0;
          z-index: 20;
          display: flex;
          justify-content: center;
          padding: 0 16px;
          pointer-events: none;  /* let the canvas behind be clickable */
        }
        .ab-card {
          pointer-events: auto;
          width: 100%;
          max-width: 720px;
          background: var(--bg-1);
          border: 1px solid var(--amber);
          border-radius: 6px;
          box-shadow:
            0 0 0 1px rgba(245, 181, 60, 0.10),
            0 12px 40px rgba(0, 0, 0, 0.55),
            0 0 60px rgba(245, 181, 60, 0.12);
          padding: 12px 16px;
          display: flex;
          flex-direction: column;
          gap: 10px;
          animation: ab-in 320ms var(--ease);
        }
        @keyframes ab-in {
          from { transform: translateY(-8px); opacity: 0; }
          to   { transform: translateY(0);    opacity: 1; }
        }

        .ab-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        .ab-tags {
          display: flex;
          align-items: center;
          gap: 6px;
        }
        .pulse {
          animation: heartbeat 1.4s var(--ease) infinite;
        }
        .ab-pager {
          display: flex;
          align-items: center;
          gap: 4px;
        }
        .ab-pager button {
          padding: 1px 9px;
          font-size: var(--fs-md);
          line-height: 1;
        }
        .ab-pager-counter {
          font-size: var(--fs-xs);
          color: var(--fg-1);
          min-width: 60px;
          text-align: center;
        }

        .ab-body {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .ab-summary {
          margin: 0;
          font-size: var(--fs-md);
          line-height: 1.55;
          color: var(--fg-0);
          white-space: pre-wrap;
          max-height: 9.5em;     /* ~6 lines, scroll past if longer */
          overflow-y: auto;
        }
        .ab-link {
          color: var(--cobalt-soft);
          font-family: var(--font-mono);
          font-size: var(--fs-sm);
          word-break: break-all;
          text-decoration: underline;
        }
        .ab-amount {
          font-size: var(--fs-sm);
        }
        .copper { color: var(--copper); }
        .ab-hint {
          font-size: var(--fs-sm);
          color: var(--fg-1);
          background: var(--bg-2);
          border: 1px solid var(--border);
          padding: 6px 10px;
          border-radius: 3px;
          line-height: 1.5;
        }
        .ab-inline-link {
          background: transparent;
          border: none;
          color: var(--cobalt-soft);
          font: inherit;
          cursor: pointer;
          padding: 0 4px;
          text-decoration: underline dotted;
        }
        .ab-raw {
          background: var(--bg-0);
          border: 1px solid var(--border);
          padding: 6px 10px;
          font-size: 10.5px;
          line-height: 1.4;
          margin: 0;
          max-height: 180px;
          overflow: auto;
        }

        .ab-actions {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 8px;
          padding-top: 4px;
          border-top: 1px solid var(--border);
        }
        .ab-actions-left, .ab-actions-right {
          display: flex;
          gap: 6px;
        }
        .ab-actions .ghost {
          font-size: var(--fs-xs);
          padding: 4px 10px;
        }
        .ab-actions .primary {
          font-size: var(--fs-sm);
          padding: 5px 16px;
        }
      `}</style>
    </Show>
  );
}

function typeTag(t: InboxActionType): string {
  return {
    credential: "cobalt",
    oauth: "cobalt",
    sign_in: "cobalt",
    purchase: "copper",
    approval: "amber",
    mfa: "amber",
    other: "graphite",
  }[t];
}

function extractURL(details: Record<string, unknown>): string | null {
  const url = details["url"];
  return typeof url === "string" ? url : null;
}

function extractAmount(details: Record<string, unknown>): string | null {
  const a = details["amount_usd"] ?? details["amount"];
  return typeof a === "number" ? a.toFixed(2) : null;
}

function extractSecretName(details: Record<string, unknown>): string | null {
  const s = details["secret_name"];
  return typeof s === "string" ? s : null;
}

void For;
