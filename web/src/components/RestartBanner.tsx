import { Show, createSignal } from "solid-js";

import { api } from "../api";
import { refreshSettings, store } from "../state";

/** Surfaced when Settings has changed provider or model on a running
 *  controller. Click "restart swarm" to rebuild with the new config. */
export function RestartBanner() {
  const [busy, setBusy] = createSignal(false);

  async function restart() {
    setBusy(true);
    try {
      await api.restartSwarm();
      await refreshSettings();
    } catch (e) {
      window.alert(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Show when={store.status?.needs_restart}>
      <div class="restart-banner">
        <span class="tag amber">RESTART NEEDED</span>
        <span class="msg">
          {store.status?.needs_restart_reason ?? "settings changed"}.
          swarm paused.
        </span>
        <button
          class="primary"
          disabled={busy()}
          onClick={restart}
          style={{ "font-size": "var(--fs-xs)", padding: "3px 10px" }}
        >
          restart swarm
        </button>
        <style>{`
          .restart-banner {
            position: absolute;
            top: calc(var(--topbar-h) + 8px);
            left: 50%;
            transform: translateX(-50%);
            display: flex;
            align-items: center;
            gap: 10px;
            background: var(--bg-1);
            border: 1px solid var(--amber);
            border-radius: 3px;
            padding: 6px 10px;
            box-shadow: 0 0 24px rgba(245, 181, 60, 0.18);
            z-index: 7;
            font-size: var(--fs-sm);
            max-width: 80%;
          }
          .restart-banner .msg {
            color: var(--fg-0);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
        `}</style>
      </div>
    </Show>
  );
}
