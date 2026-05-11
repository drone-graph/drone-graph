import { For, Show, createSignal } from "solid-js";

import { api } from "../api";
import { refreshInbox, selectGap, setView, store } from "../state";
import type { InboxActionType, InboxItem } from "../types";

/** Compact rail-strip badge + popover. Used in the top bar. */
export function InboxBadge() {
  const [open, setOpen] = createSignal(false);
  return (
    <div class="inbox-badge">
      <button
        class="ghost"
        onClick={() => setOpen((v) => !v)}
        classList={{ active: store.inbox.length > 0 }}
        title={`${store.inbox.length} action${store.inbox.length === 1 ? "" : "s"} needed`}
        style={{ "min-width": "auto", padding: "4px 8px" }}
      >
        ✉
        <Show when={store.inbox.length > 0}>
          <span class="count">{store.inbox.length}</span>
        </Show>
      </button>
      <Show when={open()}>
        <InboxPopover close={() => setOpen(false)} />
      </Show>
      <style>{`
        .inbox-badge { position: relative; }
        .inbox-badge .count {
          margin-left: 4px;
          color: var(--amber);
          font-weight: 600;
        }
        .inbox-badge button.active { color: var(--amber); }
      `}</style>
    </div>
  );
}

function InboxPopover(props: { close: () => void }) {
  return (
    <div class="popover" onClick={(e) => e.stopPropagation()}>
      <div class="head row" style={{ "justify-content": "space-between" }}>
        <span class="dim" style={{ "font-size": "var(--fs-xs)", "letter-spacing": "0.08em" }}>
          ACTION INBOX
        </span>
        <button class="ghost" onClick={props.close} style={{ padding: "2px 6px" }}>
          ✕
        </button>
      </div>
      <Show
        when={store.inbox.length > 0}
        fallback={
          <div class="empty faint">
            no pending action items. when a drone needs a credential,
            sign-in, MFA code, or purchase approval, it lands here.
          </div>
        }
      >
        <div class="list">
          <For each={store.inbox}>
            {(item) => <InboxRow item={item} close={props.close} />}
          </For>
        </div>
      </Show>
      <style>{`
        .popover {
          position: absolute;
          top: calc(100% + 6px);
          right: 0;
          background: var(--bg-1);
          border: 1px solid var(--border-strong);
          border-radius: 4px;
          width: 380px;
          max-height: 60vh;
          overflow: hidden;
          z-index: 20;
          box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
          display: flex;
          flex-direction: column;
        }
        .popover .head {
          padding: 8px 12px;
          border-bottom: 1px solid var(--border);
        }
        .popover .empty {
          padding: 26px 16px;
          text-align: center;
          font-size: var(--fs-sm);
          line-height: 1.6;
        }
        .popover .list {
          flex: 1;
          overflow-y: auto;
          padding: 6px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
      `}</style>
    </div>
  );
}

function InboxRow(props: { item: InboxItem; close: () => void }) {
  const [showDetail, setShowDetail] = createSignal(false);
  const [busy, setBusy] = createSignal(false);

  async function resolve(
    outcome:
      | "resolved"
      | "try_another_way"
      | "dont_do_this"
      | "not_right_now",
  ) {
    const promptLabels: Record<typeof outcome, string> = {
      resolved: "What did you do? (optional)",
      try_another_way: "Why this route isn't workable — so GF can pick another (optional)",
      dont_do_this: "Why you're rejecting the goal (optional)",
      not_right_now: "When to come back to this, or context (optional)",
    } as const;
    const defaultLabels: Record<typeof outcome, string> = {
      resolved: "completed externally",
      try_another_way: "",
      dont_do_this: "",
      not_right_now: "",
    } as const;
    const note = window.prompt(promptLabels[outcome], defaultLabels[outcome]);
    if (note === null) return;
    setBusy(true);
    try {
      await api.resolveInbox(props.item.finding_id, { outcome, note });
      await refreshInbox();
    } catch (e) {
      window.alert(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function grantIdentity() {
    const gapId = String(props.item.details?.gap_id ?? props.item.affected_gap_ids[0] ?? "");
    if (!gapId) return;
    const note = window.prompt(
      "Approve operator-identity for this gap? Add an optional note for the audit log:",
      "",
    );
    if (note === null) return;
    setBusy(true);
    try {
      await api.grantIdentity(gapId, note);
      await refreshInbox();
    } catch (e) {
      window.alert(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function denyIdentity() {
    const gapId = String(props.item.details?.gap_id ?? props.item.affected_gap_ids[0] ?? "");
    if (!gapId) return;
    const reason = window.prompt(
      "Reason for denying operator identity? The drone will run in clean mode and GF will see this so it doesn't keep re-asking.",
      "not necessary",
    );
    if (reason === null) return;
    setBusy(true);
    try {
      await api.denyIdentity(gapId, reason);
      await refreshInbox();
    } catch (e) {
      window.alert(String(e));
    } finally {
      setBusy(false);
    }
  }

  function inspect() {
    if (props.item.affected_gap_ids[0]) {
      selectGap(props.item.affected_gap_ids[0]);
      setView("console");
      props.close();
    }
  }

  return (
    <div class="row-card">
      <div class="row" style={{ "justify-content": "space-between" }}>
        <span class={`tag ${typeTag(props.item.action_type)}`}>
          {props.item.action_type}
        </span>
        <span class="faint" style={{ "font-size": "var(--fs-xs)" }}>
          tick {props.item.tick}
        </span>
      </div>
      <div class="summary">{props.item.summary}</div>
      <Show when={extractURL(props.item.details)}>
        <a
          class="link mono"
          href={extractURL(props.item.details)!}
          target="_blank"
          rel="noopener noreferrer"
        >
          {extractURL(props.item.details)}
        </a>
      </Show>
      <Show when={extractAmount(props.item.details)}>
        <div class="amount">
          proposed spend: <span class="copper">${extractAmount(props.item.details)}</span>
        </div>
      </Show>
      <Show when={showDetail()}>
        <pre class="detail">{JSON.stringify(props.item.details, null, 2)}</pre>
      </Show>
      <div class="row actions">
        <button
          class="ghost"
          onClick={() => setShowDetail(!showDetail())}
          style={{ "font-size": "var(--fs-xs)" }}
        >
          {showDetail() ? "hide" : "details"}
        </button>
        <Show when={props.item.affected_gap_ids.length > 0}>
          <button
            class="ghost"
            onClick={inspect}
            style={{ "font-size": "var(--fs-xs)" }}
          >
            inspect gap
          </button>
        </Show>
        <span style={{ flex: "1" }} />
        <Show
          when={props.item.action_type === "identity"}
          fallback={
            <>
              <button
                class="ghost"
                disabled={busy()}
                onClick={() => void resolve("dont_do_this")}
                style={{ "font-size": "var(--fs-xs)" }}
                title="reject the goal — gap will be retired"
              >
                don't do this
              </button>
              <button
                class="ghost"
                disabled={busy()}
                onClick={() => void resolve("not_right_now")}
                style={{ "font-size": "var(--fs-xs)" }}
                title="pause the gap — resume later from the gap detail"
              >
                not right now
              </button>
              <button
                class="ghost"
                disabled={busy()}
                onClick={() => void resolve("try_another_way")}
                style={{ "font-size": "var(--fs-xs)" }}
                title="keep the goal, reject this means — GF decomposes around it"
              >
                try another way
              </button>
              <button
                class="primary"
                disabled={busy()}
                onClick={() => void resolve("resolved")}
                style={{ "font-size": "var(--fs-xs)", padding: "3px 10px" }}
              >
                mark done
              </button>
            </>
          }
        >
          <button
            class="ghost"
            disabled={busy()}
            onClick={() => void denyIdentity()}
            style={{ "font-size": "var(--fs-xs)" }}
            title="run in isolated mode anyway"
          >
            keep isolated
          </button>
          <button
            class="primary"
            disabled={busy()}
            onClick={() => void grantIdentity()}
            style={{ "font-size": "var(--fs-xs)", padding: "3px 10px" }}
            title="let this drone use your real identity"
          >
            approve
          </button>
        </Show>
      </div>
      <style>{`
        .row-card {
          background: var(--bg-2);
          border: 1px solid var(--border);
          border-radius: 3px;
          padding: 8px 10px;
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .row-card .summary {
          font-size: var(--fs-sm);
          color: var(--fg-0);
          line-height: 1.5;
        }
        .row-card .amount {
          color: var(--fg-1);
          font-size: var(--fs-sm);
        }
        .row-card .link {
          font-size: var(--fs-xs);
          color: var(--cobalt-soft);
          text-decoration: underline;
          word-break: break-all;
        }
        .row-card .detail {
          background: var(--bg-0);
          border: 1px solid var(--border);
          padding: 6px 8px;
          font-size: 10.5px;
          line-height: 1.4;
          margin: 4px 0 0;
          max-height: 160px;
          overflow: auto;
        }
        .row-card .actions { margin-top: 4px; }
      `}</style>
    </div>
  );
}

function typeTag(t: InboxActionType): string {
  return {
    credential: "cobalt",
    oauth: "cobalt",
    sign_in: "cobalt",
    purchase: "copper",
    approval: "amber",
    mfa: "amber",
    identity: "amber",
    other: "graphite",
  }[t];
}

function extractURL(details: Record<string, unknown>): string | null {
  const url = details["url"];
  return typeof url === "string" ? url : null;
}

function extractAmount(details: Record<string, unknown>): string | null {
  const a = details["amount_usd"] ?? details["amount"];
  return typeof a === "number" ? a.toFixed(2) : null;
}
