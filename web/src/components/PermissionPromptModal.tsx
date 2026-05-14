import { For, Show, createMemo, createSignal } from "solid-js";

import { api } from "../api";
import { refreshPermissionPrompts, store } from "../state";
import type { PermissionPrompt } from "../types";

/** Synchronous permission prompts. When a drone calls a tool that the
 *  active permission tier requires the operator to authorise, the dispatcher
 *  blocks until this modal answers. It floats above the canvas (and above
 *  the action banner) so an in-flight drone never goes unnoticed.
 *
 *  Operator UX:
 *   - One prompt at a time, oldest first.
 *   - Approve / Deny buttons. Deny carries an optional note back to the LLM
 *     so it can choose another route.
 *   - If multiple prompts pile up (slow operator, many drones), a small
 *     counter shows the queue depth. */
export function PermissionPromptModal() {
  const queue = createMemo<PermissionPrompt[]>(() => store.permission_prompts);
  const head = createMemo<PermissionPrompt | null>(() => queue()[0] ?? null);
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal<string | null>(null);
  const [note, setNote] = createSignal("");

  async function decide(outcome: "grant" | "deny") {
    const item = head();
    if (!item) return;
    setBusy(true);
    setError(null);
    try {
      const trimmed = note().trim();
      if (outcome === "grant") {
        await api.grantPermission(item.id, trimmed || null);
      } else {
        await api.denyPermission(item.id, trimmed || null);
      }
      setNote("");
      await refreshPermissionPrompts();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Show when={head()}>
      <div class="pp-shell" role="dialog" aria-modal="true">
        <div class="pp-card">
          <header class="pp-head">
            <div class="pp-tags">
              <span class="tag amber pulse">permission needed</span>
              <span class={`tag ${categoryTag(head()!.category)}`}>
                {head()!.category}
              </span>
              <span class="tag graphite">{head()!.tool_name}</span>
            </div>
            <Show when={queue().length > 1}>
              <span class="pp-queue">
                {queue().length - 1} more queued
              </span>
            </Show>
          </header>

          <div class="pp-body">
            <p class="pp-summary">{head()!.summary}</p>
            <p class="pp-meta dim">
              drone <span class="mono">{head()!.drone_id.slice(0, 12)}</span>{" "}
              on gap{" "}
              <span class="mono">{head()!.gap_id.slice(0, 12)}</span>{" "}
              · tier {head()!.tier}
            </p>
            <input
              class="pp-note"
              type="text"
              placeholder="optional note (passed back to the drone)"
              value={note()}
              disabled={busy()}
              onInput={(e) => setNote(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !busy()) void decide("grant");
                if (e.key === "Escape" && !busy()) void decide("deny");
              }}
            />
            <Show when={error()}>
              <p class="pp-error">{error()}</p>
            </Show>
          </div>

          <footer class="pp-actions">
            <button
              class="ghost"
              disabled={busy()}
              onClick={() => void decide("deny")}
              title="deny — drone gets a denial result and has to pick another way"
            >
              deny
            </button>
            <button
              class="primary"
              disabled={busy()}
              onClick={() => void decide("grant")}
              title="approve — drone runs the tool immediately"
            >
              approve
            </button>
          </footer>
        </div>
      </div>

      <style>{`
        .pp-shell {
          position: fixed;
          inset: 0;
          background: rgba(0, 0, 0, 0.55);
          z-index: 200;
          display: flex;
          align-items: flex-start;
          justify-content: center;
          padding: calc(var(--topbar-h) + 56px) 16px 16px;
          animation: pp-fade 180ms var(--ease);
        }
        @keyframes pp-fade {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        .pp-card {
          width: 100%;
          max-width: 640px;
          background: var(--bg-1);
          border: 1px solid var(--amber);
          border-radius: 6px;
          box-shadow:
            0 0 0 1px rgba(245, 181, 60, 0.10),
            0 12px 40px rgba(0, 0, 0, 0.55),
            0 0 60px rgba(245, 181, 60, 0.12);
          padding: 14px 18px;
          display: flex;
          flex-direction: column;
          gap: 12px;
          animation: pp-in 220ms var(--ease);
        }
        @keyframes pp-in {
          from { transform: translateY(-8px); opacity: 0; }
          to   { transform: translateY(0);    opacity: 1; }
        }
        .pp-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        .pp-tags {
          display: flex;
          align-items: center;
          gap: 6px;
          flex-wrap: wrap;
        }
        .pp-queue {
          font-size: var(--fs-xs);
          color: var(--fg-1);
          letter-spacing: 0.04em;
        }
        .pulse { animation: heartbeat 1.4s var(--ease) infinite; }
        .pp-body {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .pp-summary {
          margin: 0;
          font-size: var(--fs-md);
          line-height: 1.55;
          color: var(--fg-0);
          white-space: pre-wrap;
        }
        .pp-meta {
          margin: 0;
          font-size: var(--fs-xs);
          line-height: 1.5;
        }
        .pp-note {
          background: var(--bg-0);
          border: 1px solid var(--border);
          border-radius: 3px;
          padding: 6px 10px;
          color: var(--fg-0);
          font-size: var(--fs-sm);
          font-family: inherit;
        }
        .pp-note:focus {
          outline: none;
          border-color: var(--cobalt);
        }
        .pp-error {
          color: var(--copper);
          font-size: var(--fs-sm);
          margin: 0;
        }
        .pp-actions {
          display: flex;
          justify-content: flex-end;
          align-items: center;
          gap: 8px;
          padding-top: 4px;
          border-top: 1px solid var(--border);
        }
        .pp-actions .ghost {
          font-size: var(--fs-sm);
          padding: 5px 14px;
        }
        .pp-actions .primary {
          font-size: var(--fs-sm);
          padding: 5px 18px;
        }
      `}</style>
    </Show>
  );
}

function categoryTag(c: PermissionPrompt["category"]): string {
  return {
    local: "graphite",
    external: "copper",
    unknown: "amber",
  }[c] ?? "graphite";
}

// Keep For referenced for future iteration over queue.
void For;
