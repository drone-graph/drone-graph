import {
  Show,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
} from "solid-js";

import { ActionBanner } from "./components/ActionBanner";
import { ActiveDronesRail } from "./components/ActiveDronesRail";
import { ChatRail } from "./components/ChatRail";
import { EventDrawer } from "./components/EventDrawer";
import { FindingsGraph } from "./components/FindingsGraph";
import { GapDetailOverlay } from "./components/GapDetail";
import { Internals } from "./components/Internals";
import { Marketplace } from "./components/Marketplace";
import { OnboardingBudget } from "./components/OnboardingBudget";
import { OnboardingIdentity } from "./components/OnboardingIdentity";
import { OnboardingKey } from "./components/OnboardingKey";
import { OnboardingSeed } from "./components/OnboardingSeed";
import { ParanoidModal } from "./components/ParanoidModal";
import { RestartBanner } from "./components/RestartBanner";
import { Settings } from "./components/Settings";
import { SubstrateCanvas } from "./components/SubstrateCanvas";
import { TopBar } from "./components/TopBar";
import { WorkerFocus } from "./components/WorkerFocus";
import { setAmbientActivity, unlockAudio } from "./sound";
import { EventStream } from "./sse";
import {
  ingestEvent,
  isUnconfigured,
  loadSnapshot,
  setConnected,
  startVitalsPolling,
  store,
} from "./state";

const LAYOUT_CSS = `
.app {
  position: relative;
  height: 100%;
  background: var(--bg-0);
  isolation: isolate;
  overflow: hidden;
}

.dashboard {
  position: absolute;
  inset: 0;
  display: grid;
  grid-template-rows: var(--topbar-h) 1fr;
  opacity: 0;
  transition: opacity 1600ms var(--ease);
  pointer-events: none;
}
.dashboard.revealed {
  opacity: 1;
  pointer-events: auto;
}

.boot-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 80px 24px;
  gap: 16px;
  height: 100%;
}
.boot-error h1 {
  font-size: var(--fs-lg);
  font-weight: 500;
  color: var(--fg-0);
  margin: 0;
}

.console-layout {
  display: grid;
  grid-template-columns: var(--rail-w-left) 1fr var(--rail-w-right);
  /* Explicit row track so child rails inherit a defined height and their
   * inner overflow-y: auto scrollers actually have something to scroll
   * within. Without this the implicit row is auto (content-sized) and the
   * rails grow to fit content instead of clipping it. */
  grid-template-rows: 1fr;
  position: relative;
  z-index: 1;
  min-height: 0;
  /* Reserve space for the bottom event drawer so the rails don't sit under it. */
  padding-bottom: var(--drawer-h);
  overflow: hidden;
}

.center-stack {
  position: relative;
  display: flex;
  flex-direction: column;
  background: var(--bg-0);
  border-left: 1px solid var(--border);
  border-right: 1px solid var(--border);
  overflow: hidden;
}
`;

export function App() {
  const [bootError, setBootError] = createSignal<string | null>(null);
  // Drives the dashboard fade-in once the operator seeds the swarm. Starts
  // false; becomes true the first time isEmpty() flips from true → false.
  const [dashboardRevealed, setDashboardRevealed] = createSignal(false);

  onMount(async () => {
    try {
      await loadSnapshot();
    } catch (e: unknown) {
      setBootError(e instanceof Error ? e.message : String(e));
      return;
    }
    const stream = new EventStream();
    const offEv = stream.onEvent(ingestEvent);
    const offConn = stream.onConnection(setConnected);
    stream.start();
    const stopPoll = startVitalsPolling();
    onCleanup(() => {
      offEv();
      offConn();
      stream.stop();
      stopPoll();
    });

    const onClickOnce = () => {
      unlockAudio();
      window.removeEventListener("click", onClickOnce);
    };
    window.addEventListener("click", onClickOnce, { once: true });
  });

  // Track active-drone count → ambient hum amplitude.
  createEffect(() => {
    setAmbientActivity(store.active_drones.length);
  });

  const isEmpty = createMemo(() => {
    // True when the substrate has nothing but the two preset gaps and no
    // findings of substance yet.
    const emergent = store.gaps.filter((g) => !g.preset_kind);
    const hasUserPrompt = store.recent_findings.some(
      (f) => f.author === "user" && f.kind === "user_input",
    );
    return emergent.length === 0 && !hasUserPrompt;
  });

  // Fade the dashboard in once there's real work; never fade back.
  createEffect(() => {
    if (!isUnconfigured() && !isEmpty()) {
      setDashboardRevealed(true);
    }
  });

  // ---- View routing ------------------------------------------------------
  // Onboarding (fullscreen, no chrome):
  //   - isUnconfigured       → OnboardingKey
  //   - keys set + isEmpty   → OnboardingSeed
  // Dashboard (TopBar + console / marketplace / internals / settings):
  //   - everything else
  //
  // The Settings/marketplace/internals views remain reachable post-onboarding
  // via the TopBar nav. We intentionally do NOT auto-route to Settings during
  // onboarding — OnboardingKey is the simpler stark surface for first-time
  // key entry; the full Settings panel is for tuning post-onboarding.

  return (
    <div class="app">
      <Show
        when={bootError() === null}
        fallback={
          <div class="boot-error">
            <h1>Mission control couldn't reach the substrate.</h1>
            <p class="dim">{bootError()}</p>
            <p class="faint">
              Make sure Neo4j is up (<span class="mono">docker compose up -d neo4j</span>)
              and the API is running (<span class="mono">drone-graph serve</span>).
            </p>
          </div>
        }
      >
        <Show when={store.loaded}>
          {/* Dashboard chrome — always mounted but hidden until revealed so
              the canvas can animate in cleanly when OnboardingSeed dissolves. */}
          <div
            class="dashboard"
            classList={{ revealed: dashboardRevealed() }}
            aria-hidden={!dashboardRevealed()}
          >
            <TopBar />
            <Show
              when={store.view === "console"}
              fallback={
                store.view === "findings" ? <FindingsGraph />
                : store.view === "marketplace" ? <Marketplace />
                : store.view === "internals" ? <Internals />
                : <Settings />
              }
            >
              <ConsoleLayout />
            </Show>
          </div>

          {/* Onboarding overlays sit above the dashboard until done.
              Sequenced: key → budget → seed. Each gate stays open until
              its precondition is satisfied. */}
          <Show when={isUnconfigured()}>
            <OnboardingKey />
          </Show>
          <Show
            when={
              !isUnconfigured() &&
              !store.settings?.cost_ceiling_acknowledged &&
              isEmpty()
            }
          >
            <OnboardingBudget />
          </Show>
          <Show
            when={
              !isUnconfigured() &&
              store.settings?.cost_ceiling_acknowledged &&
              !store.settings?.identity_acknowledged &&
              isEmpty()
            }
          >
            <OnboardingIdentity />
          </Show>
          <Show
            when={
              !isUnconfigured() &&
              store.settings?.cost_ceiling_acknowledged &&
              store.settings?.identity_acknowledged &&
              isEmpty()
            }
          >
            <OnboardingSeed />
          </Show>
        </Show>
        <ParanoidModal />
        <Show when={dashboardRevealed()}>
          <ActionBanner />
          <RestartBanner />
        </Show>
      </Show>
      <style>{LAYOUT_CSS}</style>
    </div>
  );
}

function ConsoleLayout() {
  return (
    <>
      <div class="console-layout">
        <ChatRail />
        <div class="center-stack">
          <SubstrateCanvas />
          <WorkerFocus />
        </div>
        <ActiveDronesRail />
      </div>
      <EventDrawer />
      <GapDetailOverlay />
    </>
  );
}
