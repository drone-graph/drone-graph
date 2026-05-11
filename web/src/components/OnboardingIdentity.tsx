import { Show, createSignal } from "solid-js";

import { api } from "../api";
import { refreshSettings } from "../state";

/** Step 3 of onboarding (after key + budget, before seed). One decision:
 *  is the swarm ever allowed to act *as* the operator?
 *
 *  Off (default, recommended): drones always run in an isolated sandbox.
 *  They use swarm-managed personas (cm_create_persona / cm_use_persona)
 *  when external identity is needed. The operator never gets dragged
 *  in unless they explicitly choose to be.
 *
 *  On: Gap Finding can flag specific gaps as needing the operator's real
 *  identity. Each flagged gap pauses for inbox approval before dispatch.
 */
export function OnboardingIdentity() {
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal<string | null>(null);

  async function choose(allow: boolean) {
    setSaving(true);
    setError(null);
    try {
      await api.updateSettings({ allow_operator_identity: allow });
      await refreshSettings();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div class="onboard-id">
      <div class="frame">
        <div class="header">
          <span class="dot heartbeat" />
          <span class="title">DRONE GRAPH</span>
          <span class="dim sub">· MISSION CONTROL · step 3 of 3</span>
        </div>

        <h1>Should drones ever act as you?</h1>
        <p class="dim sub-line">
          By default, every drone runs in an isolated sandbox — its own
          throwaway $HOME, no access to your GitHub, no ssh keys, no
          saved logins, no API tokens. The swarm acts as itself, with
          swarm-managed personas it picks or creates as needed.
        </p>

        <div class="options">
          <button
            type="button"
            class="option recommended"
            disabled={saving()}
            onClick={() => void choose(false)}
          >
            <div class="o-head">
              <span class="o-tag">RECOMMENDED</span>
              <span class="o-title">Keep me out of it</span>
            </div>
            <p class="o-body">
              Drones never get access to your accounts, files, or
              credentials. Anything that genuinely requires <em>you</em>{" "}
              gets pushed back as an action inbox item — and the swarm
              works around it where it can.
            </p>
          </button>

          <button
            type="button"
            class="option"
            disabled={saving()}
            onClick={() => void choose(true)}
          >
            <div class="o-head">
              <span class="o-title">Let me approve case-by-case</span>
            </div>
            <p class="o-body">
              Gap Finding can flag specific gaps as needing your real
              identity (your $HOME, your env, your accounts). Each
              flagged gap waits for your explicit approval in the
              action inbox before any drone dispatches against it.
            </p>
          </button>
        </div>

        <Show when={error()}>
          <p class="error">{error()}</p>
        </Show>

        <p class="faint footer">
          You can change this any time in Settings.
        </p>
      </div>
      <style>{`
        .onboard-id {
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
        .onboard-id .frame {
          width: min(600px, 100%);
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .onboard-id .header {
          display: flex;
          align-items: center;
          gap: 10px;
          letter-spacing: 0.16em;
          font-size: var(--fs-sm);
        }
        .onboard-id .header .dot {
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: var(--cobalt);
          box-shadow: 0 0 12px var(--cobalt);
        }
        .onboard-id .title { font-weight: 500; }
        .onboard-id .sub { letter-spacing: 0.16em; font-size: var(--fs-xs); }
        .onboard-id h1 {
          margin: 18px 0 0;
          font-weight: 400;
          font-size: var(--fs-xl);
          letter-spacing: 0.01em;
          line-height: 1.35;
        }
        .onboard-id .sub-line {
          font-size: var(--fs-sm);
          line-height: 1.55;
          margin: 4px 0 0;
        }
        .onboard-id .options {
          display: flex;
          flex-direction: column;
          gap: 10px;
          margin-top: 16px;
        }
        .onboard-id .option {
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
        .onboard-id .option:hover:not(:disabled) {
          background: var(--bg-2);
          border-color: var(--cobalt);
        }
        .onboard-id .option.recommended {
          border-color: var(--cobalt-dim);
        }
        .onboard-id .o-head {
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .onboard-id .o-tag {
          font-size: 10px;
          letter-spacing: 0.14em;
          color: var(--cobalt);
          background: rgba(64, 128, 200, 0.12);
          padding: 2px 6px;
          border-radius: 2px;
        }
        .onboard-id .o-title {
          font-size: var(--fs-md);
          font-weight: 500;
          color: var(--fg-0);
        }
        .onboard-id .o-body {
          font-size: var(--fs-sm);
          line-height: 1.55;
          color: var(--fg-1);
          margin: 0;
        }
        .onboard-id .o-body em {
          color: var(--fg-0);
          font-style: italic;
        }
        .onboard-id .error {
          color: var(--copper);
          font-size: var(--fs-sm);
          margin: 0;
        }
        .onboard-id .footer {
          margin-top: 14px;
          font-size: var(--fs-xs);
          line-height: 1.6;
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </div>
  );
}
