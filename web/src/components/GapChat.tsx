// Gap-level chat thread.
//
// Chat lives on the gap, not on the active drone. The operator can
// post a question or directive any time — if there's a live drone on
// the gap, it sees the message at its next turn boundary (or wakes
// from cm_browser.await_operator within ~1.5s). If no drone is
// dispatched, the message stays in the substrate and the next drone
// against this gap reads it during context preload.
//
// History is rendered from chat_with_drone findings on the gap.

import { For, Show, createResource, createSignal, onCleanup, onMount } from "solid-js";

import { api } from "../api";

export function GapChat(props: { gapId: string; compact?: boolean }) {
  const [draft, setDraft] = createSignal("");
  const [sending, setSending] = createSignal(false);
  const [messages, { refetch }] = createResource(
    () => props.gapId,
    async (gid: string) => {
      try {
        const r = await api.chatGapHistory(gid, 100);
        return r.messages;
      } catch {
        return [];
      }
    },
  );

  // Poll every 4s so the operator's view stays fresh even when the
  // SSE stream hasn't delivered an event yet for this gap. Cheap.
  let timer: ReturnType<typeof setInterval> | undefined;
  onMount(() => {
    timer = setInterval(() => void refetch(), 4000);
  });
  onCleanup(() => {
    if (timer) clearInterval(timer);
  });

  async function send() {
    const t = draft().trim();
    if (!t || sending()) return;
    setSending(true);
    setDraft("");
    try {
      await api.chatGap(props.gapId, t);
      await refetch();
    } catch (e) {
      window.alert(String(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <div class="gap-chat" classList={{ compact: !!props.compact }}>
      <div class="thread">
        <Show
          when={(messages() ?? []).length > 0}
          fallback={
            <div class="faint empty">
              No messages yet. Ask the swarm a question, give context,
              or steer the gap.
            </div>
          }
        >
          <For each={messages()}>
            {(m) => (
              <div class="msg" classList={{ user: m.author === "user" }}>
                <div class="who">
                  {m.author === "user" ? "you" : m.author}
                </div>
                <div class="body">{m.text}</div>
              </div>
            )}
          </For>
        </Show>
      </div>
      <form
        class="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          type="text"
          placeholder="message this gap…"
          value={draft()}
          disabled={sending()}
          onInput={(e) => setDraft(e.currentTarget.value)}
        />
        <button type="submit" disabled={sending() || !draft().trim()}>
          send
        </button>
      </form>
      <style>{`
        .gap-chat {
          display: flex;
          flex-direction: column;
          gap: 8px;
          min-height: 220px;
        }
        .gap-chat.compact { min-height: 160px; }
        .gap-chat .thread {
          flex: 1;
          min-height: 120px;
          max-height: 320px;
          overflow-y: auto;
          padding: 8px;
          background: var(--bg-0);
          border: 1px solid var(--border);
          border-radius: 3px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .gap-chat .empty {
          padding: 8px;
          font-size: var(--fs-sm);
        }
        .gap-chat .msg {
          display: flex;
          flex-direction: column;
          gap: 2px;
          font-size: var(--fs-sm);
        }
        .gap-chat .msg .who {
          font-size: 10px;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: var(--fg-2);
        }
        .gap-chat .msg.user .who { color: var(--cobalt-soft); }
        .gap-chat .msg .body {
          white-space: pre-wrap;
          line-height: 1.45;
          color: var(--fg-0);
        }
        .gap-chat .composer {
          display: flex;
          gap: 6px;
        }
        .gap-chat .composer input {
          flex: 1;
          background: var(--bg-0);
          border: 1px solid var(--border);
          color: var(--fg-0);
          padding: 6px 8px;
          font-size: var(--fs-sm);
          font-family: inherit;
        }
        .gap-chat .composer button {
          padding: 6px 12px;
          font-size: var(--fs-sm);
        }
      `}</style>
    </div>
  );
}
