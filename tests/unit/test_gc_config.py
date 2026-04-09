"""Unit tests for GC config MCP tools and CLI (story-015.7)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


def _extract_json(output: str) -> dict[str, object]:
    """Extract JSON from CLI output, skipping any warning lines."""
    json_start = output.find("{")
    return json.loads(output[json_start:])  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# GCConfig dataclass
# ---------------------------------------------------------------------------


class TestGCConfig:
    """Tests for the GCConfig dataclass."""

    def test_defaults(self) -> None:
        from tapps_brain.gc import GCConfig

        cfg = GCConfig()
        assert cfg.floor_retention_days == 30
        assert cfg.session_expiry_days == 7
        assert cfg.contradicted_threshold == pytest.approx(0.2)

    def test_custom_values(self) -> None:
        from tapps_brain.gc import GCConfig

        cfg = GCConfig(floor_retention_days=60, session_expiry_days=14, contradicted_threshold=0.1)
        assert cfg.floor_retention_days == 60
        assert cfg.session_expiry_days == 14
        assert cfg.contradicted_threshold == pytest.approx(0.1)

    def test_to_dict(self) -> None:
        from tapps_brain.gc import GCConfig

        cfg = GCConfig(floor_retention_days=45, session_expiry_days=3, contradicted_threshold=0.15)
        d = cfg.to_dict()
        assert d == {
            "floor_retention_days": 45,
            "session_expiry_days": 3,
            "contradicted_threshold": 0.15,
            "session_index_ttl_days": 90,
        }

    def test_zero_threshold_is_valid(self) -> None:
        from tapps_brain.gc import GCConfig

        cfg = GCConfig(contradicted_threshold=0.0)
        assert cfg.contradicted_threshold == pytest.approx(0.0)

    def test_zero_retention_days_is_valid(self) -> None:
        from tapps_brain.gc import GCConfig

        cfg = GCConfig(floor_retention_days=0, session_expiry_days=0)
        assert cfg.floor_retention_days == 0
        assert cfg.session_expiry_days == 0


# ---------------------------------------------------------------------------
# MemoryGarbageCollector accepts GCConfig
# ---------------------------------------------------------------------------


class TestMemoryGarbageCollectorGCConfig:
    """Tests that MemoryGarbageCollector uses GCConfig thresholds."""

    def test_gc_config_applied(self) -> None:
        from tapps_brain.gc import GCConfig, MemoryGarbageCollector

        cfg = GCConfig(floor_retention_days=99, session_expiry_days=99, contradicted_threshold=0.05)
        gc = MemoryGarbageCollector(gc_config=cfg)
        assert gc._floor_retention_days == 99
        assert gc._session_expiry_days == 99
        assert gc._contradicted_threshold == pytest.approx(0.05)

    def test_explicit_kwargs_override_gc_config(self) -> None:
        from tapps_brain.gc import GCConfig, MemoryGarbageCollector

        cfg = GCConfig(floor_retention_days=99)
        gc = MemoryGarbageCollector(gc_config=cfg, floor_retention_days=10)
        assert gc._floor_retention_days == 10  # explicit wins

    def test_defaults_without_gc_config(self) -> None:
        from tapps_brain.gc import MemoryGarbageCollector

        gc = MemoryGarbageCollector()
        assert gc._floor_retention_days == 30
        assert gc._session_expiry_days == 7
        assert gc._contradicted_threshold == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# MemoryStore.get_gc_config / set_gc_config
# ---------------------------------------------------------------------------


class TestMemoryStoreGCConfig:
    """Tests for get_gc_config / set_gc_config on MemoryStore."""

    @pytest.fixture()
    def store(self, tmp_path: Path):
        from tapps_brain.store import MemoryStore

        s = MemoryStore(tmp_path)
        yield s
        s.close()

    def test_get_gc_config_returns_default(self, store) -> None:
        from tapps_brain.gc import GCConfig

        cfg = store.get_gc_config()
        assert isinstance(cfg, GCConfig)
        assert cfg.floor_retention_days == 30

    def test_set_gc_config_updates_store(self, store) -> None:
        from tapps_brain.gc import GCConfig

        new_cfg = GCConfig(floor_retention_days=60, session_expiry_days=14)
        store.set_gc_config(new_cfg)
        updated = store.get_gc_config()
        assert updated.floor_retention_days == 60
        assert updated.session_expiry_days == 14


# ---------------------------------------------------------------------------
# MCP tools: memory_gc_config and memory_gc_config_set
# ---------------------------------------------------------------------------


def _tool_fn(mcp_server, name: str):
    for tool in mcp_server._tool_manager.list_tools():
        if tool.name == name:
            return tool.fn
    msg = f"tool not found: {name}"
    raise KeyError(msg)


@pytest.fixture()
def mcp_server(tmp_path: Path):
    pytest.importorskip("mcp")
    from tapps_brain.mcp_server import create_server

    server = create_server(tmp_path)
    yield server
    server._tapps_store.close()


class TestMemoryGcConfigMCPTool:
    """Tests for memory_gc_config and memory_gc_config_set MCP tools."""

    @pytest.mark.requires_mcp
    def test_memory_gc_config_returns_defaults(self, mcp_server) -> None:
        fn = _tool_fn(mcp_server, "memory_gc_config")
        result = json.loads(fn())
        assert result["floor_retention_days"] == 30
        assert result["session_expiry_days"] == 7
        assert result["contradicted_threshold"] == pytest.approx(0.2)

    @pytest.mark.requires_mcp
    def test_memory_gc_config_set_updates_values(self, mcp_server) -> None:
        set_fn = _tool_fn(mcp_server, "memory_gc_config_set")
        get_fn = _tool_fn(mcp_server, "memory_gc_config")

        result = json.loads(set_fn(floor_retention_days=60))
        assert result["status"] == "updated"
        assert result["floor_retention_days"] == 60
        # Other fields unchanged
        assert result["session_expiry_days"] == 7

        # Verify persisted
        current = json.loads(get_fn())
        assert current["floor_retention_days"] == 60

    @pytest.mark.requires_mcp
    def test_memory_gc_config_set_partial_update(self, mcp_server) -> None:
        set_fn = _tool_fn(mcp_server, "memory_gc_config_set")

        result = json.loads(set_fn(session_expiry_days=3, contradicted_threshold=0.1))
        assert result["session_expiry_days"] == 3
        assert result["contradicted_threshold"] == pytest.approx(0.1)
        assert result["floor_retention_days"] == 30  # unchanged

    @pytest.mark.requires_mcp
    def test_memory_gc_config_tools_registered(self, mcp_server) -> None:
        tool_names = [t.name for t in mcp_server._tool_manager.list_tools()]
        assert "memory_gc_config" in tool_names
        assert "memory_gc_config_set" in tool_names


# ---------------------------------------------------------------------------
# CLI: maintenance gc-config
# ---------------------------------------------------------------------------


class TestMaintenanceGcConfigCLI:
    """Tests for the `maintenance gc-config` CLI command."""

    def test_gc_config_shows_defaults(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["maintenance", "gc-config", "--project-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "floor_retention_days" in result.output
        assert "30" in result.output

    def test_gc_config_json_output(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app, ["maintenance", "gc-config", "--project-dir", str(tmp_path), "--json"]
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["floor_retention_days"] == 30
        assert data["session_expiry_days"] == 7

    def test_gc_config_set_floor_retention(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "maintenance",
                "gc-config",
                "--project-dir",
                str(tmp_path),
                "--floor-retention-days",
                "60",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["floor_retention_days"] == 60

    def test_gc_config_set_multiple_values(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "maintenance",
                "gc-config",
                "--project-dir",
                str(tmp_path),
                "--session-expiry-days",
                "14",
                "--contradicted-threshold",
                "0.1",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["session_expiry_days"] == 14
        assert data["contradicted_threshold"] == pytest.approx(0.1)
        assert data["floor_retention_days"] == 30  # unchanged
