"""Backend factory for Hive and Federation storage (PostgreSQL only).

Callers use the factory functions to get a backend instance. SQLite backends
were removed in ADR-007 — ``create_hive_backend`` / ``create_federation_backend``
require a ``postgres://`` or ``postgresql://`` DSN.

EPIC-055 — pluggable storage backends.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tapps_brain._protocols import AgentRegistryBackend, FederationBackend, HiveBackend

from tapps_brain.hive import AgentRegistration, AgentRegistry

# ---------------------------------------------------------------------------
# SQLite Agent Registry Backend (YAML file — not a SQLite database)
# ---------------------------------------------------------------------------


class SqliteAgentRegistryBackend:
    """File-backed :class:`AgentRegistryBackend` (agents.yaml).

    Despite the historical name, this does **not** use SQLite — only a YAML
    registry file. Prefer :func:`create_agent_registry_backend` for selection.
    """

    def __init__(self, registry_path: Path | None = None) -> None:
        self._registry = AgentRegistry(registry_path=registry_path)

    def register(self, agent: AgentRegistration) -> None:
        self._registry.register(agent)

    def unregister(self, agent_id: str) -> bool:
        return self._registry.unregister(agent_id)

    def get(self, agent_id: str) -> AgentRegistration | None:
        return self._registry.get(agent_id)

    def list_agents(self) -> list[Any]:
        return self._registry.list_agents()

    def agents_for_domain(self, domain_name: str) -> list[Any]:
        return self._registry.agents_for_domain(domain_name)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def create_hive_backend(
    dsn_or_path: str | None = None,
    *,
    encryption_key: str | None = None,
) -> HiveBackend:
    """Create a :class:`PostgresHiveBackend`.

    A **PostgreSQL** DSN is required. SQLite Hive backends were removed (ADR-007).

    ``encryption_key`` is accepted for API compatibility with older callers but
    is ignored — Postgres does not use SQLCipher.
    """
    _ = encryption_key  # SQLCipher was SQLite-only; keep parameter for call-site compat
    if dsn_or_path is None or not str(dsn_or_path).strip():
        msg = (
            "create_hive_backend() requires TAPPS_BRAIN_HIVE_DSN "
            "(postgres:// or postgresql://). SQLite backends are removed (ADR-007)."
        )
        raise ValueError(msg)
    if not str(dsn_or_path).startswith(("postgres://", "postgresql://")):
        msg = (
            "Hive backend requires a PostgreSQL DSN. SQLite backends are removed (ADR-007)."
        )
        raise ValueError(msg)
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_hive import PostgresHiveBackend

    cm = PostgresConnectionManager(dsn_or_path)
    return PostgresHiveBackend(cm)


def create_federation_backend(dsn_or_path: str | None = None) -> FederationBackend:
    """Create a :class:`PostgresFederationBackend`.

    A **PostgreSQL** DSN is required. SQLite Federation backends were removed (ADR-007).
    """
    if dsn_or_path is None or not str(dsn_or_path).strip():
        msg = (
            "create_federation_backend() requires TAPPS_BRAIN_FEDERATION_DSN "
            "(postgres:// or postgresql://). SQLite backends are removed (ADR-007)."
        )
        raise ValueError(msg)
    if not str(dsn_or_path).startswith(("postgres://", "postgresql://")):
        msg = (
            "Federation backend requires a PostgreSQL DSN. "
            "SQLite backends are removed (ADR-007)."
        )
        raise ValueError(msg)
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_federation import PostgresFederationBackend

    cm = PostgresConnectionManager(dsn_or_path)
    return PostgresFederationBackend(cm)


def resolve_hive_backend_from_env(
    *,
    encryption_key: str | None = None,
) -> HiveBackend | None:
    """Return a Postgres :class:`HiveBackend` from ``TAPPS_BRAIN_HIVE_DSN``, or ``None``.

    When the env var is unset or empty, returns ``None`` (no Hive). Never opens
    a SQLite Hive (ADR-007).
    """
    import os

    dsn = (os.environ.get("TAPPS_BRAIN_HIVE_DSN") or "").strip()
    if not dsn:
        return None
    return create_hive_backend(dsn, encryption_key=encryption_key)


def create_agent_registry_backend(
    registry_path: str | None = None,
) -> AgentRegistryBackend:
    """Create an :class:`AgentRegistryBackend`.

    - ``None`` or a file path -> :class:`SqliteAgentRegistryBackend` (YAML file)
    - ``postgres://...`` or ``postgresql://...`` -> :class:`PostgresAgentRegistry` (EPIC-055)
    """
    if registry_path is not None and registry_path.startswith(("postgres://", "postgresql://")):
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.postgres_hive import PostgresAgentRegistry

        cm = PostgresConnectionManager(registry_path)
        return PostgresAgentRegistry(cm)
    path = Path(registry_path) if registry_path else None
    return SqliteAgentRegistryBackend(registry_path=path)
