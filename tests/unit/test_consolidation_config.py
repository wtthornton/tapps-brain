"""Unit tests for auto-consolidation config MCP tools and CLI (story-015.8)."""

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
# ConsolidationConfig dataclass
# ---------------------------------------------------------------------------


class TestConsolidationConfig:
    """Tests for the ConsolidationConfig dataclass."""

    def test_defaults(self) -> None:
        from tapps_brain.store import ConsolidationConfig

        cfg = ConsolidationConfig()
        assert cfg.enabled is True
        assert cfg.threshold == pytest.approx(0.7)
        assert cfg.min_entries == 3

    def test_custom_values(self) -> None:
        from tapps_brain.store import ConsolidationConfig

        cfg = ConsolidationConfig(enabled=True, threshold=0.5, min_entries=5)
        assert cfg.enabled is True
        assert cfg.threshold == pytest.approx(0.5)
        assert cfg.min_entries == 5

    def test_to_dict(self) -> None:
        from tapps_brain.store import ConsolidationConfig

        cfg = ConsolidationConfig(enabled=True, threshold=0.6, min_entries=4)
        d = cfg.to_dict()
        assert d == {
            "enabled": True,
            "threshold": 0.6,
            "min_entries": 4,
        }


# ---------------------------------------------------------------------------
# MemoryStore.get_consolidation_config / set_consolidation_config
# ---------------------------------------------------------------------------


class TestMemoryStoreConsolidationConfig:
    """Tests for get_consolidation_config / set_consolidation_config on MemoryStore."""

    @pytest.fixture()
    def store(self, tmp_path: Path):
        from tapps_brain.store import MemoryStore

        s = MemoryStore(tmp_path)
        yield s
        s.close()

    def test_get_consolidation_config_returns_default(self, store) -> None:
        from tapps_brain.store import ConsolidationConfig

        cfg = store.get_consolidation_config()
        assert isinstance(cfg, ConsolidationConfig)
        assert cfg.enabled is True
        assert cfg.threshold == pytest.approx(0.7)
        assert cfg.min_entries == 3

    def test_set_consolidation_config_updates_store(self, store) -> None:
        from tapps_brain.store import ConsolidationConfig

        new_cfg = ConsolidationConfig(enabled=True, threshold=0.5, min_entries=5)
        store.set_consolidation_config(new_cfg)
        updated = store.get_consolidation_config()
        assert updated.enabled is True
        assert updated.threshold == pytest.approx(0.5)
        assert updated.min_entries == 5


# ---------------------------------------------------------------------------
# MCP tools: memory_consolidation_config and memory_consolidation_config_set
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

    server = create_server(tmp_path, enable_hive=False)
    yield server
    st = server._tapps_store
    h = getattr(st, "_hive_store", None)
    if h is not None:
        h.close()
    st.close()


class TestConsolidationConfigMCPTool:
    """Tests for memory_consolidation_config and memory_consolidation_config_set MCP tools."""

    @pytest.mark.requires_mcp
    def test_memory_consolidation_config_returns_defaults(self, mcp_server) -> None:
        fn = _tool_fn(mcp_server, "memory_consolidation_config")
        result = json.loads(fn())
        assert result["enabled"] is True
        assert result["threshold"] == pytest.approx(0.7)
        assert result["min_entries"] == 3

    @pytest.mark.requires_mcp
    def test_memory_consolidation_config_set_updates_values(self, mcp_server) -> None:
        set_fn = _tool_fn(mcp_server, "memory_consolidation_config_set")
        get_fn = _tool_fn(mcp_server, "memory_consolidation_config")

        result = json.loads(set_fn(enabled=True))
        assert result["status"] == "updated"
        assert result["enabled"] is True

        current = json.loads(get_fn())
        assert current["enabled"] is True

    @pytest.mark.requires_mcp
    def test_memory_consolidation_config_set_partial_update(self, mcp_server) -> None:
        set_fn = _tool_fn(mcp_server, "memory_consolidation_config_set")

        result = json.loads(set_fn(threshold=0.5, min_entries=5))
        assert result["status"] == "updated"
        assert result["threshold"] == pytest.approx(0.5)
        assert result["min_entries"] == 5
        # enabled should remain at default
        assert result["enabled"] is True

    @pytest.mark.requires_mcp
    def test_memory_consolidation_config_tools_registered(self, mcp_server) -> None:
        tool_names = [t.name for t in mcp_server._tool_manager.list_tools()]
        assert "memory_consolidation_config" in tool_names
        assert "memory_consolidation_config_set" in tool_names


# ---------------------------------------------------------------------------
# CLI: maintenance consolidation-config
# ---------------------------------------------------------------------------


class TestMaintenanceConsolidationConfigCLI:
    """Tests for the `maintenance consolidation-config` CLI command."""

    def test_consolidation_config_shows_defaults(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app, ["maintenance", "consolidation-config", "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "enabled" in result.output
        assert "True" in result.output

    def test_consolidation_config_json_output(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["maintenance", "consolidation-config", "--project-dir", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["enabled"] is True
        assert data["threshold"] == pytest.approx(0.7)
        assert data["min_entries"] == 3

    def test_consolidation_config_set_enabled(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "maintenance",
                "consolidation-config",
                "--project-dir",
                str(tmp_path),
                "--enabled",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["status"] == "updated"
        assert data["enabled"] is True

    def test_consolidation_config_set_threshold(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "maintenance",
                "consolidation-config",
                "--project-dir",
                str(tmp_path),
                "--threshold",
                "0.5",
                "--min-entries",
                "5",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["status"] == "updated"
        assert data["threshold"] == pytest.approx(0.5)
        assert data["min_entries"] == 5
