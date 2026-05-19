import { BrowserProfiles } from "./BrowserProfiles";

/**
 * Standalone browser view, reachable from the top-bar nav.
 *
 * Lets the operator launch a headed Chrome session (with all the anti-bot
 * countermeasures that drones use) and manually sign into services.
 */
export function BrowserView() {
  return (
    <div class="browser-view">
      <header class="browser-view-header">
        <h2>Browser</h2>
        <p class="dim">
          Launch Chrome to manually sign into services. The browser uses the same
          anti-detection measures as drones (real Chrome channel, stealth patches).
          Close the window when done — sessions are persisted for later reuse.
        </p>
      </header>

      <BrowserProfiles />

      <style>{`
        .browser-view {
          padding: 24px 32px;
          max-width: 1100px;
          margin: 0 auto;
          overflow-y: auto;
          height: 100%;
        }
        .browser-view-header {
          margin-bottom: 20px;
        }
        .browser-view-header h2 {
          margin: 0 0 6px;
          font-size: var(--fs-md);
          font-weight: 500;
          letter-spacing: 0.02em;
        }
        .browser-view-header p {
          margin: 0;
          font-size: var(--fs-sm);
          line-height: 1.4;
        }
      `}</style>
    </div>
  );
}
