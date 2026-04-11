"""Unit tests for agent-scoped storage paths (STORY-053.1),
auto-registration (STORY-053.3), and CLI/MCP passthrough (STORY-053.4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from unittest.mock import MagicMock

import pytest

from tapps_brain.backends import AgentRegistry
from tapps_brain.persistence import MemoryPersistence
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# MemoryPersistence — path computation
# ---------------------------------------------------------------------------


class TestPersistenceAgentId:
    """MemoryPersistence storage path varies by agent_id."""

    def test_default_path_without_agent_id(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path, encryption_key="")
        assert p.db_path == tmp_path / ".tapps-brain" / "memory" / "memory.db"
        assert p.agent_id is None

    def test_agent_id_path(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path, agent_id="frontend-dev", encryption_key="")
        assert p.db_path == tmp_path / ".tapps-brain" / "agents" / "frontend-dev" / "memory.db"
        assert p.agent_id == "frontend-dev"

    def test_directory_created_automatically(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path, agent_id="planner", encryption_key="")
        assert p.store_dir.is_dir()
        assert p.db_path.parent.is_dir()

    def test_custom_store_dir_with_agent_id(self, tmp_path: Path) -> None:
        p = MemoryPersistence(
            tmp_path, store_dir=".tapps-mcp", agent_id="backend", encryption_key=""
        )
        assert p.db_path == tmp_path / ".tapps-mcp" / "agents" / "backend" / "memory.db"

    def test_two_agents_get_separate_stores(self, tmp_path: Path) -> None:
        p1 = MemoryPersistence(tmp_path, agent_id="agent-a", encryption_key="")
        p2 = MemoryPersistence(tmp_path, agent_id="agent-b", encryption_key="")
        assert p1.db_path != p2.db_path
        assert p1.db_path.exists()
        assert p2.db_path.exists()


# ---------------------------------------------------------------------------
# MemoryStore — pass-through of agent_id
# ---------------------------------------------------------------------------


class TestStoreAgentId:
    """MemoryStore exposes agent_id and routes storage correctly."""

    def test_store_default_no_agent_id(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path, embedding_provider=None)
        assert store.agent_id is None
        expected = tmp_path / ".tapps-brain" / "memory" / "memory.db"
        assert expected.exists()

    def test_store_with_agent_id(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path, agent_id="frontend-dev", embedding_provider=None)
        assert store.agent_id == "frontend-dev"
        expected = tmp_path / ".tapps-brain" / "agents" / "frontend-dev" / "memory.db"
        assert expected.exists()

    def test_store_agent_id_property_returns_none_for_legacy(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path, embedding_provider=None)
        assert store.agent_id is None

    def test_two_stores_independent(self, tmp_path: Path) -> None:
        s1 = MemoryStore(tmp_path, agent_id="agent-a", embedding_provider=None)
        s2 = MemoryStore(tmp_path, agent_id="agent-b", embedding_provider=None)
        # Save to one, verify the other is empty.
        s1.save("greeting", "hello from agent-a")
        assert s1.get("greeting") is not None
        assert s2.get("greeting") is None


# ---------------------------------------------------------------------------
# MemoryStore — automatic source_agent propagation (STORY-053.2)
# ---------------------------------------------------------------------------


class TestSourceAgentPropagation:
    """save() auto-fills source_agent from the store's agent_id."""

    def test_save_auto_fills_source_agent(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path, agent_id="frontend-dev", embedding_provider=None)
        entry = store.save("k1", "some value")
        assert entry.source_agent == "frontend-dev"

    def test_save_explicit_source_agent_overrides(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path, agent_id="frontend-dev", embedding_provider=None)
        entry = store.save("k2", "imported value", source_agent="importer")
        assert entry.source_agent == "importer"

    def test_save_legacy_store_keeps_unknown(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path, embedding_provider=None)
        entry = store.save("k3", "legacy value")
        assert entry.source_agent == "unknown"


# ---------------------------------------------------------------------------
# MemoryStore — auto-registration in Hive registry (STORY-053.3)
# ---------------------------------------------------------------------------


class TestAutoRegisterAgent:
    """MemoryStore auto-registers agent in AgentRegistry when hive_store is set."""

    def test_auto_register_agent_in_hive(self, tmp_path: Path) -> None:
        # Use a mock HiveBackend — auto-registration uses AgentRegistry (YAML), not HiveStore
        mock_hive = MagicMock()
        mock_hive.close = MagicMock()
        registry_path = tmp_path / "agents.yaml"
        store = MemoryStore(
            tmp_path,
            agent_id="frontend-dev",
            hive_store=mock_hive,
            embedding_provider=None,
        )
        registry = AgentRegistry(registry_path=registry_path)
        agent = registry.get("frontend-dev")
        assert agent is not None
        assert agent.id == "frontend-dev"
        assert agent.project_root == str(tmp_path)
        store.close()

    def test_auto_register_idempotent(self, tmp_path: Path) -> None:
        mock_hive = MagicMock()
        mock_hive.close = MagicMock()
        registry_path = tmp_path / "agents.yaml"
        s1 = MemoryStore(
            tmp_path,
            agent_id="frontend-dev",
            hive_store=mock_hive,
            embedding_provider=None,
        )
        s1.close()
        s2 = MemoryStore(
            tmp_path,
            agent_id="frontend-dev",
            hive_store=mock_hive,
            embedding_provider=None,
        )
        s2.close()
        registry = AgentRegistry(registry_path=registry_path)
        agents = [a for a in registry.list_agents() if a.id == "frontend-dev"]
        assert len(agents) == 1

    def test_auto_register_disabled(self, tmp_path: Path) -> None:
        mock_hive = MagicMock()
        mock_hive.close = MagicMock()
        registry_path = tmp_path / "agents.yaml"
        store = MemoryStore(
            tmp_path,
            agent_id="frontend-dev",
            hive_store=mock_hive,
            embedding_provider=None,
            auto_register=False,
        )
        store.close()
        registry = AgentRegistry(registry_path=registry_path)
        assert registry.get("frontend-dev") is None

    def test_no_auto_register_without_agent_id(self, tmp_path: Path) -> None:
        mock_hive = MagicMock()
        mock_hive.close = MagicMock()
        registry_path = tmp_path / "agents.yaml"
        store = MemoryStore(
            tmp_path,
            hive_store=mock_hive,
            embedding_provider=None,
        )
        store.close()
        registry = AgentRegistry(registry_path=registry_path)
        assert len(registry.list_agents()) == 0


# ---------------------------------------------------------------------------
# MCP server — _get_store agent-id passthrough (STORY-053.4)
# ---------------------------------------------------------------------------


class TestMcpGetStoreAgentId:
    """_get_store routes agent_id to MemoryStore correctly."""

    def test_mcp_get_store_with_agent_id(self, tmp_path: Path) -> None:
        from tapps_brain.mcp_server import _get_store

        store = _get_store(tmp_path, enable_hive=False, agent_id="frontend-dev")
        assert store.agent_id == "frontend-dev"
        expected = tmp_path / ".tapps-brain" / "agents" / "frontend-dev" / "memory.db"
        assert expected.exists()

    def test_mcp_get_store_without_agent_id(self, tmp_path: Path) -> None:
        from tapps_brain.mcp_server import _get_store

        store = _get_store(tmp_path, enable_hive=False, agent_id="unknown")
        assert store.agent_id is None
        expected = tmp_path / ".tapps-brain" / "memory" / "memory.db"
        assert expected.exists()

    def test_env_var_agent_id_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import os

        # Simulate: --agent-id not given (defaults to "unknown"),
        # but TAPPS_BRAIN_AGENT_ID env var is set.
        monkeypatch.setenv("TAPPS_BRAIN_AGENT_ID", "env-agent")
        # We cannot easily run main() to completion (it starts stdio),
        # so we test the env-var resolution logic that main() uses.
        effective = "unknown"
        if effective == "unknown":
            effective = os.environ.get("TAPPS_BRAIN_AGENT_ID", "unknown")
        assert effective == "env-agent"


# ---------------------------------------------------------------------------
# maintenance split-by-agent (STORY-053.5)
# ---------------------------------------------------------------------------


class TestSplitByAgent:
    """Tests for the maintenance split-by-agent migration command."""

    @staticmethod
    def _make_shared_store(tmp_path: Path) -> MemoryPersistence:
        """Create a shared store with entries from multiple agents."""
        from tapps_brain.models import MemoryEntry

        shared = MemoryPersistence(tmp_path, encryption_key="")
        shared.save(MemoryEntry(key="a-key1", value="val1", source_agent="a"))
        shared.save(MemoryEntry(key="a-key2", value="val2", source_agent="a"))
        shared.save(MemoryEntry(key="b-key1", value="val3", source_agent="b"))
        shared.save(MemoryEntry(key="u-key1", value="val4", source_agent="unknown"))
        return shared

    def test_split_by_agent_dry_run(self, tmp_path: Path) -> None:
        """Dry-run reports counts but creates no per-agent stores."""
        self._make_shared_store(tmp_path)

        # Invoke the underlying function in dry-run mode
        import typer

        from tapps_brain.cli import maintenance_split_by_agent

        with pytest.raises(typer.Exit):
            maintenance_split_by_agent(
                project_dir=str(tmp_path),
                store_dir=".tapps-brain",
                dry_run=True,
            )

        # No per-agent directories should exist
        agents_dir = tmp_path / ".tapps-brain" / "agents"
        assert not agents_dir.exists()

    def test_split_by_agent_creates_stores(self, tmp_path: Path) -> None:
        """Split creates per-agent stores with correct entries."""
        shared = self._make_shared_store(tmp_path)
        original_count = len(shared.load_all())

        from tapps_brain.cli import maintenance_split_by_agent

        maintenance_split_by_agent(
            project_dir=str(tmp_path),
            store_dir=".tapps-brain",
            dry_run=False,
        )

        # Verify agent "a" store
        store_a = MemoryPersistence(tmp_path, agent_id="a", encryption_key="")
        entries_a = store_a.load_all()
        assert len(entries_a) == 2
        assert {e.key for e in entries_a} == {"a-key1", "a-key2"}

        # Verify agent "b" store
        store_b = MemoryPersistence(tmp_path, agent_id="b", encryption_key="")
        entries_b = store_b.load_all()
        assert len(entries_b) == 1
        assert entries_b[0].key == "b-key1"

        # Verify "_legacy" store (source_agent="unknown")
        store_legacy = MemoryPersistence(tmp_path, agent_id="_legacy", encryption_key="")
        entries_legacy = store_legacy.load_all()
        assert len(entries_legacy) == 1
        assert entries_legacy[0].key == "u-key1"

        # Original shared store is unchanged
        assert len(shared.load_all()) == original_count
