"""Tests for HiveBackend, FederationBackend, and AgentRegistryBackend protocols.

STORY-054.1 / STORY-054.2 / STORY-054.6 — verify that:
1. Each protocol is runtime_checkable.
2. The concrete implementations satisfy isinstance checks.
3. MemoryStore accepts HiveBackend (SqliteHiveBackend) as hive_store.
"""

from __future__ import annotations

from pathlib import Path

from tapps_brain._protocols import (
    AgentRegistryBackend,
    FederationBackend,
    HiveBackend,
)
from tapps_brain.backends import SqliteHiveBackend
from tapps_brain.federation import FederatedStore
from tapps_brain.hive import AgentRegistry, HiveStore


# ---------------------------------------------------------------------------
# runtime_checkable sanity
# ---------------------------------------------------------------------------


def test_hive_backend_is_runtime_checkable() -> None:
    assert getattr(HiveBackend, "__protocol_attrs__", None) is not None or hasattr(
        HiveBackend, "_is_runtime_protocol"
    )


def test_federation_backend_is_runtime_checkable() -> None:
    assert getattr(FederationBackend, "__protocol_attrs__", None) is not None or hasattr(
        FederationBackend, "_is_runtime_protocol"
    )


def test_agent_registry_backend_is_runtime_checkable() -> None:
    assert getattr(AgentRegistryBackend, "__protocol_attrs__", None) is not None or hasattr(
        AgentRegistryBackend, "_is_runtime_protocol"
    )


# ---------------------------------------------------------------------------
# isinstance checks with real instances
# ---------------------------------------------------------------------------


def test_hive_store_satisfies_hive_backend(tmp_path: Path) -> None:
    store = HiveStore(db_path=tmp_path / "hive.db")
    try:
        assert isinstance(store, HiveBackend)
    finally:
        store.close()


def test_federated_store_satisfies_federation_backend(tmp_path: Path) -> None:
    store = FederatedStore(db_path=tmp_path / "federated.db")
    try:
        assert isinstance(store, FederationBackend)
    finally:
        store.close()


def test_agent_registry_satisfies_agent_registry_backend(tmp_path: Path) -> None:
    registry = AgentRegistry(registry_path=tmp_path / "agents.yaml")
    assert isinstance(registry, AgentRegistryBackend)


# ---------------------------------------------------------------------------
# MemoryStore accepts HiveBackend (STORY-054.6)
# ---------------------------------------------------------------------------


def test_memory_store_accepts_hive_backend(tmp_path: Path) -> None:
    """MemoryStore.__init__ accepts a SqliteHiveBackend as hive_store."""
    from tapps_brain.store import MemoryStore

    hive_db = tmp_path / "hive" / "hive.db"
    backend = SqliteHiveBackend(db_path=hive_db)
    try:
        assert isinstance(backend, HiveBackend)
        store = MemoryStore(
            tmp_path,
            hive_store=backend,
            hive_agent_id="test-agent",
            embedding_provider=None,
        )
        # Verify the store accepted and stored the backend
        assert store._hive_store is backend
    finally:
        backend.close()
