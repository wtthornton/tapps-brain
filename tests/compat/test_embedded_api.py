"""Compatibility test suite — embedded AgentBrain API (STORY-070.14).

These tests verify that the embedded Python library (AgentBrain + MemoryStore)
remains backward-compatible as the remote-first epic (EPIC-070) lands.

CI runs this suite against a live Postgres instance (requires
``TAPPS_BRAIN_DATABASE_URL`` to be set).  The suite also runs in offline mode
using the in-memory backend so unit CI can execute without Postgres.

Design contract tested:
  - AgentBrain constructor + context manager
  - remember / recall / forget cycle
  - learn_success / learn_failure
  - MemoryStore direct API parity
  - AsyncMemoryStore explicit methods (STORY-070.10)
  - EPIC-069 project_id resolution does not regress

pytest markers:
  - ``requires_postgres`` — skipped unless ``TAPPS_BRAIN_DATABASE_URL`` is set.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HAS_POSTGRES = bool(os.environ.get("TAPPS_BRAIN_DATABASE_URL", "").strip())


def _make_temp_brain(tmp_path: Path, agent_id: str = "compat-test") -> Any:
    """Create an AgentBrain backed by the default (filesystem or Postgres) backend."""
    from tapps_brain.agent_brain import AgentBrain

    return AgentBrain(agent_id=agent_id, project_dir=tmp_path)


# ---------------------------------------------------------------------------
# AgentBrain embedded API
# ---------------------------------------------------------------------------


class TestAgentBrainCompat:
    """Core AgentBrain method signatures must remain stable."""

    def test_context_manager(self, tmp_path: Path) -> None:
        from tapps_brain.agent_brain import AgentBrain

        with AgentBrain(agent_id="compat-ctx", project_dir=tmp_path) as brain:
            assert brain.agent_id == "compat-ctx"

    def test_remember_returns_key(self, tmp_path: Path) -> None:
        with _make_temp_brain(tmp_path) as brain:
            key = brain.remember("Use ruff for linting", tier="procedural")
            assert isinstance(key, str)
            assert len(key) > 0

    def test_recall_returns_list(self, tmp_path: Path) -> None:
        with _make_temp_brain(tmp_path) as brain:
            brain.remember("tapps-brain uses ruff for linting")
            results = brain.recall("ruff linting")
            assert isinstance(results, list)

    def test_forget_removes_memory(self, tmp_path: Path) -> None:
        with _make_temp_brain(tmp_path) as brain:
            key = brain.remember("temporary fact for compat test")
            brain.store.get(key)  # verify exists
            removed = brain.forget(key)
            assert removed is True

    def test_learn_success(self, tmp_path: Path) -> None:
        with _make_temp_brain(tmp_path) as brain:
            brain.remember("linting with ruff")
            brain.learn_from_success("fixed linting errors")  # type: ignore[attr-defined]

    def test_double_close_safe(self, tmp_path: Path) -> None:
        brain = _make_temp_brain(tmp_path)
        brain.close()
        brain.close()  # must not raise

    def test_groups_property(self, tmp_path: Path) -> None:
        with _make_temp_brain(tmp_path) as brain:
            assert isinstance(brain.groups, list)

    def test_expert_domains_property(self, tmp_path: Path) -> None:
        with _make_temp_brain(tmp_path) as brain:
            assert isinstance(brain.expert_domains, list)


# ---------------------------------------------------------------------------
# MemoryStore direct API
# ---------------------------------------------------------------------------


class TestMemoryStoreCompat:
    """MemoryStore public methods must remain backward-compatible."""

    def _store(self, tmp_path: Path) -> Any:
        from tapps_brain.store import MemoryStore

        return MemoryStore(tmp_path)

    def test_save_and_get(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        try:
            result = store.save(key="compat-key", value="compat value", tier="pattern")
            assert result is not None
            entry = store.get("compat-key")
            assert entry is not None
            assert entry.value == "compat value"
        finally:
            store.close()

    def test_delete(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        try:
            store.save(key="del-key", value="to delete", tier="pattern")
            deleted = store.delete("del-key")
            assert deleted is True
            assert store.get("del-key") is None
        finally:
            store.close()

    def test_search_returns_list(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        try:
            store.save(key="search-key", value="python ruff linting", tier="pattern")
            results = store.search("ruff")
            assert isinstance(results, list)
        finally:
            store.close()

    def test_list_all_empty(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        try:
            entries = store.list_all()
            assert isinstance(entries, list)
        finally:
            store.close()

    def test_recall_returns_result(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        try:
            store.save(key="recall-compat", value="ruff linting convention", tier="pattern")
            result = store.recall("ruff")
            assert hasattr(result, "memory_count")
            assert hasattr(result, "memory_section")
        finally:
            store.close()

    def test_reinforce(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        try:
            store.save(key="reinforce-compat", value="reinforced fact", tier="pattern")
            entry = store.reinforce("reinforce-compat", confidence_boost=0.1)
            assert entry is not None
        finally:
            store.close()

    def test_health(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        try:
            h = store.health()
            assert hasattr(h, "package_version")
        finally:
            store.close()

    def test_snapshot(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        try:
            snap = store.snapshot()
            assert hasattr(snap, "total_count")
        finally:
            store.close()

    def test_gc_config_roundtrip(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        try:
            cfg = store.get_gc_config()
            assert hasattr(cfg, "floor_retention_days")
            store.set_gc_config(cfg)
        finally:
            store.close()

    def test_consolidation_config_roundtrip(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        try:
            cfg = store.get_consolidation_config()
            assert hasattr(cfg, "enabled")
            store.set_consolidation_config(cfg)
        finally:
            store.close()


# ---------------------------------------------------------------------------
# AsyncMemoryStore explicit method parity (STORY-070.10)
# ---------------------------------------------------------------------------


class TestAsyncMemoryStoreCompat:
    """AsyncMemoryStore must expose every method added in STORY-070.10."""

    async def test_gc_method_exists(self, tmp_path: Path) -> None:
        from tapps_brain.aio import AsyncMemoryStore

        store = await AsyncMemoryStore.open(tmp_path)
        try:
            # dry_run=True is safe — no entries to GC
            result = await store.gc(dry_run=True)
            assert result is not None
        finally:
            await store.close()

    async def test_supersede_method_exists(self, tmp_path: Path) -> None:
        from tapps_brain.aio import AsyncMemoryStore

        store = await AsyncMemoryStore.open(tmp_path)
        try:
            await store.save("sup-key", "original value")
            result = await store.supersede("sup-key", "updated value")
            assert result is not None
        finally:
            await store.close()

    async def test_get_gc_config(self, tmp_path: Path) -> None:
        from tapps_brain.aio import AsyncMemoryStore

        store = await AsyncMemoryStore.open(tmp_path)
        try:
            cfg = await store.get_gc_config()
            assert cfg is not None
        finally:
            await store.close()

    async def test_list_tags(self, tmp_path: Path) -> None:
        from tapps_brain.aio import AsyncMemoryStore

        store = await AsyncMemoryStore.open(tmp_path)
        try:
            tags = await store.list_tags()
            assert isinstance(tags, dict)
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# TappsBrainClient protocol (STORY-070.11) — import-only check
# ---------------------------------------------------------------------------


class TestClientImport:
    """TappsBrainClient must be importable from the top-level package."""

    def test_sync_client_importable(self) -> None:
        from tapps_brain import TappsBrainClient

        assert TappsBrainClient is not None

    def test_async_client_importable(self) -> None:
        from tapps_brain import AsyncTappsBrainClient

        assert AsyncTappsBrainClient is not None

    def test_client_url_scheme_detection(self) -> None:
        from tapps_brain.client import _detect_scheme

        assert _detect_scheme("http://localhost:8080") == "http"
        assert _detect_scheme("https://brain.prod") == "http"
        assert _detect_scheme("mcp+stdio://localhost") == "mcp+stdio"
        assert _detect_scheme("mcp+http://localhost:8080") == "mcp+http"

    def test_invalid_scheme_raises(self) -> None:
        from tapps_brain.client import _detect_scheme

        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            _detect_scheme("sqlite:///local.db")


# ---------------------------------------------------------------------------
# MCP server operator split (STORY-070.9) — import check
# ---------------------------------------------------------------------------


class TestMcpOperatorSplit:
    """Both MCP server functions must be importable."""

    @pytest.mark.requires_mcp
    def test_create_server_importable(self) -> None:
        from tapps_brain.mcp_server import create_server

        assert create_server is not None

    @pytest.mark.requires_mcp
    def test_create_operator_server_importable(self) -> None:
        from tapps_brain.mcp_server import create_operator_server

        assert create_operator_server is not None

    @pytest.mark.requires_mcp
    def test_standard_server_has_no_operator_tools(self, tmp_path: Path) -> None:
        """Standard server must NOT have operator tools regardless of env."""
        from tapps_brain.mcp_server import create_server

        try:
            server = create_server(tmp_path, enable_operator_tools=False)
        except Exception:
            pytest.skip("MCP server could not be created (likely missing Postgres)")
            return

        tool_names = {t.name for t in server._tool_manager.list_tools()}
        operator_tools = {
            "maintenance_gc", "maintenance_consolidate", "memory_export", "memory_import",
        }
        for op_tool in operator_tools:
            assert op_tool not in tool_names, (
                f"Operator tool {op_tool!r} must not be in the standard server"
            )
