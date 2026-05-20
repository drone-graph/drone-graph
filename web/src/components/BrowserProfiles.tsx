/**
 * Browser Profiles — currently unused. The real-Chrome cm_browser tool
 * handles all browser interactions; no separate manual-launch endpoint exists.
 */
export function BrowserProfiles() {
  return (
    <div class="browser-launcher">
      <p class="muted">
        Browser profiles are managed automatically by the
        real-Chrome <code>cm_browser</code> tool.
      </p>
    </div>
  );
}
