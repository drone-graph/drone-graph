import { Show, createSignal } from "solid-js";

import { api } from "../api";
import { refreshSettings } from "../state";

/** Step 2 of onboarding. Provider key is set, controller is up, substrate
 *  is empty — but before the operator seeds the swarm, force a decision
 *  about cost. The swarm can burn dollars fast on a hard task; "I didn't
 *  realize" should not be a possible state. Either pick a real ceiling or
 *  knowingly select "unlimited"; either way the substrate records the
 *  acknowledgement and onboarding proceeds. */
export function OnboardingBudget() {
  const [value, setValue] = createSignal<string>("25");
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal<string | null>(null);

  const presets: { label: string; usd: number | null; vibe: string }[] = [
    { label: "$5",   usd: 5,   vibe: "cautious" },
    { label: "$25",  usd: 25,  vibe: "typical" },
    { label: "$100", usd: 100, vibe: "ambitious" },
  ];

  async function setCeiling(usd: number | null) {
    setSaving(true);
    setError(null);
    try {
      await api.updateSettings({
        default_cost_ceiling_usd: usd,
        cost_ceiling_acknowledged: true,
      });
      // Apply live to the running controller so the meter has the new
      // ceiling immediately (instead of waiting for a restart).
      await api.setCeiling(usd);
      await refreshSettings();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function submitCustom(e?: SubmitEvent | KeyboardEvent) {
    if (e) e.preventDefault();
    const raw = value().trim();
    if (raw === "") {
      setError("Enter a number, or pick a preset / unlimited below.");
      return;
    }
    const n = Number(raw.replace(/[$,\s]/g, ""));
    if (!Number.isFinite(n) || n <= 0) {
      setError("Enter a positive number, e.g. 25 for $25.");
      return;
    }
    await setCeiling(n);
  }

  return (
    <div class="onboard-budget">
      <div class="frame">
        <div class="header">
          <span class="dot heartbeat" />
          <span class="title">DRONE GRAPH</span>
          <span class="dim sub">· MISSION CONTROL · step 2 of 2</span>
        </div>

        <h1>Set a cost ceiling.</h1>
        <p class="dim sub-line">
          When the swarm's total spend crosses this number, it auto-pauses
          and waits for you. You can raise the ceiling any time from the
          top bar.
        </p>

        <form onSubmit={submitCustom} class="composer">
          <div class="input-row">
            <span class="dollar">$</span>
            <input
              autofocus
              inputmode="decimal"
              value={value()}
              onInput={(e) => setValue(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submitCustom(e);
              }}
              placeholder="25"
            />
            <button class="primary" disabled={saving()}>
              {saving() ? "saving…" : "set ceiling"}
            </button>
          </div>
          <div class="presets">
            {presets.map((p) => (
              <button
                type="button"
                class="preset"
                disabled={saving()}
                onClick={() => {
                  setValue(String(p.usd ?? ""));
                  void setCeiling(p.usd);
                }}
              >
                <span class="preset-label">{p.label}</span>
                <span class="preset-vibe">{p.vibe}</span>
              </button>
            ))}
          </div>
        </form>

        <Show when={error()}>
          <p class="error">{error()}</p>
        </Show>

        <div class="unlimited-row">
          <button
            type="button"
            class="ghost unlimited"
            disabled={saving()}
            onClick={() => {
              if (
                window.confirm(
                  "Run without a ceiling? The swarm can spend more than you expect on hard tasks. You'll still see the running total in the top bar.",
                )
              ) {
                void setCeiling(null);
              }
            }}
          >
            run without a ceiling
          </button>
        </div>

        <p class="faint footer">
          $5 covers about an hour of light exploration on Sonnet.
          $25 is enough for a meaningful PoC pass on most tasks.
          You can change this any time in Settings → tier overrides.
        </p>
      </div>
      <style>{`
        .onboard-budget {
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
        .onboard-budget .frame {
          width: min(560px, 100%);
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .onboard-budget .header {
          display: flex;
          align-items: center;
          gap: 10px;
          letter-spacing: 0.16em;
          font-size: var(--fs-sm);
        }
        .onboard-budget .header .dot {
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: var(--cobalt);
          box-shadow: 0 0 12px var(--cobalt);
        }
        .onboard-budget .title { font-weight: 500; }
        .onboard-budget .sub { letter-spacing: 0.16em; font-size: var(--fs-xs); }
        .onboard-budget h1 {
          margin: 18px 0 0;
          font-weight: 400;
          font-size: var(--fs-xl);
          letter-spacing: 0.01em;
          line-height: 1.35;
        }
        .onboard-budget .sub-line {
          font-size: var(--fs-sm);
          line-height: 1.55;
          margin: 4px 0 0;
        }
        .onboard-budget .composer {
          display: flex;
          flex-direction: column;
          gap: 10px;
          margin-top: 16px;
        }
        .onboard-budget .input-row {
          display: grid;
          grid-template-columns: auto 1fr auto;
          gap: 8px;
          align-items: center;
        }
        .onboard-budget .dollar {
          font-size: var(--fs-lg);
          color: var(--fg-1);
          padding: 0 4px;
        }
        .onboard-budget input {
          font-family: var(--font-mono);
          font-size: var(--fs-lg);
          padding: 10px 12px;
        }
        .onboard-budget .presets {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 8px;
        }
        .onboard-budget .preset {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 4px;
          padding: 10px;
          background: var(--bg-1);
          border: 1px solid var(--border);
          border-radius: 3px;
          cursor: pointer;
          transition: background-color 120ms var(--ease),
            border-color 120ms var(--ease);
        }
        .onboard-budget .preset:hover:not(:disabled) {
          background: var(--bg-2);
          border-color: var(--cobalt);
        }
        .onboard-budget .preset-label {
          font-size: var(--fs-md);
          font-weight: 500;
        }
        .onboard-budget .preset-vibe {
          font-size: var(--fs-xs);
          color: var(--fg-2);
          letter-spacing: 0.04em;
        }
        .onboard-budget .error {
          color: var(--copper);
          font-size: var(--fs-sm);
          margin: 0;
        }
        .onboard-budget .unlimited-row {
          display: flex;
          justify-content: center;
          margin-top: 4px;
        }
        .onboard-budget .unlimited {
          color: var(--fg-2);
          font-size: var(--fs-xs);
          text-decoration: underline dotted;
          background: transparent;
          border: none;
          padding: 4px 8px;
        }
        .onboard-budget .unlimited:hover {
          color: var(--copper);
        }
        .onboard-budget .footer {
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
