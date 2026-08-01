"""Resolve monkeypatched attrs on ``tapps_brain.mcp_server`` without re-importing it.

``__init__`` eagerly imports :mod:`context` and :mod:`server`. Those modules
previously did ``import tapps_brain.mcp_server`` so tests could monkeypatch
``ms._get_store`` / ``ms.create_server``. That package re-import is a static
cycle (``mcp_server → context/server → mcp_server``).

Looking up the already-loaded package via :data:`sys.modules` preserves the
monkeypatch contract without creating an import edge back into the package.
"""

from __future__ import annotations

import sys
from typing import Any

_PKG_NAME = "tapps_brain.mcp_server"


def pkg_attr(name: str, default: Any) -> Any:  # noqa: ANN401
    """Return ``tapps_brain.mcp_server.<name>`` when the package is loaded.

    Falls back to *default* when the package is not in :data:`sys.modules` or
    the attribute is missing (e.g. early import / direct submodule use).
    """
    pkg = sys.modules.get(_PKG_NAME)
    if pkg is None:
        return default
    return getattr(pkg, name, default)
