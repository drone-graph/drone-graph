import { createSignal } from "solid-js";

import { api } from "../api";
import { playSound, unlockAudio } from "../sound";

export function EmptyState() {
  const [text, setText] = createSignal("");
  const [sending, setSending] = createSignal(false);

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
    } finally {
      setSending(false);
    }
  }

  return (
    <div class="empty">
      <div class="dot heartbeat" />
      <h1>What should the hivemind work on?</h1>
      <form onSubmit={submit} class="composer">
        <textarea
          autofocus
          placeholder="Type a seed prompt. The swarm will frame it and decompose."
          value={text()}
          onInput={(e) => setText(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              void submit(e);
            }
          }}
          rows={4}
        />
        <div class="row" style={{ "justify-content": "space-between" }}>
          <span class="faint">⌘/Ctrl + Enter to send</span>
          <button class="primary" disabled={sending() || !text().trim()}>
            seed the swarm
          </button>
        </div>
      </form>
      <p class="faint hint">
        Your prompt lands as a <span class="mono">user_input</span> finding on{" "}
        <span class="mono">preset:gap_finding</span>. Next tick, Gap Finding
        reads it and decides how to frame the work. There is no end state —
        the swarm will run continuously until you pause it.
      </p>
      <style>{`
        .empty {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 18px;
          height: 100%;
          padding: 8% 24px;
        }
        .empty .dot {
          width: 18px;
          height: 18px;
          border-radius: 50%;
          background: radial-gradient(
            circle at 30% 30%,
            var(--cobalt-soft),
            var(--cobalt) 50%,
            var(--cobalt-dim) 100%
          );
          box-shadow: 0 0 30px var(--cobalt), 0 0 80px var(--cobalt-dim);
          margin-bottom: 8px;
        }
        .empty h1 {
          margin: 0;
          font-weight: 400;
          font-size: var(--fs-xl);
          letter-spacing: 0.02em;
          color: var(--fg-0);
        }
        .composer {
          width: min(620px, 86%);
          display: flex;
          flex-direction: column;
          gap: 10px;
          margin-top: 12px;
        }
        .hint {
          max-width: 560px;
          text-align: center;
          line-height: 1.6;
          font-size: var(--fs-sm);
          margin-top: 6px;
        }
      `}</style>
    </div>
  );
}
