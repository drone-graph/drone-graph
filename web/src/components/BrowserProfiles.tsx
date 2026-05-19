import { Show, createSignal } from "solid-js";

import { api } from "../api";
import type { LaunchResponse } from "../types";

/**
 * Simplified Browser Launcher — single button to launch a headed Chrome
 * session with all the bot-detection countermeasures that drones use
 * (channel="chrome", --disable-blink-features=AutomationControlled,
 * playwright-stealth).
 *
 * The operator can manually log into services, then close the window.
 * No profile management, no service tags, no registration.
 */
export function BrowserProfiles() {
  const [launching, setLaunching] = createSignal(false);
  const [feedback, setFeedback] = createSignal<{
    ok: boolean;
    msg: string;
  } | null>(null);

  function showFeedback(ok: boolean, msg: string) {
    setFeedback({ ok, msg });
    setTimeout(() => setFeedback(null), 8000);
  }

  async function launchBrowser() {
    setLaunching(true);
    setFeedback(null);
    try {
      // Use a stable session name so the operator always gets the same
      // persistent profile (cookies, local storage survive across launches).
      const r: LaunchResponse = await api.profileLaunch("manual-session");
      showFeedback(r.success, r.message);
    } catch (e: unknown) {
      showFeedback(false, e instanceof Error ? e.message : String(e));
    } finally {
      setLaunching(false);
    }
  }

  return (
    <div class="browser-launcher">
      <div class="launch-row">
        <button class="primary" onClick={launchBrowser} disabled={launching()}>
          {launching() ? "launching…" : "launch browser"}
        </button>
        <Show when={launching()}>
          <span class="running-indicator">running…</span>
        </Show>
      </div>

      <Show when={feedback()}>
        {(f) => (
          <p
            class="feedback"
            classList={{ ok: f().ok, err: !f().ok }}
          >
            {f().msg}
          </p>
        )}
      </Show>

      <style>{`
        .browser-launcher {
          display: flex;
          flex-direction: column;
          gap: 8px;
          margin: 4px 0;
        }
        .launch-row {
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .launch-row button {
          font-size: var(--fs-sm);
          padding: 6px 16px;
          cursor: pointer;
        }
        .running-indicator {
          font-size: var(--fs-sm);
          color: var(--fg-2);
          animation: pulse 1.5s ease-in-out infinite;
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        .feedback {
          font-size: var(--fs-sm);
          margin: 0;
          padding: 6px 8px;
          border-radius: 3px;
          max-width: 480px;
          line-height: 1.4;
        }
        .feedback.ok {
          background: rgba(60, 200, 120, 0.08);
          color: #3cc878;
        }
        .feedback.err {
          background: rgba(210, 80, 60, 0.08);
          color: var(--copper);
        }
      `}</style>
    </div>
  );
}
