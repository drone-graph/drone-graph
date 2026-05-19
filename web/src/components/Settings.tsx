import { For, Show, createMemo, createResource, createSignal, onMount } from "solid-js";

import { api } from "../api";
import { isSoundEnabled, setSoundEnabled } from "../sound";
import { refreshSettings, setView, store } from "../state";
import type { ModelRegistry } from "../types";
import { BrowserProfiles } from "./BrowserProfiles";

export function Settings() {
  const settings = () => store.settings;
  const [anthropicKey, setAnthropicKey] = createSignal("");
  const [openaiKey, setOpenaiKey] = createSignal("");
  const [provider, setProvider] = createSignal(
    store.settings?.default_provider ?? "",
  );
  const [model, setModel] = createSignal(store.settings?.default_model ?? "");
  const [ceiling, setCeiling] = createSignal(
    store.settings?.default_cost_ceiling_usd?.toString() ?? "",
  );
  const [paranoid, setParanoid] = createSignal(
    !!store.settings?.default_paranoid_install,
  );
  const [maxBrowsers, setMaxBrowsers] = createSignal(
    String(store.settings?.max_concurrent_browsers ?? 4),
  );
  const [permissionTier, setPermissionTier] = createSignal<
    "open" | "ask_external" | "ask_everything"
  >(store.settings?.permission_tier ?? "ask_external");
  const [workspaceDir, setWorkspaceDir] = createSignal(
    store.settings?.workspace_dir ?? "",
  );
  const [saving, setSaving] = createSignal(false);
  const [savedAt, setSavedAt] = createSignal<Date | null>(null);
  const [error, setError] = createSignal<string | null>(null);
  const [authStatus, setAuthStatus] = createSignal<{has_profile: boolean; cdp_running: boolean} | null>(null);

  const refreshAuthStatus = async () => {
    try {
      const result = await api.authenticatedProfileStatus();
      setAuthStatus(result);
    } catch {
      /* ignore */
    }
  };

  const hasAnyKey = createMemo(
    () =>
      !!(settings()?.has_anthropic_key || settings()?.has_openai_key),
  );

  const [registry] = createResource<ModelRegistry>(async () => {
    try {
      return await api.models();
    } catch {
      return { populated: false, tier_defaults_by_provider: {}, tiers: ["nano", "mini", "standard", "advanced", "frontier"], models: [] };
    }
  });

  const activeModels = createMemo(() => {
    const reg = registry();
    if (!reg?.populated) return [];
    const prov = provider();
    return reg.models.filter(
      (m) => !m.deprecated && (prov === "" || m.provider === prov),
    );
  });

  // Local editing state for tier overrides; starts from the saved values.
  const [overrides, setOverrides] = createSignal<
    Record<string, Record<string, string>>
  >(store.settings?.tier_overrides ?? {});
  const setOverride = (prov: string, tier: string, gid: string) => {
    setOverrides((prev) => {
      const next: Record<string, Record<string, string>> = {
        ...prev,
        [prov]: { ...(prev[prov] ?? {}) },
      };
      if (gid) {
        next[prov][tier] = gid;
      } else {
        delete next[prov][tier];
      }
      return next;
    });
  };
  const effectiveTier = (prov: string, tier: string): string => {
    const override = overrides()[prov]?.[tier];
    if (override) return override;
    return registry()?.tier_defaults_by_provider?.[prov]?.[tier] ?? "";
  };

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const ceilingNum =
        ceiling().trim() === "" ? null : Number(ceiling().trim());
      if (ceilingNum !== null && !Number.isFinite(ceilingNum)) {
        setError("Cost ceiling must be a number or empty.");
        return;
      }
      await api.updateSettings({
        // Only send fields with explicit user input. The backend treats
        // undefined as "leave alone"; empty string as "clear".
        ...(anthropicKey() !== "" && { anthropic_api_key: anthropicKey() }),
        ...(openaiKey() !== "" && { openai_api_key: openaiKey() }),
        default_provider: provider() || null,
        default_model: model() || null,
        default_cost_ceiling_usd: ceilingNum,
        default_paranoid_install: paranoid(),
        tier_overrides: overrides(),
        ...(maxBrowsers().trim() !== "" && {
          max_concurrent_browsers: Math.max(1, Number(maxBrowsers().trim())),
        }),
        permission_tier: permissionTier(),
        workspace_dir: workspaceDir().trim() || null,
      });
      await refreshSettings();
      setAnthropicKey("");
      setOpenaiKey("");
      setSavedAt(new Date());
      // No reload — App.tsx watches store.status and routes automatically
      // (OnboardingKey → OnboardingSeed → dashboard) as state evolves.
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function clearKey(which: "anthropic" | "openai") {
    if (!window.confirm(`Clear the ${which} key?`)) return;
    try {
      await api.updateSettings(
        which === "anthropic"
          ? { anthropic_api_key: "" }
          : { openai_api_key: "" },
      );
      await refreshSettings();
    } catch (e) {
      window.alert(String(e));
    }
  }

  function toggleSound() {
    const next = !isSoundEnabled();
    setSoundEnabled(next);
    void api.updateSettings({ sound_enabled: next });
    void refreshSettings();
  }

  onMount(() => { void refreshAuthStatus(); });

  return (
    <div class="settings">
      <div class="bar">
        <h2>Settings</h2>
        <span class="faint" style={{ "font-size": "var(--fs-xs)" }}>
          {settings()?.settings_path ?? "—"}
        </span>
      </div>
      <div class="body">
        <Section title="provider keys">
          <p class="dim" style={{ "line-height": "1.55", "font-size": "var(--fs-sm)" }}>
            Stored locally at <span class="mono">{settings()?.settings_path}</span> with file
            mode 0600. Keys are also written into{" "}
            <span class="mono">os.environ</span> so the existing tooling
            (model_registry, providers) picks them up. Drones never see
            keys directly; they're injected via subprocess env.
          </p>

          <KeyRow
            label="ANTHROPIC_API_KEY"
            hint={settings()?.anthropic_key_hint ?? null}
            present={!!settings()?.has_anthropic_key}
            value={anthropicKey()}
            onInput={setAnthropicKey}
            onClear={() => void clearKey("anthropic")}
          />
          <KeyRow
            label="OPENAI_API_KEY"
            hint={settings()?.openai_key_hint ?? null}
            present={!!settings()?.has_openai_key}
            value={openaiKey()}
            onInput={setOpenaiKey}
            onClear={() => void clearKey("openai")}
          />
        </Section>

        <Section title="defaults">
          <Field label="default provider">
            <select
              value={provider()}
              onChange={(e) => setProvider(e.currentTarget.value)}
            >
              <option value="">(auto — pick whichever key is set)</option>
              <option value="anthropic">anthropic</option>
              <option value="openai">openai</option>
            </select>
          </Field>
          <Field label="model (presets — GF + Alignment)">
            <Show
              when={registry()?.populated && activeModels().length > 0}
              fallback={
                <input
                  placeholder="claude-sonnet-4-6 or gpt-4o"
                  value={model()}
                  onInput={(e) => setModel(e.currentTarget.value)}
                />
              }
            >
              <select
                value={model()}
                onChange={(e) => setModel(e.currentTarget.value)}
              >
                <option value="">(default — registry standard tier)</option>
                <For each={activeModels()}>
                  {(m) => (
                    <option value={m.vendor_model_id}>
                      {m.vendor_model_id} · {m.provider} · ${m.input_price_per_million_usd}/${m.output_price_per_million_usd} per M
                    </option>
                  )}
                </For>
              </select>
            </Show>
            <span class="faint" style={{ "font-size": "var(--fs-xs)", "margin-top": "3px" }}>
              Workers route per-gap via the model registry (gap.model_tier →
              tier_defaults). This setting is just the preset model.
            </span>
          </Field>
          <Show when={registry()?.populated}>
            <Field label="tier overrides — pick a different model for any tier (presets always use frontier)">
              <div class="tier-grid">
                <For each={Object.keys(registry()!.tier_defaults_by_provider)}>
                  {(prov) => (
                    <div class="tier-provider">
                      <div class="tier-prov-label">
                        <span class="tag cobalt">{prov}</span>
                      </div>
                      <For each={registry()!.tiers}>
                        {(tier) => (
                          <div class="tier-row-edit">
                            <span class="tag graphite tier-name">{tier}</span>
                            <select
                              value={effectiveTier(prov, tier)}
                              onChange={(e) =>
                                setOverride(prov, tier, e.currentTarget.value)
                              }
                            >
                              <For
                                each={registry()!.models.filter(
                                  (m) => !m.deprecated && m.provider === prov,
                                )}
                              >
                                {(m) => (
                                  <option value={m.dgraph_model_id}>
                                    {m.vendor_model_id} · ${m.input_price_per_million_usd}/${m.output_price_per_million_usd} per M
                                  </option>
                                )}
                              </For>
                            </select>
                            <span class="faint" style={{ "font-size": "var(--fs-xs)" }}>
                              <Show
                                when={overrides()[prov]?.[tier]}
                                fallback={<>default</>}
                              >
                                <a
                                  class="link"
                                  onClick={() => setOverride(prov, tier, "")}
                                >
                                  reset
                                </a>
                              </Show>
                            </span>
                          </div>
                        )}
                      </For>
                    </div>
                  )}
                </For>
              </div>
            </Field>
          </Show>
          <Field label="cost ceiling (USD)">
            <input
              placeholder="unlimited"
              value={ceiling()}
              onInput={(e) => setCeiling(e.currentTarget.value)}
            />
          </Field>
          <Field label="paranoid install mode by default">
            <label class="row" style={{ gap: "6px" }}>
              <input
                type="checkbox"
                checked={paranoid()}
                onChange={(e) => setParanoid(e.currentTarget.checked)}
                style={{ width: "auto" }}
              />
              <span class="dim" style={{ "font-size": "var(--fs-sm)" }}>
                require operator approval before any installed tool runs
              </span>
            </label>
          </Field>
          <Field label="max simultaneous browsers">
            <input
              type="number"
              min="1"
              max="32"
              value={maxBrowsers()}
              onInput={(e) => setMaxBrowsers(e.currentTarget.value)}
              style={{ width: "80px" }}
            />
            <span class="dim" style={{ "font-size": "var(--fs-xs)", "margin-left": "8px" }}>
              drones requesting a browser past this cap wait for a free slot.
            </span>
          </Field>
        </Section>

        <Section title="workspace">
          <p class="dim" style={{ "line-height": "1.55", "font-size": "var(--fs-sm)" }}>
            Drones create a subfolder per gap inside this directory. Leave
            empty to use the default{" "}
            <span class="mono">./workspace</span> next to the project.
          </p>
          <Field label="workspace directory">
            <input
              type="text"
              placeholder="/home/you/drone-workspace"
              value={workspaceDir()}
              onInput={(e) => setWorkspaceDir(e.currentTarget.value)}
            />
          </Field>
        </Section>

        <Section title="permissions">
          <Field label="permission tier">
            <select
              value={permissionTier()}
              onChange={(e) =>
                setPermissionTier(
                  e.currentTarget.value as
                    | "open"
                    | "ask_external"
                    | "ask_everything",
                )
              }
            >
              <option value="open">open — no prompts</option>
              <option value="ask_external">
                ask before external actions
              </option>
              <option value="ask_everything">
                ask before every action
              </option>
            </select>
            <span class="dim" style={{ "font-size": "var(--fs-xs)", "margin-left": "8px" }}>
              Drones always have full access. The tier governs which
              tool calls block for your approval.
            </span>
          </Field>
        </Section>

        <Section title="audio">
          <button onClick={toggleSound}>
            {isSoundEnabled() ? "♪ disable" : "· enable"} atmospheric sound
          </button>
        </Section>

        <Section title="browser">
          <p class="dim" style={{ "font-size": "var(--fs-sm)", "margin": "0 0 6px" }}>
            Launch Chrome to manually sign into services. Uses the same anti-detection
            measures as drones (real Chrome channel, stealth patches). Sessions are
            persisted on disk for drone reuse.
          </p>
          <BrowserProfiles />
        </Section>

        <Section title="authenticated Google profile">
          <Field label="profile path">
            <Show when={authStatus()?.has_profile} fallback={<span class="faint">Not configured</span>}>
              <span style={{color: "var(--green)"}}>Profile ready</span>
            </Show>
          </Field>
          <Field label="status">
            <Show when={authStatus()?.cdp_running} fallback={<span class="faint">Stopped</span>}>
              <span style={{color: "var(--green)"}}>Running</span>
            </Show>
          </Field>
          <div class="row" style={{gap: "8px", "margin-top": "8px"}}>
            <button onClick={async () => {
              await api.authenticatedProfileSetup();
              await refreshAuthStatus();
            }}>Launch Setup</button>
            <button onClick={async () => {
              await api.authenticatedProfileStart();
              await refreshAuthStatus();
            }}>Start Chrome</button>
            <button onClick={async () => {
              await api.authenticatedProfileStop();
              await refreshAuthStatus();
            }}>Stop Chrome</button>
          </div>
        </Section>

        <div class="actions">
          <Show when={error()}>
            <span class="copper" style={{ "font-size": "var(--fs-sm)" }}>
              {error()}
            </span>
          </Show>
          <Show when={savedAt()}>
            <span class="faint" style={{ "font-size": "var(--fs-xs)" }}>
              saved {savedAt()!.toLocaleTimeString()}
            </span>
          </Show>
          <button class="primary" onClick={save} disabled={saving()}>
            save settings
          </button>
          <Show when={hasAnyKey()}>
            <button onClick={() => setView("console")}>back to console</button>
          </Show>
        </div>
      </div>

      <style>{`
        .settings {
          display: flex;
          flex-direction: column;
          height: 100%;
          overflow: hidden;
        }
        .settings .bar {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          padding: 14px 22px;
          border-bottom: 1px solid var(--border);
        }
        .settings h2 {
          margin: 0;
          font-size: var(--fs-lg);
          font-weight: 500;
        }
        .settings .body {
          flex: 1;
          overflow-y: auto;
          padding: 18px 26px 80px;
          display: flex;
          flex-direction: column;
          gap: 24px;
          max-width: 720px;
        }
        .settings .actions {
          display: flex;
          justify-content: flex-end;
          gap: 10px;
          align-items: center;
        }
        .copper { color: var(--copper); }
        .tier-grid {
          display: flex;
          flex-direction: column;
          gap: 14px;
          background: var(--bg-2);
          border: 1px solid var(--border);
          border-radius: 3px;
          padding: 10px 12px;
        }
        .tier-row {
          display: flex;
          gap: 10px;
          align-items: center;
        }
        .tier-provider {
          display: flex;
          flex-direction: column;
          gap: 5px;
        }
        .tier-prov-label {
          margin-bottom: 2px;
        }
        .tier-row-edit {
          display: grid;
          grid-template-columns: 80px 1fr 60px;
          gap: 10px;
          align-items: center;
        }
        .tier-row-edit .tier-name {
          justify-self: start;
        }
        .tier-row-edit select {
          background: var(--bg-1);
          color: var(--fg-0);
          border: 1px solid var(--border);
          padding: 3px 6px;
          font-family: var(--font-mono);
          font-size: var(--fs-xs);
        }
        .tier-row-edit .link {
          cursor: pointer;
          text-decoration: underline dotted;
        }
      `}</style>
    </div>
  );
}

function Section(props: { title: string; children: unknown }) {
  return (
    <div class="section">
      <div
        class="dim"
        style={{
          "font-size": "var(--fs-xs)",
          "letter-spacing": "0.08em",
          "margin-bottom": "8px",
        }}
      >
        {props.title.toUpperCase()}
      </div>
      <div class="col" style={{ gap: "10px" }}>{props.children as never}</div>
    </div>
  );
}

function Field(props: { label: string; children: unknown }) {
  return (
    <label class="col" style={{ gap: "3px" }}>
      <span class="faint" style={{ "font-size": "var(--fs-xs)" }}>{props.label}</span>
      {props.children as never}
    </label>
  );
}

function KeyRow(props: {
  label: string;
  hint: string | null;
  present: boolean;
  value: string;
  onInput: (v: string) => void;
  onClear: () => void;
}) {
  const [visible, setVisible] = createSignal(false);
  return (
    <div class="col" style={{ gap: "4px" }}>
      <div class="row" style={{ "justify-content": "space-between" }}>
        <span class="faint mono" style={{ "font-size": "var(--fs-xs)" }}>
          {props.label}
        </span>
        <Show when={props.present}>
          <div class="row">
            <span class="tag teal">set · {props.hint}</span>
            <button
              class="ghost"
              onClick={props.onClear}
              style={{ "font-size": "var(--fs-xs)", padding: "2px 6px" }}
            >
              clear
            </button>
          </div>
        </Show>
      </div>
      <div class="row">
        <input
          type={visible() ? "text" : "password"}
          value={props.value}
          placeholder={props.present ? "leave blank to keep current" : "paste key here"}
          onInput={(e) => props.onInput(e.currentTarget.value)}
        />
        <button
          class="ghost"
          onClick={() => setVisible(!visible())}
          style={{ "font-size": "var(--fs-xs)" }}
        >
          {visible() ? "hide" : "show"}
        </button>
      </div>
    </div>
  );
}
