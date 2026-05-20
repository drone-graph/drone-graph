"""Real Chrome browser lane — tools + launcher using the user's Chrome profile.

Imported eagerly (via ``browser/__init__.py``) so ``cm_browser``
and ``cm_check_browser`` register at startup and are available in every
emergent gap's default loadout.
"""

from drone_graph.tools.builtins.browser.authenticated.tool import (  # noqa: F401
    cm_browser,
)
from drone_graph.tools.builtins.browser.authenticated.status_tool import (  # noqa: F401
    cm_check_browser,
)
from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (  # noqa: F401
    AuthenticatedChrome,
)
from drone_graph.tools.builtins.browser.authenticated.config import (  # noqa: F401
    AuthenticatedConfig,
    load_config,
    save_config,
)
