"""Importing this package triggers ``@register_tool`` for every builtin.

Order doesn't matter; the registry is keyed by name. Listing them explicitly
here keeps the surface visible at a glance.
"""

from drone_graph.tools.builtins import (
    alignment,
    concurrency,
    queries,
    structural,
    worker,
)

__all__ = ["alignment", "concurrency", "queries", "structural", "worker"]
