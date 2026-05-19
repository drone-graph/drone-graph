import { Show, createSignal } from "solid-js";

import { api } from "../api";
import { refreshSettings, store } from "../state";

/** Workspace directory selection — shown right after API keys so the operator
 *  can decide where drone-generated files (CSV, Excel, websites, etc.) land.
 *
 *  Browsers don't expose a native folder picker for security reasons, so we
 *  offer two paths:
 *    1. Type or paste an absolute path (works everywhere).
 *    2. Use a <input type="file" webkitdirectory> on Chromium-based browsers
 *       to let the user select a folder and read its path from the first file.
 */
export function OnboardingWorkspace() {
  const [path, setPath] = createSignal(store.settings?.workspace_dir ?? "");
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal<string | null>(null);
  const [useDefault, setUseDefault] = createSignal(false);

  async function submit(e?: SubmitEvent) {
    if (e) e.preventDefault();
    if (saving()) return;

    const p = path().trim();
    if (!p && !useDefault()) {
      setError("Enter a folder path or choose Use default.");
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await api.updateSettings({
        workspace_dir: useDefault() ? null : p,
        workspace_dir_acknowledged: true,
      });
      await refreshSettings();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  function handleDirectoryPick(
    e: Event & { currentTarget: HTMLInputElement },
  ) {
    const files = e.currentTarget.files;
    if (!files || files.length === 0) return;
    // webkitdirectory gives a FileList where each entry has a webkitRelativePath
    // like "MyFolder/sub/file.txt". We reconstruct the parent directory from the
    // first file's path minus its relative portion.
    const first = files[0];
    const rel = (first as any).webkitRelativePath as string;
    const slashIdx = rel.indexOf("/");
    if (slashIdx > 0) {
      const folderName = rel.slice(0, slashIdx);
      // We don't have the absolute prefix from the File API, so we ask the
      // user to confirm or edit the path. On most OSes the folder name alone
      // isn't enough, but this at least pre-fills something useful.
      setPath(folderName);
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

        <h1>Where should drones save their work?</h1>
        <p class="dim sub-line">
          Every drone gets its own subfolder inside this directory. Leave it
          blank to use the default{" "}
          <span class="mono">./workspace</span> next to the project.
        </p>

        <form onSubmit={submit} class="composer workspace-composer">
          <input
            type="text"
            placeholder="/home/you/drone-workspace  or  C:\Users\You\DroneWorkspace"
            value={path()}
            onInput={(e) => {
              setPath(e.currentTarget.value);
              setUseDefault(false);
            }}
            disabled={useDefault()}
          />
          {/* Hidden directory picker for Chromium browsers */}
          <label class="directory-pill">
            <input
              type="file"
              {...({ webkitdirectory: "" } as any)}
              style={{ display: "none" }}
              onChange={handleDirectoryPick}
            />
            browse…
          </label>
          <button class="primary" disabled={saving()}>
            {saving() ? "saving…" : "save"}
          </button>
        </form>

        <div class="workspace-options">
          <label class="workspace-option-row">
            <input
              type="checkbox"
              checked={useDefault()}
              onChange={(e) => {
                const checked = e.currentTarget.checked;
                setUseDefault(checked);
                if (checked) setPath("");
              }}
            />
            <span class="dim">Use default workspace folder</span>
          </label>
        </div>

        <Show when={error()}>
          <p class="error">{error()}</p>
        </Show>

        <p class="faint footer">
          You can change this anytime in Settings → Workspace.
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
          width: min(620px, 100%);
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
        .workspace-composer {
          display: grid;
          grid-template-columns: 1fr auto auto;
          gap: 8px;
          margin-top: 8px;
          align-items: center;
        }
        .workspace-composer input {
          font-family: var(--font-mono);
          font-size: var(--fs-md);
          padding: 8px 10px;
        }
        .directory-pill {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          padding: 8px 12px;
          border: 1px solid var(--border);
          background: transparent;
          color: var(--fg-1);
          font-size: var(--fs-sm);
          cursor: pointer;
          font-family: var(--font-mono);
          letter-spacing: 0.04em;
        }
        .directory-pill:hover {
          border-color: var(--cobalt);
          color: var(--cobalt-soft);
          background: rgba(60, 110, 245, 0.06);
        }
        .workspace-options {
          margin-top: 4px;
        }
        .workspace-option-row {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: var(--fs-sm);
          cursor: pointer;
        }
        .workspace-option-row input[type="checkbox"] {
          width: 14px;
          height: 14px;
          accent-color: var(--cobalt);
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
