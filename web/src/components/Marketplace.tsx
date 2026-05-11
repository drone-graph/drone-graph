import { For, Show, createMemo, createSignal } from "solid-js";

import { api } from "../api";
import { store } from "../state";
import type { Tool } from "../types";

type Filter = "all" | "builtin" | "installed" | "deprecated" | "flagged";
type SortBy = "recent" | "name" | "kind";

export function Marketplace() {
  const [filter, setFilter] = createSignal<Filter>("all");
  const [sort, setSort] = createSignal<SortBy>("recent");
  const [search, setSearch] = createSignal("");
  const [selected, setSelected] = createSignal<Tool | null>(null);

  const tools = createMemo(() => {
    let arr = store.tools.slice();
    const f = filter();
    if (f === "builtin") arr = arr.filter((t) => t.kind === "builtin");
    if (f === "installed") arr = arr.filter((t) => t.kind === "installed");
    if (f === "deprecated") arr = arr.filter((t) => t.deprecated_at !== null);
    if (f === "flagged") arr = arr.filter((t) => t.flagged_by_alignment);
    const q = search().trim().toLowerCase();
    if (q) {
      arr = arr.filter(
        (t) =>
          t.name.toLowerCase().includes(q) ||
          t.description.toLowerCase().includes(q),
      );
    }
    const s = sort();
    arr.sort((a, b) => {
      if (s === "recent") {
        return (
          new Date(b.last_used_at).getTime() -
          new Date(a.last_used_at).getTime()
        );
      }
      if (s === "name") return a.name.localeCompare(b.name);
      return a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name);
    });
    return arr;
  });

  return (
    <div class="marketplace">
      <div class="bar">
        <input
          placeholder="search tools by name or description…"
          value={search()}
          onInput={(e) => setSearch(e.currentTarget.value)}
        />
        <div class="row">
          <For each={["all", "builtin", "installed", "deprecated", "flagged"] as Filter[]}>
            {(f) => (
              <button
                class="ghost"
                classList={{ active: filter() === f }}
                onClick={() => setFilter(f)}
              >
                {f}
              </button>
            )}
          </For>
        </div>
        <div class="row">
          <span class="faint" style={{ "font-size": "var(--fs-xs)" }}>
            sort
          </span>
          <select
            value={sort()}
            onChange={(e) => setSort(e.currentTarget.value as SortBy)}
          >
            <option value="recent">recent</option>
            <option value="name">name</option>
            <option value="kind">kind</option>
          </select>
        </div>
      </div>

      <div class="grid">
        <div class="list">
          <For each={tools()}>
            {(t) => (
              <ToolCard
                t={t}
                selected={selected()?.name === t.name}
                onSelect={() => setSelected(t)}
              />
            )}
          </For>
          <Show when={tools().length === 0}>
            <div class="faint" style={{ "text-align": "center", padding: "60px" }}>
              no tools matching that filter.
            </div>
          </Show>
        </div>
        <div class="detail">
          <Show
            when={selected()}
            fallback={
              <div class="faint" style={{ padding: "20px" }}>
                select a tool to inspect.
              </div>
            }
          >
            <ToolDetail t={selected()!} />
          </Show>
        </div>
      </div>

      <style>{`
        .marketplace {
          display: flex;
          flex-direction: column;
          height: 100%;
          overflow: hidden;
        }
        .bar {
          display: grid;
          grid-template-columns: 1fr auto auto;
          align-items: center;
          gap: 12px;
          padding: 12px 18px;
          border-bottom: 1px solid var(--border);
        }
        .bar input { font-size: var(--fs-sm); padding: 6px 10px; }
        .bar button.active {
          color: var(--cobalt-soft);
          background: var(--bg-2);
        }
        .bar select {
          background: var(--bg-2);
          color: var(--fg-0);
          border: 1px solid var(--border);
          padding: 3px 6px;
          font-family: var(--font-mono);
          font-size: var(--fs-sm);
        }
        .grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          flex: 1;
          overflow: hidden;
        }
        .list {
          overflow-y: auto;
          padding: 12px 18px;
          display: grid;
          grid-template-columns: 1fr;
          gap: 8px;
          border-right: 1px solid var(--border);
        }
        .detail {
          overflow-y: auto;
        }
      `}</style>
    </div>
  );
}

function ToolCard(props: { t: Tool; selected: boolean; onSelect: () => void }) {
  return (
    <div
      class="card"
      classList={{ selected: props.selected, dim: props.t.deprecated_at !== null }}
      onClick={props.onSelect}
    >
      <div class="row" style={{ "justify-content": "space-between" }}>
        <span class="name">{props.t.name}</span>
        <span class="row">
          <span class={`tag ${tierTag(props.t.trust_tier)}`}>{props.t.trust_tier}</span>
          <span class={`tag ${props.t.kind === "builtin" ? "graphite" : "cobalt"}`}>
            {props.t.kind}
          </span>
        </span>
      </div>
      <div class="desc faint">{oneLine(props.t.description)}</div>
      <Show when={props.t.flagged_by_alignment}>
        <span class="tag copper">alignment-flagged</span>
      </Show>
      <Show when={props.t.deprecated_at}>
        <span class="tag graphite">deprecated</span>
      </Show>
      <style>{`
        .card {
          background: var(--bg-1);
          border: 1px solid var(--border);
          padding: 10px 12px;
          border-radius: 4px;
          cursor: pointer;
          transition: border-color 120ms var(--ease);
        }
        .card:hover { border-color: var(--cobalt); }
        .card.selected { border-color: var(--cobalt); background: var(--bg-2); }
        .card .name { font-size: var(--fs-md); }
        .card .desc {
          font-size: var(--fs-sm);
          margin-top: 4px;
          line-height: 1.5;
        }
        .card.dim { opacity: 0.55; }
      `}</style>
    </div>
  );
}

function ToolDetail(props: { t: Tool }) {
  async function setTier(tier: Tool["trust_tier"]) {
    try {
      await api.setTrustTier(props.t.name, tier);
    } catch (e) {
      window.alert(String(e));
    }
  }
  return (
    <div class="td">
      <div class="head">
        <div class="row" style={{ "justify-content": "space-between" }}>
          <span style={{ "font-size": "var(--fs-lg)" }}>{props.t.name}</span>
          <div class="row">
            <span class={`tag ${tierTag(props.t.trust_tier)}`}>{props.t.trust_tier}</span>
            <span class={`tag ${props.t.kind === "builtin" ? "graphite" : "cobalt"}`}>
              {props.t.kind}
            </span>
          </div>
        </div>
        <div class="faint" style={{ "font-size": "var(--fs-sm)", "margin-top": "4px" }}>
          last used {formatRelative(props.t.last_used_at)} ·
          created {formatRelative(props.t.created_at)}
        </div>
      </div>
      <Section title="description">
        <p class="whitespace">{props.t.description}</p>
      </Section>
      <Show when={props.t.usage}>
        <Section title="usage">
          <pre class="code">{props.t.usage}</pre>
        </Section>
      </Show>
      <Show when={props.t.install_commands.length > 0}>
        <Section title="install commands">
          <pre class="code">{props.t.install_commands.join("\n")}</pre>
        </Section>
      </Show>
      <Show when={props.t.depends_on.length > 0}>
        <Section title="depends on">
          <div class="row" style={{ "flex-wrap": "wrap", gap: "4px" }}>
            <For each={props.t.depends_on}>
              {(n) => <span class="tag graphite">{n}</span>}
            </For>
          </div>
        </Section>
      </Show>
      <Show when={props.t.deprecated_at}>
        <Section title="deprecated">
          <div class="faint" style={{ "font-size": "var(--fs-sm)" }}>
            {props.t.deprecated_reason ?? "(no reason)"} ·{" "}
            {formatRelative(props.t.deprecated_at!)}
          </div>
        </Section>
      </Show>
      <Section title="trust">
        <div class="row" style={{ gap: "6px" }}>
          <For each={["high", "standard", "low", "blocked"] as const}>
            {(tier) => (
              <button
                onClick={() => setTier(tier)}
                classList={{ primary: props.t.trust_tier === tier }}
                disabled={props.t.kind === "builtin" && tier !== "high"}
              >
                {tier}
              </button>
            )}
          </For>
        </div>
        <Show when={props.t.kind === "installed"}>
          <div class="row" style={{ gap: "6px", "margin-top": "8px" }}>
            <button
              onClick={() => void (props.t.flagged_by_alignment ? api.unflagTool(props.t.name) : api.flagTool(props.t.name))}
            >
              {props.t.flagged_by_alignment ? "unflag" : "flag suspicious"}
            </button>
          </div>
        </Show>
      </Section>
      <style>{`
        .td {
          padding: 16px 20px;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        .td .head { border-bottom: 1px solid var(--border); padding-bottom: 10px; }
        .code {
          background: var(--bg-0);
          border: 1px solid var(--border);
          padding: 8px 10px;
          font-size: var(--fs-sm);
          line-height: 1.5;
          white-space: pre-wrap;
          margin: 0;
          color: var(--fg-1);
        }
      `}</style>
    </div>
  );
}

function Section(props: { title: string; children: unknown }) {
  return (
    <div>
      <div
        class="dim"
        style={{
          "font-size": "var(--fs-xs)",
          "letter-spacing": "0.08em",
          "margin-bottom": "4px",
        }}
      >
        {props.title.toUpperCase()}
      </div>
      <div>{props.children as never}</div>
    </div>
  );
}

function tierTag(t: Tool["trust_tier"]): string {
  return { high: "teal", standard: "cobalt", low: "amber", blocked: "copper" }[t];
}
function oneLine(s: string): string {
  const i = s.indexOf("\n");
  return (i === -1 ? s : s.slice(0, i)).trim();
}
function formatRelative(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const diff = Date.now() - t;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}
