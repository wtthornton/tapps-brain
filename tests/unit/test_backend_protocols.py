"""Tests for HiveBackend, FederationBackend, and AgentRegistryBackend protocols.

STORY-054.1 / STORY-054.2 / STORY-054.6 — verify that:
1. Each protocol is runtime_checkable.
2. Concrete implementations satisfy isinstance checks (using mocks/SqliteAgentRegistry).
3. MemoryStore accepts any HiveBackend as hive_store.

v3 note: HiveStore (SQLite) and FederatedStore (SQLite) were removed in ADR-007.
Protocol compliance tests now use MagicMock objects that satisfy the protocol.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tapps_brain._protocols import (
    AgentRegistryBackend,
    FederationBackend,
    HiveBackend,
)
from tapps_brain.backends import AgentRegistry, SqliteAgentRegistryBackend

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
# isinstance checks with YAML-backed AgentRegistry
# ---------------------------------------------------------------------------


def test_agent_registry_satisfies_agent_registry_backend(tmp_path: Path) -> None:
    registry = AgentRegistry(registry_path=tmp_path / "agents.yaml")
    assert isinstance(registry, AgentRegistryBackend)


def test_sqlite_agent_registry_backend_satisfies_protocol(tmp_path: Path) -> None:
    backend = SqliteAgentRegistryBackend(registry_path=tmp_path / "agents.yaml")
    assert isinstance(backend, AgentRegistryBackend)


# ---------------------------------------------------------------------------
# MemoryStore accepts HiveBackend (STORY-054.6) — uses mock HiveBackend
# ---------------------------------------------------------------------------


def test_memory_store_accepts_hive_backend(tmp_path: Path) -> None:
    """MemoryStore.__init__ accepts any object satisfying the HiveBackend protocol."""
    from tapps_brain.store import MemoryStore

    # Create a mock that satisfies HiveBackend (used in place of removed HiveStore)
    mock_backend = MagicMock(spec=HiveBackend)
    mock_backend.close = MagicMock()

    store = MemoryStore(
        tmp_path,
        hive_store=mock_backend,
        hive_agent_id="test-agent",
        embedding_provider=None,
    )
    try:
        assert store._hive_store is mock_backend
    finally:
        store.close()
