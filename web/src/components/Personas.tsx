// Persona reality budget panel.
//
// Surfaces the swarm's identities + the lifecycle of each capability so
// the operator can see at a glance what the swarm can actually do in
// the world vs. what it merely aspires to.
//
// Capability statuses:
//   pending    — wish; nothing real yet.
//   registered — an external entity exists (account, key) but it
//                hasn't been proven to work end-to-end.
//   verified   — battle-tested; a drone has exercised it successfully.

import { For, Show, createResource, createSignal } from "solid-js";

import { api } from "../api";
import type { CapabilityStatus, Persona, PersonaCapability } from "../types";

export function Personas() {
  const [personas, { refetch }] = createResource<Persona[]>(async () => {
    try {
      return await api.listPersonas();
    } catch {
      return [];
    }
  });

  return (
    <div class="personas">
      <div class="head">
        <span class="dim" style={{ "letter-spacing": "0.08em" }}>
          PERSONAS · {(personas() ?? []).length}
        </span>
        <p class="faint sub">
          Personas are swarm-managed identities. Their fields are{" "}
          <em>goals tracked toward verification</em>: a capability with
          <em> pending</em> status is a wish; <em>registered</em> means
          a real external entity exists; <em>verified</em> means a
          drone has actually exercised it. Read the matrix before
          assuming any persona can do anything externally.
        </p>
      </div>
      <Show
        when={(personas() ?? []).length > 0}
        fallback={
          <div class="empty faint">
            No personas yet. The swarm-zero baseline is minted at
            substrate init; drones mint additional personas via
            cm_create_persona.
          </div>
        }
      >
        <div class="grid">
          <For each={personas()}>
            {(p) => (
              <PersonaCard p={p} onChanged={() => void refetch()} />
            )}
          </For>
        </div>
      </Show>
      <style>{`
        .personas {
          height: 100%;
          overflow-y: auto;
          padding: 16px 18px;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        .personas .head { display: flex; flex-direction: column; gap: 6px; }
        .personas .sub { max-width: 720px; line-height: 1.55; font-size: var(--fs-sm); }
        .personas .empty { padding: 30px; text-align: center; }
        .personas .grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
          gap: 12px;
        }
      `}</style>
    </div>
  );
}

function PersonaCard(props: { p: Persona; onChanged: () => void }) {
  const [busy, setBusy] = createSignal(false);
  async function toggleReal() {
    setBusy(true);
    try {
      await api.setPersonaBackedByRealHuman(props.p.name, !props.p.backed_by_real_human);
      props.onChanged();
    } catch (e) {
      window.alert(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function addCapability() {
    const key = window.prompt("New capability key (e.g. stripe_express, dns_zone):");
    if (!key || !key.trim()) return;
    const desired =
      window.prompt(`Desired value for "${key.trim()}" (optional):`) ?? "";
    setBusy(true);
    try {
      await api.upsertPersonaCapability(props.p.name, key.trim(), {
        status: "pending",
        desired_value: desired || undefined,
      });
      props.onChanged();
    } catch (e) {
      window.alert(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div class="card">
      <div class="row" style={{ "justify-content": "space-between" }}>
        <div class="col" style={{ gap: "2px" }}>
          <div class="row" style={{ gap: "6px" }}>
            <span class="tag teal">{props.p.name}</span>
            <Show when={props.p.backed_by_real_human}>
              <span class="tag amber" title="a real human stands behind this persona">
                real human
              </span>
            </Show>
          </div>
          <div class="display">{props.p.display_name}</div>
        </div>
        <div class="row" style={{ gap: "6px" }}>
          <button
            class="ghost"
            disabled={busy()}
            onClick={() => void toggleReal()}
            title={
              props.p.backed_by_real_human
                ? "remove the real-human flag"
                : "mark this persona as backed by a real human"
            }
          >
            {props.p.backed_by_real_human ? "✓ real" : "mark real"}
          </button>
        </div>
      </div>
      <Show when={props.p.bio}>
        <p class="bio">{props.p.bio}</p>
      </Show>
      <div class="caps-head row" style={{ "justify-content": "space-between" }}>
        <span class="dim" style={{ "font-size": "var(--fs-xs)", "letter-spacing": "0.08em" }}>
          CAPABILITIES · {props.p.capabilities.length}
        </span>
        <button
          class="ghost"
          disabled={busy()}
          onClick={() => void addCapability()}
          style={{ "font-size": "var(--fs-xs)" }}
        >
          + add
        </button>
      </div>
      <Show
        when={props.p.capabilities.length > 0}
        fallback={
          <div class="faint" style={{ padding: "8px", "font-size": "var(--fs-sm)" }}>
            No capabilities yet. This persona can't reliably do anything
            in the world until at least one capability is verified.
          </div>
        }
      >
        <div class="caps">
          <For each={props.p.capabilities}>
            {(c) => (
              <CapabilityRow
                p={props.p}
                c={c}
                onChanged={props.onChanged}
              />
            )}
          </For>
        </div>
      </Show>
      <Show when={props.p.ssh_fingerprint}>
        <div class="fp mono" title="ssh public key fingerprint">
          {props.p.ssh_fingerprint}
        </div>
      </Show>
      <style>{`
        .card {
          background: var(--bg-2);
          border: 1px solid var(--border);
          border-radius: 3px;
          padding: 12px 14px;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .card .display {
          font-size: var(--fs-md);
          color: var(--fg-0);
        }
        .card .bio {
          font-size: var(--fs-sm);
          color: var(--fg-1);
          margin: 0;
          line-height: 1.45;
        }
        .card .caps-head { margin-top: 4px; }
        .card .caps {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .card .fp {
          margin-top: 4px;
          font-size: 10.5px;
          color: var(--fg-2);
          word-break: break-all;
        }
      `}</style>
    </div>
  );
}

function CapabilityRow(props: {
  p: Persona;
  c: PersonaCapability;
  onChanged: () => void;
}) {
  const [busy, setBusy] = createSignal(false);
  async function setStatus(status: CapabilityStatus) {
    setBusy(true);
    try {
      await api.upsertPersonaCapability(props.p.name, props.c.key, {
        status,
        desired_value: props.c.desired_value ?? undefined,
        actual_value: props.c.actual_value ?? undefined,
        credential_ref: props.c.credential_ref ?? undefined,
        notes: props.c.notes ?? undefined,
      });
      props.onChanged();
    } catch (e) {
      window.alert(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div class="cap-row" classList={{ [`status-${props.c.status}`]: true }}>
      <div class="row" style={{ gap: "6px", "align-items": "center" }}>
        <span class={`tag ${statusTag(props.c.status)}`}>
          {props.c.status}
        </span>
        <span class="key mono">{props.c.key}</span>
      </div>
      <div class="vals">
        <Show when={props.c.actual_value} fallback={
          <Show when={props.c.desired_value}>
            <span class="dim" style={{ "font-size": "var(--fs-xs)" }}>
              wants: <span class="mono">{props.c.desired_value}</span>
            </span>
          </Show>
        }>
          <span style={{ "font-size": "var(--fs-xs)" }}>
            <span class="mono">{props.c.actual_value}</span>
          </span>
        </Show>
        <Show when={props.c.credential_ref}>
          <span class="dim" style={{ "font-size": "var(--fs-xs)" }}>
            cred: <span class="mono">{props.c.credential_ref}</span>
          </span>
        </Show>
      </div>
      <Show when={props.c.notes}>
        <div class="notes">{props.c.notes}</div>
      </Show>
      <div class="row controls">
        <For each={["pending", "registered", "verified"] as CapabilityStatus[]}>
          {(s) => (
            <button
              class="ghost"
              classList={{ active: props.c.status === s }}
              disabled={busy()}
              onClick={() => void setStatus(s)}
            >
              {s}
            </button>
          )}
        </For>
      </div>
      <style>{`
        .cap-row {
          background: var(--bg-1);
          border: 1px solid var(--border);
          border-radius: 3px;
          padding: 6px 8px;
          display: flex;
          flex-direction: column;
          gap: 3px;
        }
        .cap-row.status-pending    { border-left: 2px solid var(--fg-2); }
        .cap-row.status-registered { border-left: 2px solid var(--cobalt); }
        .cap-row.status-verified   { border-left: 2px solid var(--teal); }
        .cap-row .key { font-size: var(--fs-sm); color: var(--fg-0); }
        .cap-row .vals { display: flex; gap: 8px; flex-wrap: wrap; }
        .cap-row .notes {
          font-size: var(--fs-xs);
          color: var(--fg-1);
          line-height: 1.45;
        }
        .cap-row .controls {
          gap: 4px;
          margin-top: 2px;
        }
        .cap-row .controls button {
          font-size: var(--fs-xs);
          padding: 1px 6px;
        }
        .cap-row .controls button.active {
          background: var(--bg-3);
          color: var(--fg-0);
        }
      `}</style>
    </div>
  );
}

function statusTag(s: CapabilityStatus): string {
  return { pending: "graphite", registered: "cobalt", verified: "teal" }[s];
}
