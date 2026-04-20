"""Singleton ProfileResolver builder for the HTTP adapter (TAP-604).

Extracted from ``tapps_brain.http_adapter``.

The resolver is built lazily on the first ``/mcp`` request and cached for
the lifetime of the process.  Thread-safety is provided by
``_PROFILE_RESOLVER_LOCK``.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# STORY-073.2: process-wide ProfileResolver singleton.  Built once on first
# /mcp request; guarded by _PROFILE_RESOLVER_LOCK.
_PROFILE_RESOLVER: Any = None
_PROFILE_RESOLVER_LOCK: threading.Lock = threading.Lock()


def _get_profile_resolver() -> Any:
    """Return the process-wide :class:`~tapps_brain.mcp_server.profile_resolver.ProfileResolver`.

    Built lazily on first call; subsequent calls return the cached singleton.
    Thread-safe via ``_PROFILE_RESOLVER_LOCK``.

    The resolver is initialised with:

    * The bundled :class:`~tapps_brain.mcp_server.profile_registry.ProfileRegistry`.
    * An optional agent-profile getter backed by ``TAPPS_BRAIN_HIVE_DSN`` or
      ``TAPPS_BRAIN_DATABASE_URL`` when a Postgres DSN is configured.
    * The ``TAPPS_BRAIN_DEFAULT_PROFILE`` env var (default ``"full"``).
    """
    global _PROFILE_RESOLVER
    if _PROFILE_RESOLVER is not None:
        return _PROFILE_RESOLVER
    with _PROFILE_RESOLVER_LOCK:
        if _PROFILE_RESOLVER is not None:
            return _PROFILE_RESOLVER
        from tapps_brain.mcp_server.profile_registry import ProfileRegistry
        from tapps_brain.mcp_server.profile_resolver import ProfileResolver

        registry = ProfileRegistry()

        # Build an agent-profile getter if a Postgres DSN is available.
        # Import get_settings lazily through http_adapter to respect test patches.
        getter = None
        import tapps_brain.http_adapter as _http_mod

        dsn = _http_mod.get_settings().dsn or os.environ.get("TAPPS_BRAIN_HIVE_DSN", "").strip()
        if dsn and (dsn.startswith("postgres://") or dsn.startswith("postgresql://")):
            try:
                from tapps_brain.postgres_connection import PostgresConnectionManager
                from tapps_brain.postgres_hive import PostgresAgentRegistry

                _cm = PostgresConnectionManager(dsn)
                _pg_agent_reg = PostgresAgentRegistry(_cm)

                def _pg_getter(project_id: str, agent_id: str) -> str | None:
                    row = _pg_agent_reg.get(agent_id)
                    if row is None:
                        return None
                    return str(row.get("profile") or "") or None

                getter = _pg_getter
            except Exception as exc:
                logger.warning(
                    "http_adapter.profile_resolver.agent_registry_unavailable",
                    error=str(exc),
                    detail=(
                        "Agent-registry lookup disabled for profile resolution. "
                        "Profile will fall back to header or server default."
                    ),
                )

        _PROFILE_RESOLVER = ProfileResolver(registry, agent_profile_getter=getter)
        return _PROFILE_RESOLVER
