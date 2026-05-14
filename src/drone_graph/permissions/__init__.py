"""Synchronous permission prompts.

Drones run with full access to the operator's machine and accounts. The
operator's permission tier governs how loudly the swarm asks before acting:

  * ``open``           — never prompts
  * ``ask_external``   — prompts before externally-visible actions
                         (cm_browser, cm_install_package)
  * ``ask_everything`` — prompts before any local-effect or external action

The mechanism is purely synchronous: the tool dispatcher writes a row to
``signals.db`` (status='pending'), polls until the row flips to
``granted`` / ``denied``, then either runs the tool or returns a denial
string to the model. The UI consumes pending rows over SSE and POSTs the
resolution.
"""

from drone_graph.permissions.gate import (
    PermissionDecision,
    check_or_wait,
    tool_category,
    tier_requires_prompt,
)

__all__ = [
    "PermissionDecision",
    "check_or_wait",
    "tier_requires_prompt",
    "tool_category",
]
