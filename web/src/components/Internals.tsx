import { For, Show, createResource, createSignal, onCleanup, onMount } from "solid-js";

import { selectGap, setView, store } from "../state";
import type { Claim, Install } from "../types";

export function Internals() {
  const [claims, { refetch: refetchClaims }] = createResource<Claim[]>(
    async () => {
      try {
        const r = await fetch("/api/signals/claims");
        return r.ok ? ((await r.json()) as Claim[]) : [];
      } catch {
        return [];
      }
    },
  );
  const [installs, { refetch: refetchInstalls }] = createResource<Install[]>(
    async () => {
      try {
        const r = await fetch("/api/signals/installs");
        return r.ok ? ((await r.json()) as Install[]) : [];
      } catch {
        return [];
      }
    },
  );

  // Poll while the view is mounted; stop when unmounted (when the operator
  // navigates to console / marketplace / settings).
  onMount(() => {
    const id = window.setInterval(() => {
      void refetchClaims();
      void refetchInstalls();
    }, 2000);
    onCleanup(() => window.clearInterval(id));
  });

  const [tab, setTab] = createSignal<"claims" | "installs" | "events">(
    "claims",
  );

  return (
    <div class="internals">
      <div class="bar">
        <div class="row">
          <For each={["claims", "installs", "events"] as const}>
            {(t) => (
              <button
                class="ghost"
                classList={{ active: tab() === t }}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            )}
          </For>
        </div>
        <span class="faint" style={{ "margin-left": "auto", "font-size": "var(--fs-xs)" }}>
          run: <span class="mono">{store.status?.run_id ?? "—"}</span>
        </span>
      </div>

      <div class="body">
        <Show when={tab() === "claims"}>
          <div class="dim head-row">
            kind · key · drone · lease (s) · cancelled
          </div>
          <For each={claims() ?? []}>
            {(c) => {
              const isGap = c.kind === "gap";
              return (
                <div
                  class="row-line"
                  classList={{ clickable: isGap }}
                  onClick={() => {
                    if (!isGap) return;
                    selectGap(c.key);
                    setView("console");
                  }}
                  title={isGap ? "open this gap in console" : ""}
                >
                  <span class="tag graphite">{c.kind}</span>
                  <span class="mono ellipsis">{c.key}</span>
                  <span class="faint mono">{c.drone_id.slice(0, 12)}</span>
                  <span
                    class="mono"
                    style={{
                      color: c.expires_at - Date.now() / 1000 < 0 ? "var(--copper)" : "var(--fg-1)",
                    }}
                  >
                    {(c.expires_at - Date.now() / 1000).toFixed(0)}
                  </span>
                  <Show when={c.cancelled}>
                    <span class="tag copper">cancelled</span>
                  </Show>
                </div>
              );
            }}
          </For>
          <Show when={(claims() ?? []).length === 0}>
            <div class="faint empty">no active claims.</div>
          </Show>
        </Show>

        <Show when={tab() === "installs"}>
          <div class="dim head-row">install_key · drone · when · usage</div>
          <For each={installs() ?? []}>
            {(i) => (
              <div class="row-line">
                <span class="mono ellipsis">{i.key}</span>
                <span class="faint mono">{i.installed_by.slice(0, 12)}</span>
                <span class="faint">{Math.floor(Date.now() / 1000 - i.installed_at)}s ago</span>
                <span class="ellipsis faint">{i.usage ?? "—"}</span>
              </div>
            )}
          </For>
          <Show when={(installs() ?? []).length === 0}>
            <div class="faint empty">no installs recorded.</div>
          </Show>
        </Show>

        <Show when={tab() === "events"}>
          <div class="dim head-row">event tape (raw)</div>
          <pre class="raw">{store.events_tail.slice(-200).map((e) => JSON.stringify(e)).join("\n")}</pre>
        </Show>
      </div>

      <style>{`
        .internals {
          display: flex;
          flex-direction: column;
          height: 100%;
          overflow: hidden;
        }
        .bar {
          display: flex;
          align-items: center;
          padding: 10px 18px;
          gap: 12px;
          border-bottom: 1px solid var(--border);
        }
        .bar button.active {
          color: var(--cobalt-soft);
          background: var(--bg-2);
        }
        .body {
          flex: 1;
          overflow-y: auto;
          padding: 14px 22px;
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .head-row {
          font-size: var(--fs-xs);
          letter-spacing: 0.06em;
          margin-bottom: 8px;
        }
        .row-line {
          display: grid;
          grid-template-columns: 100px 1fr 140px 100px auto;
          gap: 10px;
          padding: 4px 6px;
          margin: 0 -6px;
          align-items: center;
          font-size: var(--fs-sm);
          border-bottom: 1px solid var(--border);
        }
        .row-line.clickable {
          cursor: pointer;
          transition: background-color 100ms var(--ease);
        }
        .row-line.clickable:hover {
          background: var(--bg-2);
        }
        .ellipsis {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .empty { padding: 40px 0; text-align: center; }
        .raw {
          background: var(--bg-0);
          border: 1px solid var(--border);
          font-size: 11px;
          line-height: 1.4;
          padding: 8px 10px;
          white-space: pre-wrap;
          overflow-x: auto;
          margin: 0;
        }
      `}</style>
    </div>
  );
}
