"""Authenticated Chrome profile lane — tools + launcher for Gmail/Google
sessions.

Imported eagerly (via ``browser/__init__.py``) so ``cm_authenticated_browser``
and ``cm_check_auth_profile`` register at startup and are available in every
emergent gap's default loadout alongside the existing ``cm_browser``.
"""

from drone_graph.tools.builtins.browser.authenticated.tool import (  # noqa: F401
    cm_authenticated_browser,
)
from drone_graph.tools.builtins.browser.authenticated.status_tool import (  # noqa: F401
    cm_check_auth_profile,
)
from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (  # noqa: F401
    AuthenticatedChrome,
)
from drone_graph.tools.builtins.browser.authenticated.config import (  # noqa: F401
    AuthenticatedConfig,
    load_config,
    save_config,
)
