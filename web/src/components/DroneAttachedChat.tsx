// Live drone-attached chat panel.
//
// Shown for any active drone that has an open Chromium window (cm_browser
// state present for its gap). Renders the latest screenshot, basic page
// metadata (url, title, last action), the operator ↔ drone chat thread,
// and a composer. When the drone is in ``await_operator`` the panel
// highlights itself amber and the operator's first reply unblocks the
// drone via the ``chat_with_drone`` finding mechanism.
//
// The panel does NOT replace WorkerFocus's terminal pane — it sits next
// to it so the operator can watch both the bash output and the browser
// state of the drone at the same time.

import { Show, createMemo, createSignal, onCleanup, onMount } from "solid-js";

import { refreshBrowserState, sendDroneChat, store } from "../state";

export function DroneAttachedChat(props: { gapId: string }) {
  const snap = createMemo(() => store.browser_state[props.gapId] ?? null);
  const thread = createMemo(() => store.drone_chat[props.gapId] ?? []);
  const [draft, setDraft] = createSignal("");
  const [sending, setSending] = createSignal(false);

  // Keep the screenshot fresh while the panel is mounted. The SSE event
  // ``browser.state`` also drives a refresh, but a 4s poll catches cases
  // where the event ring rolled over before this panel was open.
  let pollTimer: ReturnType<typeof setInterval> | undefined;
  onMount(() => {
    void refreshBrowserState(props.gapId);
    pollTimer = setInterval(() => {
      void refreshBrowserState(props.gapId);
    }, 4000);
  });
  onCleanup(() => {
    if (pollTimer) clearInterval(pollTimer);
  });

  async function send() {
    const t = draft().trim();
    if (!t || sending()) return;
    setSending(true);
    setDraft("");
    try {
      await sendDroneChat(props.gapId, t);
    } finally {
      setSending(false);
    }
  }

  return (
    <Show when={snap()}>
      <div
        class="drone-chat"
        classList={{ awaiting: !!snap()?.awaiting_prompt }}
      >
        <div class="head">
          <div class="col" style={{ gap: "2px", "min-width": 0 }}>
            <div class="row" style={{ gap: "6px" }}>
              <span class="tag teal">browser</span>
              <Show when={snap()?.profile}>
                <span class="dim mono" style={{ "font-size": "var(--fs-xs)" }}>
                  {snap()!.profile}
                </span>
              </Show>
              <Show when={snap()?.action}>
                <span class="faint" style={{ "font-size": "var(--fs-xs)" }}>
                  · {snap()!.action}
                </span>
              </Show>
            </div>
            <div class="title" title={snap()?.title ?? ""}>
              {snap()?.title ? oneLine(snap()!.title!) : "(no title)"}
            </div>
            <div class="url faint" title={snap()?.url ?? ""}>
              {snap()?.url ?? ""}
            </div>
          </div>
        </div>

        <div class="screenshot">
          <Show
            when={snap()?.screenshot_b64}
            fallback={
              <div class="faint" style={{ padding: "20px" }}>
                waiting for first screenshot…
              </div>
            }
          >
            <img
              src={`data:image/png;base64,${snap()!.screenshot_b64}`}
              alt={snap()?.title ?? "drone browser view"}
            />
          </Show>
        </div>

        <Show when={snap()?.awaiting_prompt}>
          <div class="ask">
            <div class="ask-tag">DRONE NEEDS YOU</div>
            <div class="ask-text">{snap()!.awaiting_prompt}</div>
          </div>
        </Show>

        <div class="thread">
          <Show
            when={thread().length > 0}
            fallback={
              <div class="faint" style={{ padding: "8px 12px" }}>
                no messages yet. Type below to talk to this drone.
              </div>
            }
          >
            {thread().map((m) => (
              <div class="msg" classList={{ user: m.author === "user" }}>
                <div class="who">{m.author === "user" ? "you" : "drone"}</div>
                <div class="body">{m.text}</div>
              </div>
            ))}
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
            placeholder={
              snap()?.awaiting_prompt
                ? "your reply unblocks the drone…"
                : "message this drone…"
            }
            value={draft()}
            disabled={sending()}
            onInput={(e) => setDraft(e.currentTarget.value)}
          />
          <button type="submit" disabled={sending() || !draft().trim()}>
            send
          </button>
        </form>

        <style>{`
          .drone-chat {
            position: absolute;
            top: 0;
            right: 0;
            width: 380px;
            max-width: 50%;
            height: 100%;
            background: var(--bg-1);
            border-left: 1px solid var(--cobalt-dim);
            display: flex;
            flex-direction: column;
            z-index: 4;
            box-shadow: -8px 0 24px rgba(0, 0, 0, 0.4);
          }
          .drone-chat.awaiting {
            border-left-color: var(--copper);
            animation: chatPulse 1.4s ease-in-out infinite;
          }
          @keyframes chatPulse {
            0%, 100% { box-shadow: -8px 0 24px rgba(0,0,0,0.4); }
            50%      { box-shadow: -8px 0 32px rgba(200,128,40,0.5); }
          }
          .drone-chat .head {
            padding: 10px 12px;
            border-bottom: 1px solid var(--border);
            flex-shrink: 0;
          }
          .drone-chat .title {
            font-size: var(--fs-sm);
            color: var(--fg-0);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .drone-chat .url {
            font-size: var(--fs-xs);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .drone-chat .screenshot {
            flex: 0 0 auto;
            max-height: 40%;
            overflow: hidden;
            background: var(--bg-0);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: center;
          }
          .drone-chat .screenshot img {
            width: 100%;
            height: auto;
            display: block;
            object-fit: contain;
          }
          .drone-chat .ask {
            padding: 10px 12px;
            background: rgba(200, 128, 40, 0.12);
            border-bottom: 1px solid var(--copper-dim, var(--copper));
          }
          .drone-chat .ask-tag {
            font-size: 10px;
            letter-spacing: 0.12em;
            color: var(--copper);
            margin-bottom: 4px;
          }
          .drone-chat .ask-text {
            font-size: var(--fs-sm);
            color: var(--fg-0);
            line-height: 1.4;
            white-space: pre-wrap;
          }
          .drone-chat .thread {
            flex: 1 1 auto;
            min-height: 0;
            overflow-y: auto;
            padding: 8px 12px;
            display: flex;
            flex-direction: column;
            gap: 6px;
          }
          .drone-chat .msg {
            display: flex;
            flex-direction: column;
            gap: 2px;
            font-size: var(--fs-sm);
          }
          .drone-chat .msg .who {
            font-size: 10px;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: var(--fg-2);
          }
          .drone-chat .msg.user .who {
            color: var(--cobalt-soft);
          }
          .drone-chat .msg .body {
            line-height: 1.4;
            white-space: pre-wrap;
            color: var(--fg-0);
          }
          .drone-chat .composer {
            display: flex;
            gap: 6px;
            padding: 8px 10px;
            border-top: 1px solid var(--border);
            flex-shrink: 0;
            background: var(--bg-2);
          }
          .drone-chat .composer input {
            flex: 1;
            background: var(--bg-0);
            border: 1px solid var(--border);
            color: var(--fg-0);
            padding: 6px 8px;
            font-size: var(--fs-sm);
            font-family: inherit;
          }
          .drone-chat .composer button {
            padding: 6px 12px;
            font-size: var(--fs-sm);
          }
        `}</style>
      </div>
    </Show>
  );
}

function oneLine(s: string): string {
  const i = s.indexOf("\n");
  return (i === -1 ? s : s.slice(0, i)).trim();
}
