"""Computer-use via headed Playwright Chromium.

Imported eagerly so its builtin tool (`cm_browser`) registers at startup
and is in every emergent gap's default loadout.

Design notes — see ``architecture-notes`` (forthcoming) for the long
version. The short one:

  * One headed Chromium window per *profile name*. Profiles persist on
    disk under ``~/.config/drone-graph/browser-profiles/<name>/`` so a
    drone that logged into LinkedIn yesterday stays logged in today.
  * Drones declare which profile they want via the tool's ``profile``
    argument. When a drone has successfully authenticated a profile, it
    calls ``cm_register_tool`` to advertise the capability so future
    drones can ``cm_request_tool`` it.
  * Concurrency is bounded via the signals sidecar. Default 4 browser
    windows at once (configurable in Settings). A drone that needs a
    browser waits for a slot instead of doing other work.
  * ``await_operator`` is the key UX primitive: the drone stages an
    action (form filled, page loaded, OAuth challenge ready) and pauses
    until the operator either drives via the live browser window
    themselves, or types a message into the drone-attached chat panel.
  * Browsers are reaped when their owning drone subprocess exits. The
    scheduler checks for orphans on each tick.
"""

from drone_graph.tools.builtins.browser import tool  # noqa: F401 — eager register
