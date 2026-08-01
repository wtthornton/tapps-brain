"""Leaf factory for opening a ``MemoryStore`` for MCP server use.

Lives outside :mod:`server` / :mod:`context` so both can call it without a
circular import.  Package-level monkeypatches on
``tapps_brain.mcp_server._get_store`` remain supported via
:func:`tapps_brain.mcp_server._pkg_attr.pkg_attr`.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def _get_store(
    project_dir: Path,
    *,
    enable_hive: bool = True,
    agent_id: str = "unknown",
) -> Any:  # noqa: ANN401
    """Open a MemoryStore for the given project directory.

    When *enable_hive* is ``True``, a Postgres :class:`HiveBackend` is
    wired in when ``TAPPS_BRAIN_HIVE_DSN`` is set (ADR-007 — no SQLite
    Hive).

    **Strict mode** (``TAPPS_BRAIN_STRICT=1``): When this env var is set,
    startup **fails immediately** if ``TAPPS_BRAIN_HIVE_DSN`` is not
    configured.  This prevents silent degradation in production where a
    missing DSN would quietly disable Hive tools.
    """
    from tapps_brain.backends import resolve_hive_backend_from_env
    from tapps_brain.store import MemoryStore

    strict = os.environ.get("TAPPS_BRAIN_STRICT", "") == "1"

    hive_store = None
    if enable_hive:
        hive_store = resolve_hive_backend_from_env()
        if strict and hive_store is None:
            raise RuntimeError(
                "TAPPS_BRAIN_STRICT=1 requires TAPPS_BRAIN_HIVE_DSN to be set (postgresql://...)"
            )

    agent_id_for_store = agent_id if agent_id != "unknown" else None
    return MemoryStore(
        project_dir,
        agent_id=agent_id_for_store,
        hive_store=hive_store,
        hive_agent_id=agent_id,
    )
