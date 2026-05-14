import { Show, createSignal } from "solid-js";

import { api } from "../api";
import { refreshSettings } from "../state";
import type { SettingsView } from "../types";

type Tier = SettingsView["permission_tier"];

/** Step 3 of onboarding (after key + budget, before seed). Drones always
 *  run with full access to the operator's machine and accounts; this step
 *  picks how loudly they ask before acting on that access.
 *
 *  - open: never prompts. Maximum throughput, maximum trust.
 *  - ask_external: prompts before actions with external effects
 *    (sending mail, posting, deploying, charging money, etc.).
 *  - ask_everything: prompts before every tool call that touches the
 *    machine or the web. Maximum oversight.
 */
export function OnboardingPermissions() {
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal<string | null>(null);

  async function choose(tier: Tier) {
    setSaving(true);
    setError(null);
    try {
      await api.updateSettings({
        permission_tier: tier,
        permission_tier_acknowledged: true,
      });
      await refreshSettings();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div class="onboard-perm">
      <div class="frame">
        <div class="header">
          <span class="dot heartbeat" />
          <span class="title">DRONE GRAPH</span>
          <span class="dim sub">· MISSION CONTROL · step 3 of 3</span>
        </div>

        <h1>How loudly should the swarm ask?</h1>
        <p class="dim sub-line">
          Drones run as you. They share your machine, your accounts, your
          credentials. Pick how often they should pause for your okay
          before acting on that access. You can change this any time in
          Settings.
        </p>

        <div class="options">
          <button
            type="button"
            class="option"
            disabled={saving()}
            onClick={() => void choose("open")}
          >
            <div class="o-head">
              <span class="o-title">Open</span>
            </div>
            <p class="o-body">
              Drones act freely. No prompts, no friction. Best when you
              know what you've asked for and want it done.
            </p>
          </button>

          <button
            type="button"
            class="option recommended"
            disabled={saving()}
            onClick={() => void choose("ask_external")}
          >
            <div class="o-head">
              <span class="o-tag">RECOMMENDED</span>
              <span class="o-title">Ask before external actions</span>
            </div>
            <p class="o-body">
              Local work runs freely. Anything with reach beyond this
              machine — sending mail, posting, deploying, spending
              money — pauses for your okay first.
            </p>
          </button>

          <button
            type="button"
            class="option"
            disabled={saving()}
            onClick={() => void choose("ask_everything")}
          >
            <div class="o-head">
              <span class="o-title">Ask before everything</span>
            </div>
            <p class="o-body">
              Every tool call that touches the machine or the web pauses
              for explicit approval. Maximum oversight; slow but
              predictable.
            </p>
          </button>
        </div>

        <Show when={error()}>
          <p class="error">{error()}</p>
        </Show>
      </div>
      <style>{`
        .onboard-perm {
          position: fixed;
          inset: 0;
          background: var(--bg-0);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 100;
          padding: 24px;
          animation: fadeIn 600ms var(--ease);
        }
        .onboard-perm .frame {
          width: min(600px, 100%);
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .onboard-perm .header {
          display: flex;
          align-items: center;
          gap: 10px;
          letter-spacing: 0.16em;
          font-size: var(--fs-sm);
        }
        .onboard-perm .header .dot {
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: var(--cobalt);
          box-shadow: 0 0 12px var(--cobalt);
        }
        .onboard-perm .title { font-weight: 500; }
        .onboard-perm .sub { letter-spacing: 0.16em; font-size: var(--fs-xs); }
        .onboard-perm h1 {
          margin: 18px 0 0;
          font-weight: 400;
          font-size: var(--fs-xl);
          letter-spacing: 0.01em;
          line-height: 1.35;
        }
        .onboard-perm .sub-line {
          font-size: var(--fs-sm);
          line-height: 1.55;
          margin: 4px 0 0;
        }
        .onboard-perm .options {
          display: flex;
          flex-direction: column;
          gap: 10px;
          margin-top: 16px;
        }
        .onboard-perm .option {
          text-align: left;
          padding: 14px 16px;
          background: var(--bg-1);
          border: 1px solid var(--border);
          border-radius: 3px;
          cursor: pointer;
          display: flex;
          flex-direction: column;
          gap: 6px;
          transition: border-color 120ms var(--ease), background-color 120ms var(--ease);
        }
        .onboard-perm .option:hover:not(:disabled) {
          background: var(--bg-2);
          border-color: var(--cobalt);
        }
        .onboard-perm .option.recommended {
          border-color: var(--cobalt-dim);
        }
        .onboard-perm .o-head {
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .onboard-perm .o-tag {
          font-size: 10px;
          letter-spacing: 0.14em;
          color: var(--cobalt);
          background: rgba(64, 128, 200, 0.12);
          padding: 2px 6px;
          border-radius: 2px;
        }
        .onboard-perm .o-title {
          font-size: var(--fs-md);
          font-weight: 500;
          color: var(--fg-0);
        }
        .onboard-perm .o-body {
          font-size: var(--fs-sm);
          line-height: 1.55;
          color: var(--fg-1);
          margin: 0;
        }
        .onboard-perm .error {
          color: var(--copper);
          font-size: var(--fs-sm);
          margin: 0;
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </div>
  );
}
