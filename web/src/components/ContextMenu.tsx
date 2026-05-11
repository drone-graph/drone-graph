import { Show, createSignal, onCleanup, onMount } from "solid-js";

import { api } from "../api";
import type { Gap } from "../types";

export function ContextMenu(props: {
  x: number;
  y: number;
  gap: Gap;
  onClose: () => void;
}) {
  const [reasonOpen, setReasonOpen] = createSignal<"retire" | "reopen" | null>(
    null,
  );
  const [reason, setReason] = createSignal("");

  onMount(() => {
    const onDocClick = () => props.onClose();
    setTimeout(() => document.addEventListener("click", onDocClick, { once: true }), 0);
    onCleanup(() => document.removeEventListener("click", onDocClick));
  });

  async function speakTo() {
    const text = window.prompt(
      `Speak to gap ${props.gap.id.slice(0, 8)}…`,
    );
    if (text && text.trim()) {
      await api.chat(text, props.gap.id);
    }
    props.onClose();
  }

  async function rewrite() {
    const intent = window.prompt("New intent:", props.gap.intent);
    if (!intent || !intent.trim()) {
      props.onClose();
      return;
    }
    const criteria = window.prompt("New criteria:", props.gap.criteria);
    if (!criteria || !criteria.trim()) {
      props.onClose();
      return;
    }
    try {
      await api.rewrite(props.gap.id, intent, criteria);
    } catch (e) {
      window.alert(String(e));
    }
    props.onClose();
  }

  async function submitReason() {
    const r = reason().trim() || "user_request";
    try {
      if (reasonOpen() === "retire") await api.retire(props.gap.id, r);
      else if (reasonOpen() === "reopen") await api.reopen(props.gap.id, r);
    } catch (e) {
      window.alert(String(e));
    }
    props.onClose();
  }

  const canRetire = props.gap.status !== "retired" && !props.gap.preset_kind;
  const canReopen = props.gap.status === "filled" && !props.gap.preset_kind;

  return (
    <div
      class="ctxmenu"
      style={{ left: `${props.x}px`, top: `${props.y}px` }}
      onClick={(e) => e.stopPropagation()}
    >
      <Show
        when={reasonOpen() === null}
        fallback={
          <div class="reason">
            <div class="dim" style={{ "font-size": "var(--fs-xs)" }}>
              REASON
            </div>
            <input
              autofocus
              value={reason()}
              onInput={(e) => setReason(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submitReason();
                if (e.key === "Escape") setReasonOpen(null);
              }}
            />
            <div class="row" style={{ "justify-content": "flex-end" }}>
              <button onClick={() => setReasonOpen(null)}>cancel</button>
              <button class="primary" onClick={submitReason}>
                confirm
              </button>
            </div>
          </div>
        }
      >
        <div class="header dim">
          {props.gap.preset_kind ? props.gap.preset_kind.toUpperCase() : props.gap.id.slice(0, 8)}
        </div>
        <button class="ghost" onClick={speakTo}>
          speak to this gap
        </button>
        <Show when={!props.gap.preset_kind && props.gap.status === "unfilled"}>
          <button class="ghost" onClick={rewrite}>
            rewrite intent…
          </button>
        </Show>
        <Show when={canRetire}>
          <button class="ghost danger" onClick={() => setReasonOpen("retire")}>
            retire subtree…
          </button>
        </Show>
        <Show when={canReopen}>
          <button class="ghost" onClick={() => setReasonOpen("reopen")}>
            reopen…
          </button>
        </Show>
      </Show>
      <style>{`
        .ctxmenu {
          position: fixed;
          z-index: 50;
          background: var(--bg-1);
          border: 1px solid var(--border-strong);
          border-radius: 4px;
          min-width: 200px;
          padding: 4px;
          display: flex;
          flex-direction: column;
          gap: 1px;
          box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
        }
        .ctxmenu .header {
          padding: 6px 10px 2px;
          font-size: var(--fs-xs);
          letter-spacing: 0.04em;
        }
        .ctxmenu button {
          text-align: left;
          padding: 6px 10px;
          border: none;
        }
        .ctxmenu button:hover { background: var(--bg-3); }
        .reason {
          padding: 10px 12px;
          display: flex;
          flex-direction: column;
          gap: 8px;
          min-width: 260px;
        }
      `}</style>
    </div>
  );
}
