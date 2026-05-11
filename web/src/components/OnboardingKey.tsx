import { Show, createSignal } from "solid-js";

import { api } from "../api";
import { refreshSettings, store } from "../state";

/** Fullscreen, stark, single-step provider-key entry. Rendered when no key
 *  has been configured yet — replaces the dashboard chrome entirely so the
 *  first thing the operator sees is one input on a black field. */
export function OnboardingKey() {
  const [provider, setProvider] = createSignal<"anthropic" | "openai">(
    (store.settings?.has_anthropic_key && "anthropic") ||
      (store.settings?.has_openai_key && "openai") ||
      "anthropic",
  );
  const [key, setKey] = createSignal("");
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal<string | null>(null);

  async function begin(e?: SubmitEvent | KeyboardEvent) {
    if (e) e.preventDefault();
    const k = key().trim();
    if (!k || saving()) return;
    setSaving(true);
    setError(null);
    try {
      const patch =
        provider() === "anthropic"
          ? { anthropic_api_key: k, default_provider: "anthropic" }
          : { openai_api_key: k, default_provider: "openai" };
      await api.updateSettings(patch);
      await refreshSettings();
      // Backend boots the controller in maybe_start_controller; the next
      // /api/status the snapshot polls will report state != "idle" and the
      // App component will swap us to OnboardingSeed automatically.
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div class="onboard">
      <div class="frame">
        <div class="header">
          <span class="dot heartbeat" />
          <span class="title">DRONE GRAPH</span>
          <span class="dim sub">· MISSION CONTROL</span>
        </div>

        <h1>To wake the hivemind, give it a key.</h1>
        <p class="dim sub-line">
          Stored locally at <span class="mono">~/.config/drone-graph/settings.json</span>
          {" "}with mode 0600. Used only to talk to the model vendor.
        </p>

        <div class="providers">
          <button
            class="provider-tab"
            classList={{ active: provider() === "anthropic" }}
            onClick={() => setProvider("anthropic")}
          >
            anthropic
          </button>
          <span class="dim or">/</span>
          <button
            class="provider-tab"
            classList={{ active: provider() === "openai" }}
            onClick={() => setProvider("openai")}
          >
            openai
          </button>
        </div>

        <form onSubmit={begin} class="composer">
          <input
            autofocus
            type="password"
            spellcheck={false}
            placeholder={
              provider() === "anthropic" ? "sk-ant-…" : "sk-…"
            }
            value={key()}
            onInput={(e) => setKey(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void begin(e);
            }}
          />
          <button class="primary" disabled={!key().trim() || saving()}>
            {saving() ? "waking…" : "begin"}
          </button>
        </form>

        <Show when={error()}>
          <p class="error">{error()}</p>
        </Show>

        <p class="faint footer">
          You can swap keys, tune model tier overrides, and set cost ceilings
          later in Settings.
        </p>
      </div>
      <style>{`
        .onboard {
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
        .onboard .frame {
          width: min(560px, 100%);
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .onboard .header {
          display: flex;
          align-items: center;
          gap: 10px;
          letter-spacing: 0.16em;
          font-size: var(--fs-sm);
        }
        .onboard .header .dot {
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: var(--cobalt);
          box-shadow: 0 0 12px var(--cobalt);
        }
        .onboard .title { font-weight: 500; }
        .onboard .sub { letter-spacing: 0.16em; font-size: var(--fs-xs); }
        .onboard h1 {
          margin: 18px 0 0;
          font-weight: 400;
          font-size: var(--fs-xl);
          letter-spacing: 0.01em;
          line-height: 1.35;
        }
        .onboard .sub-line {
          font-size: var(--fs-sm);
          line-height: 1.55;
          margin: 4px 0 0;
        }
        .onboard .providers {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-top: 16px;
        }
        .onboard .provider-tab {
          background: transparent;
          border: 1px solid var(--border);
          padding: 4px 12px;
          font-family: var(--font-mono);
          font-size: var(--fs-sm);
          letter-spacing: 0.04em;
          color: var(--fg-1);
        }
        .onboard .provider-tab.active {
          color: var(--cobalt-soft);
          border-color: var(--cobalt);
          background: rgba(60, 110, 245, 0.06);
        }
        .onboard .or { font-size: var(--fs-sm); }
        .onboard .composer {
          display: grid;
          grid-template-columns: 1fr auto;
          gap: 8px;
          margin-top: 8px;
        }
        .onboard input {
          font-family: var(--font-mono);
          font-size: var(--fs-md);
          padding: 8px 10px;
        }
        .onboard .error {
          color: var(--copper);
          font-size: var(--fs-sm);
          margin: 0;
        }
        .onboard .footer {
          margin-top: 18px;
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
