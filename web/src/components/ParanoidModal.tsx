import { Show, createMemo } from "solid-js";

import { api } from "../api";
import { store } from "../state";

export function ParanoidModal() {
  const pending = createMemo(() => store.pending_installs[0] ?? null);

  async function resolve(approve: boolean) {
    const p = pending();
    if (!p) return;
    try {
      await api.resolveInstall(p.id, approve);
    } catch (e) {
      window.alert(String(e));
    }
  }

  return (
    <Show when={pending() && store.status?.paranoid_install}>
      <div class="modal-backdrop">
        <div class="modal">
          <div class="head">
            <span class="tag amber">PARANOID INSTALL</span>
            <span class="mono faint" style={{ "font-size": "var(--fs-xs)" }}>
              {pending()!.requested_by_drone_id}
            </span>
          </div>
          <h2>{pending()!.tool_name}</h2>
          <p class="dim whitespace">{pending()!.description}</p>
          <Show when={pending()!.install_commands.length > 0}>
            <div
              class="dim"
              style={{
                "font-size": "var(--fs-xs)",
                "letter-spacing": "0.08em",
                "margin-top": "12px",
              }}
            >
              INSTALL COMMANDS
            </div>
            <pre class="code">{pending()!.install_commands.join("\n")}</pre>
          </Show>
          <Show when={pending()!.usage}>
            <div
              class="dim"
              style={{
                "font-size": "var(--fs-xs)",
                "letter-spacing": "0.08em",
                "margin-top": "12px",
              }}
            >
              USAGE
            </div>
            <pre class="code">{pending()!.usage}</pre>
          </Show>
          <div class="row actions">
            <button class="danger" onClick={() => void resolve(false)}>
              block
            </button>
            <button class="primary" onClick={() => void resolve(true)}>
              approve install
            </button>
          </div>
        </div>
      </div>
      <style>{`
        .modal-backdrop {
          position: fixed;
          inset: 0;
          background: rgba(7, 10, 15, 0.7);
          backdrop-filter: blur(4px);
          z-index: 100;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .modal {
          background: var(--bg-1);
          border: 1px solid var(--amber);
          padding: 22px 26px;
          width: min(560px, 92%);
          border-radius: 4px;
          box-shadow: 0 0 60px rgba(245, 181, 60, 0.18);
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .modal .head {
          display: flex;
          gap: 10px;
          align-items: center;
          margin-bottom: 4px;
        }
        .modal h2 {
          margin: 4px 0;
          font-size: var(--fs-lg);
          font-weight: 500;
        }
        .modal .whitespace { white-space: pre-wrap; margin: 0; line-height: 1.5; }
        .modal .code {
          background: var(--bg-0);
          border: 1px solid var(--border);
          padding: 8px 10px;
          margin: 4px 0 0;
          font-size: var(--fs-sm);
          line-height: 1.4;
          white-space: pre-wrap;
        }
        .modal .actions {
          justify-content: flex-end;
          margin-top: 16px;
        }
      `}</style>
    </Show>
  );
}
