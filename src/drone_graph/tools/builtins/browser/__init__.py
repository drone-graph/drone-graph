"""Real Chrome browser lane via CDP.

Imported eagerly so its builtin tools (`cm_browser`, `cm_check_browser`)
register at startup and are in every emergent gap's default loadout.

Architecture:
  * A single real Chrome instance is launched with the user's chosen profile
    directory via ``subprocess.Popen`` and ``--remote-debugging-port``.
  * Playwright connects over CDP (Chrome DevTools Protocol), avoiding
    the ``--enable-automation`` flag that Google sites detect.
  * Each drone gets a persistent tab (reused across tool calls). Drones
    interact only with their own tab — no cross-tab visibility.
  * The Chrome profile directory is selected by the operator during
    onboarding (folder picker in Settings) and stored in ``settings.json``
    under ``chrome_profile_dir``. **Never** exposed to the AI.
  * The Chrome profile lives permanently at ``<project-root>/chrome-data/``.
    Chrome reads and writes to this directory directly — **no copying** on
    every run.  Chrome manages its own session state (cookies, Local Storage,
    etc.) inside this directory across runs, exactly as it does for a real user.
  * ``await_operator`` is the key UX primitive: the drone stages an
    action (form filled, page loaded, OAuth challenge ready) and pauses
    until the operator either drives via the live browser window
    themselves, or types a message into the drone-attached chat panel.
  * ``cm_check_browser`` returns true/false — zero path leakage.
  * The Chrome process is reaped when drone-graph exits or on explicit stop.
"""

from drone_graph.tools.builtins.browser import authenticated  # noqa: F401 — eager register
