"""Backend factory and SQLite adapters for Hive and Federation storage.

Callers use the factory functions to get a backend instance.  The factory
inspects the DSN string and returns the appropriate backend.

EPIC-055 — pluggable storage backends.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tapps_brain._protocols import AgentRegistryBackend, FederationBackend, HiveBackend
    from tapps_brain.hive import AgentRegistration

# ---------------------------------------------------------------------------
# SQLite Hive Backend
# ---------------------------------------------------------------------------


class SqliteHiveBackend:
    """SQLite-backed :class:`HiveBackend` — delegates to existing :class:`HiveStore`."""

    def __init__(self, db_path: Path | None = None, *, encryption_key: str | None = None) -> None:
        from tapps_brain.hive import HiveStore

        self._store = HiveStore(db_path=db_path, encryption_key=encryption_key)
        # Expose _db_path so the protocol attribute is satisfied.
        self._db_path: Path = self._store._db_path

    # -- CRUD / search -------------------------------------------------------

    def save(
        self,
        *,
        key: str,
        value: str,
        namespace: str = "universal",
        source_agent: str = "unknown",
        tier: str = "pattern",
        confidence: float = 0.6,
        source: str = "agent",
        tags: list[str] | None = None,
        valid_at: str | None = None,
        invalid_at: str | None = None,
        superseded_by: str | None = None,
        conflict_policy: str = "supersede",
        memory_group: str | None = None,
    ) -> dict[str, Any] | None:
        return self._store.save(
            key=key,
            value=value,
            namespace=namespace,
            source_agent=source_agent,
            tier=tier,
            confidence=confidence,
            source=source,
            tags=tags,
            valid_at=valid_at,
            invalid_at=invalid_at,
            superseded_by=superseded_by,
            conflict_policy=conflict_policy,
            memory_group=memory_group,
        )

    def get(self, key: str, namespace: str = "universal") -> dict[str, Any] | None:
        return self._store.get(key, namespace)

    def search(
        self,
        query: str,
        namespaces: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self._store.search(
            query, namespaces=namespaces, min_confidence=min_confidence, limit=limit
        )

    # -- Confidence ----------------------------------------------------------

    def patch_confidence(self, *, namespace: str, key: str, confidence: float) -> bool:
        return self._store.patch_confidence(namespace=namespace, key=key, confidence=confidence)

    def get_confidence(self, *, namespace: str, key: str) -> float | None:
        return self._store.get_confidence(namespace=namespace, key=key)

    # -- Groups --------------------------------------------------------------

    def create_group(self, name: str, description: str = "") -> dict[str, Any]:
        return self._store.create_group(name, description)

    def add_group_member(self, group_name: str, agent_id: str, role: str = "member") -> bool:
        return self._store.add_group_member(group_name, agent_id, role)

    def remove_group_member(self, group_name: str, agent_id: str) -> bool:
        return self._store.remove_group_member(group_name, agent_id)

    def list_groups(self) -> list[dict[str, Any]]:
        return self._store.list_groups()

    def get_group_members(self, group_name: str) -> list[dict[str, Any]]:
        return self._store.get_group_members(group_name)

    def get_agent_groups(self, agent_id: str) -> list[str]:
        return self._store.get_agent_groups(agent_id)

    def agent_is_group_member(self, group_name: str, agent_id: str) -> bool:
        return self._store.agent_is_group_member(group_name, agent_id)

    def search_with_groups(
        self,
        query: str,
        agent_id: str,
        agent_namespace: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> list[dict[str, Any]]:
        return self._store.search_with_groups(
            query, agent_id, agent_namespace=agent_namespace, **kwargs
        )

    # -- Feedback ------------------------------------------------------------

    def record_feedback_event(
        self,
        *,
        event_id: str,
        namespace: str,
        entry_key: str | None,
        event_type: str,
        session_id: str | None,
        utility_score: float | None,
        details: dict[str, Any],
        timestamp: str,
        source_project: str | None = None,
    ) -> None:
        self._store.record_feedback_event(
            event_id=event_id,
            namespace=namespace,
            entry_key=entry_key,
            event_type=event_type,
            session_id=session_id,
            utility_score=utility_score,
            details=details,
            timestamp=timestamp,
            source_project=source_project,
        )

    def query_feedback_events(
        self,
        *,
        namespace: str | None = None,
        entry_key: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._store.query_feedback_events(
            namespace=namespace, entry_key=entry_key, limit=limit
        )

    # -- Introspection -------------------------------------------------------

    def list_namespaces(self) -> list[str]:
        return self._store.list_namespaces()

    def count_by_namespace(self) -> dict[str, int]:
        return self._store.count_by_namespace()

    def count_by_agent(self) -> dict[str, int]:
        return self._store.count_by_agent()

    # -- Write notifications -------------------------------------------------

    def get_write_notify_state(self) -> dict[str, Any]:
        return self._store.get_write_notify_state()

    def wait_for_write_notify(
        self,
        *,
        since_revision: int,
        timeout_sec: float,
        poll_interval_sec: float = 0.25,
    ) -> dict[str, Any]:
        return self._store.wait_for_write_notify(
            since_revision=since_revision,
            timeout_sec=timeout_sec,
            poll_interval_sec=poll_interval_sec,
        )

    # -- Lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self._store.close()


# ---------------------------------------------------------------------------
# SQLite Federation Backend
# ---------------------------------------------------------------------------


class SqliteFederationBackend:
    """SQLite-backed :class:`FederationBackend` — delegates to existing :class:`FederatedStore`."""

    def __init__(self, db_path: Path | None = None) -> None:
        from tapps_brain.federation import FederatedStore

        self._store = FederatedStore(db_path=db_path)

    def publish(
        self,
        project_id: str,
        entries: list[Any],
        project_root: str = "",
    ) -> int:
        return self._store.publish(project_id, entries, project_root)

    def unpublish(self, project_id: str, keys: list[str] | None = None) -> int:
        return self._store.unpublish(project_id, keys)

    def search(
        self,
        query: str,
        project_ids: list[str] | None = None,
        tags: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
        memory_group: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._store.search(
            query,
            project_ids=project_ids,
            tags=tags,
            min_confidence=min_confidence,
            limit=limit,
            memory_group=memory_group,
        )

    def get_project_entries(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self._store.get_project_entries(project_id, limit)

    def get_stats(self) -> dict[str, Any]:
        return self._store.get_stats()

    def close(self) -> None:
        self._store.close()


# ---------------------------------------------------------------------------
# SQLite Agent Registry Backend
# ---------------------------------------------------------------------------


class SqliteAgentRegistryBackend:
    """SQLite-backed :class:`AgentRegistryBackend`.

    Delegates to existing :class:`AgentRegistry`.
    """

    def __init__(self, registry_path: Path | None = None) -> None:
        from tapps_brain.hive import AgentRegistry

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
    """Create a :class:`HiveBackend` based on DSN or path.

    - ``None`` or a file path -> :class:`SqliteHiveBackend`
    - ``postgres://...`` or ``postgresql://...`` -> :class:`PostgresHiveBackend` (EPIC-055)
    """
    if dsn_or_path is not None and dsn_or_path.startswith(("postgres://", "postgresql://")):
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.postgres_hive import PostgresHiveBackend

        cm = PostgresConnectionManager(dsn_or_path)
        return PostgresHiveBackend(cm)
    db_path = Path(dsn_or_path) if dsn_or_path else None
    return SqliteHiveBackend(db_path=db_path, encryption_key=encryption_key)


def create_federation_backend(dsn_or_path: str | None = None) -> FederationBackend:
    """Create a :class:`FederationBackend` based on DSN or path.

    - ``None`` or a file path -> :class:`SqliteFederationBackend`
    - ``postgres://...`` or ``postgresql://...`` -> :class:`PostgresFederationBackend` (EPIC-055)
    """
    if dsn_or_path is not None and dsn_or_path.startswith(("postgres://", "postgresql://")):
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.postgres_federation import PostgresFederationBackend

        cm = PostgresConnectionManager(dsn_or_path)
        return PostgresFederationBackend(cm)
    db_path = Path(dsn_or_path) if dsn_or_path else None
    return SqliteFederationBackend(db_path=db_path)


def create_agent_registry_backend(
    registry_path: str | None = None,
) -> AgentRegistryBackend:
    """Create an :class:`AgentRegistryBackend`.

    - ``None`` or a file path -> :class:`SqliteAgentRegistryBackend`
    - ``postgres://...`` or ``postgresql://...`` -> :class:`PostgresAgentRegistry` (EPIC-055)
    """
    if registry_path is not None and registry_path.startswith(("postgres://", "postgresql://")):
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.postgres_hive import PostgresAgentRegistry

        cm = PostgresConnectionManager(registry_path)
        return PostgresAgentRegistry(cm)
    path = Path(registry_path) if registry_path else None
    return SqliteAgentRegistryBackend(registry_path=path)
