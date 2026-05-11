import { Show, createSignal } from "solid-js";

import { api } from "../api";
import { playSound, unlockAudio } from "../sound";

/** Step 2 of onboarding. Keys are set; substrate is still dormant. Single
 *  centered prompt on a black field. On submit, the OnboardingSeed dissolves
 *  and the dashboard fades in around the cobalt dot — the dot is shared with
 *  the canvas so the transition reads as a single shape continuing forward.
 */
export function OnboardingSeed() {
  const [text, setText] = createSignal("");
  const [sending, setSending] = createSignal(false);
  const [error, setError] = createSignal<string | null>(null);
  const [dissolving, setDissolving] = createSignal(false);

  async function submit(e?: SubmitEvent | KeyboardEvent) {
    if (e) e.preventDefault();
    const v = text().trim();
    if (!v || sending()) return;
    unlockAudio();
    setSending(true);
    setError(null);
    try {
      await api.chat(v);
      playSound("prompt");
      // Hold the input briefly so the user sees the swarm wake up before the
      // dashboard takes over — a perceived continuity beat.
      setDissolving(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setSending(false);
    }
  }

  return (
    <div class="onboard-seed" classList={{ dissolving: dissolving() }}>
      <div class="frame">
        <div class="dot heartbeat" />
        <h1>What should the hivemind work on?</h1>
        <p class="dim sub-line">
          The swarm reads this as the seed signal, frames it into a gap, and
          decomposes from there. There is no end state — the substrate keeps
          running until you pause it.
        </p>
        <form onSubmit={submit} class="composer">
          <textarea
            autofocus
            placeholder="Type a seed prompt. Anything from a single sentence to a paragraph of context."
            value={text()}
            onInput={(e) => setText(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                void submit(e);
              }
            }}
            rows={5}
          />
          <div class="row" style={{ "justify-content": "space-between" }}>
            <span class="faint">⌘ / Ctrl + Enter to seed the swarm</span>
            <button class="primary" disabled={sending() || !text().trim()}>
              {sending() ? "seeding…" : "seed the swarm"}
            </button>
          </div>
        </form>
        <Show when={error()}>
          <p class="error">{error()}</p>
        </Show>
      </div>
      <style>{`
        .onboard-seed {
          position: fixed;
          inset: 0;
          background: var(--bg-0);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 100;
          padding: 24px;
          animation: fadeIn 600ms var(--ease);
          transition: opacity 1400ms var(--ease), filter 1400ms var(--ease);
        }
        .onboard-seed.dissolving {
          opacity: 0;
          filter: blur(6px);
          pointer-events: none;
        }
        .onboard-seed .frame {
          width: min(620px, 100%);
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 16px;
          text-align: center;
        }
        .onboard-seed .dot {
          width: 22px;
          height: 22px;
          border-radius: 50%;
          background: radial-gradient(
            circle at 30% 30%,
            var(--cobalt-soft),
            var(--cobalt) 50%,
            var(--cobalt-dim) 100%
          );
          box-shadow: 0 0 36px var(--cobalt), 0 0 100px var(--cobalt-dim);
          margin-bottom: 10px;
        }
        .onboard-seed h1 {
          margin: 0;
          font-weight: 400;
          font-size: var(--fs-xl);
          letter-spacing: 0.02em;
          color: var(--fg-0);
        }
        .onboard-seed .sub-line {
          font-size: var(--fs-sm);
          line-height: 1.6;
          max-width: 520px;
          margin: 0;
        }
        .onboard-seed .composer {
          width: 100%;
          display: flex;
          flex-direction: column;
          gap: 10px;
          margin-top: 14px;
        }
        .onboard-seed .composer textarea {
          font-family: var(--font-mono);
          font-size: var(--fs-md);
          line-height: 1.55;
        }
        .onboard-seed .error {
          color: var(--copper);
          font-size: var(--fs-sm);
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </div>
  );
}
