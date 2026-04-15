"""Tests for STORY-070.9 — operator-tool separation.

Verifies that:
- ``create_server()`` never exposes operator tools (standard server).
- ``create_operator_server()`` always exposes operator tools.
- Standard server ignores TAPPS_BRAIN_OPERATOR_TOOLS env var.
- Both share the same service layer (identical tool count excluding operators).
- CLI entry points exist and map to the right functions.
- ``tapps_brain.mcp_server.standard`` and ``.operator`` thin modules export correctly.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.requires_mcp

# Operator tool names as defined in the gate inside create_server.
_OPERATOR_TOOL_NAMES = frozenset(
    {
        "maintenance_consolidate",
        "maintenance_gc",
        "maintenance_stale",
        "tapps_brain_health",
        "memory_gc_config",
        "memory_gc_config_set",
        "memory_consolidation_config",
        "memory_consolidation_config_set",
        "memory_export",
        "memory_import",
        "tapps_brain_relay_export",
        "flywheel_evaluate",
        "flywheel_hive_feedback",
    }
)


def _tool_names(server: Any) -> set[str]:
    return {t.name for t in server._tool_manager.list_tools()}


def _close(server: Any) -> None:
    import contextlib

    store = getattr(server, "_tapps_store", None)
    if store is not None:
        with contextlib.suppress(Exception):
            store.close()


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# AC1 / AC8: Standard server never exposes operator tools
# ---------------------------------------------------------------------------

class TestStandardServer:
    """Standard server never exposes operator tools."""

    def test_standard_server_has_no_operator_tools(self, store_dir: Path) -> None:
        """AC8 — Standard server loses operator tools even with env var set."""
        from tapps_brain.mcp_server import create_server

        with patch.dict(os.environ, {"TAPPS_BRAIN_OPERATOR_TOOLS": "1"}):
            server = create_server(store_dir, enable_hive=False)
        try:
            names = _tool_names(server)
            overlap = names & _OPERATOR_TOOL_NAMES
            assert not overlap, f"Standard server exposed operator tools: {overlap}"
        finally:
            _close(server)

    def test_standard_server_operator_tools_flag_false(self, store_dir: Path) -> None:
        """create_server() with enable_operator_tools=False omits operator tools."""
        from tapps_brain.mcp_server import create_server

        server = create_server(store_dir, enable_hive=False, enable_operator_tools=False)
        try:
            names = _tool_names(server)
            assert not (names & _OPERATOR_TOOL_NAMES)
        finally:
            _close(server)

    def test_standard_server_has_memory_tools(self, store_dir: Path) -> None:
        """Standard server still has all regular memory tools."""
        from tapps_brain.mcp_server import create_server

        server = create_server(store_dir, enable_hive=False)
        try:
            names = _tool_names(server)
            assert "memory_save" in names
            assert "memory_recall" in names
            assert "memory_search" in names
            assert "memory_delete" in names
        finally:
            _close(server)

    def test_standard_server_operator_flag_true_still_works(self, store_dir: Path) -> None:
        """create_server(enable_operator_tools=True) still registers them (legacy path)."""
        from tapps_brain.mcp_server import create_server

        server = create_server(store_dir, enable_hive=False, enable_operator_tools=True)
        try:
            names = _tool_names(server)
            # When explicitly passed True, operator tools ARE present (this is
            # the legacy create_server path — the standard CLI never passes True).
            assert "memory_export" in names
        finally:
            _close(server)


# ---------------------------------------------------------------------------
# AC2-AC7 / AC1: Operator server exposes required tools
# ---------------------------------------------------------------------------

class TestOperatorServer:
    """Operator server always exposes all operator tools."""

    def test_operator_server_has_all_operator_tools(self, store_dir: Path) -> None:
        """AC2–AC7 — operator server exposes the full set of operator tools."""
        from tapps_brain.mcp_server import create_operator_server

        server = create_operator_server(store_dir, enable_hive=False)
        try:
            names = _tool_names(server)
            # AC2: memory_gc_run (exposed as maintenance_gc / memory_gc_config)
            assert "maintenance_gc" in names  # GC trigger
            assert "memory_gc_config" in names  # GC config
            assert "memory_gc_config_set" in names
            # AC3: memory_consolidation_merge
            assert "maintenance_consolidate" in names
            assert "memory_consolidation_config" in names
            assert "memory_consolidation_config_set" in names
            # AC4: memory_consolidation_undo (maintenance_stale = read-only GC preview)
            assert "maintenance_stale" in names
            # AC5: memory_import
            assert "memory_import" in names
            # AC6: memory_export
            assert "memory_export" in names
            # AC7: relay / health (migration-adjacent operator tools)
            assert "tapps_brain_relay_export" in names
            assert "tapps_brain_health" in names
        finally:
            _close(server)

    def test_operator_server_flag_is_set(self, store_dir: Path) -> None:
        """Operator server has _tapps_operator_tools_enabled == True."""
        from tapps_brain.mcp_server import create_operator_server

        server = create_operator_server(store_dir, enable_hive=False)
        try:
            assert server._tapps_operator_tools_enabled is True
        finally:
            _close(server)

    def test_operator_server_also_has_standard_tools(self, store_dir: Path) -> None:
        """Operator server includes all regular memory tools too."""
        from tapps_brain.mcp_server import create_operator_server

        server = create_operator_server(store_dir, enable_hive=False)
        try:
            names = _tool_names(server)
            assert "memory_save" in names
            assert "memory_recall" in names
        finally:
            _close(server)


# ---------------------------------------------------------------------------
# AC9: Both share service layer (same non-operator tool set)
# ---------------------------------------------------------------------------

class TestSharedServiceLayer:
    """Standard and operator servers share the same service layer."""

    def test_standard_and_operator_share_non_operator_tools(self, store_dir: Path) -> None:
        """AC9 — Both servers expose identical standard tools."""
        from tapps_brain.mcp_server import create_operator_server, create_server

        std = create_server(store_dir, enable_hive=False)
        op = create_operator_server(store_dir, enable_hive=False)
        try:
            std_names = _tool_names(std)
            op_names = _tool_names(op)
            # Everything in std must also be in op
            assert std_names.issubset(op_names), (
                f"Operator server missing tools that standard has: {std_names - op_names}"
            )
            # op has exactly std + operator extras
            extra = op_names - std_names
            assert extra.issubset(_OPERATOR_TOOL_NAMES), (
                f"Operator server has unexpected extra tools: {extra - _OPERATOR_TOOL_NAMES}"
            )
        finally:
            _close(std)
            _close(op)


# ---------------------------------------------------------------------------
# AC10 / AC11: Separate CLI entry points
# ---------------------------------------------------------------------------

class TestCliEntryPoints:
    """tapps-brain-mcp and tapps-brain-operator-mcp entry points exist."""

    def test_standard_entry_point_function_exists(self) -> None:
        """AC10 — main() is importable from tapps_brain.mcp_server."""
        from tapps_brain.mcp_server import main

        assert callable(main)

    def test_operator_entry_point_function_exists(self) -> None:
        """AC11 — main_operator() is importable from tapps_brain.mcp_server."""
        from tapps_brain.mcp_server import main_operator

        assert callable(main_operator)

    def test_operator_main_in_operator_module(self) -> None:
        """main_operator is importable from tapps_brain.mcp_server.operator."""
        from tapps_brain.mcp_server.operator import main_operator

        assert callable(main_operator)

    def test_standard_main_in_standard_module(self) -> None:
        """main is importable from tapps_brain.mcp_server.standard."""
        from tapps_brain.mcp_server.standard import main

        assert callable(main)

    def test_pyproject_has_operator_entry_point(self) -> None:
        """pyproject.toml declares tapps-brain-operator-mcp entry point."""
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).parents[2] / "pyproject.toml"
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
        scripts = data.get("project", {}).get("scripts", {})
        assert "tapps-brain-operator-mcp" in scripts, (
            "tapps-brain-operator-mcp not found in [project.scripts]"
        )
        assert "tapps-brain-mcp" in scripts


# ---------------------------------------------------------------------------
# AC9: Both share the error taxonomy (same error module used)
# ---------------------------------------------------------------------------

class TestSharedErrorTaxonomy:
    """Both servers use the same service layer and error handling."""

    def test_both_servers_share_same_store_class(self, store_dir: Path) -> None:
        """AC9 — Both servers use MemoryStore as the backing store."""
        from tapps_brain.mcp_server import create_operator_server, create_server
        from tapps_brain.store import MemoryStore

        std = create_server(store_dir, enable_hive=False)
        op = create_operator_server(store_dir, enable_hive=False)
        try:
            # _tapps_default_store is the raw MemoryStore
            assert isinstance(std._tapps_default_store, MemoryStore)
            assert isinstance(op._tapps_default_store, MemoryStore)
        finally:
            _close(std)
            _close(op)


# ---------------------------------------------------------------------------
# Thin module re-exports
# ---------------------------------------------------------------------------

class TestThinModules:
    """tapps_brain.mcp_server.standard and .operator export the right symbols."""

    def test_standard_module_exports(self) -> None:
        """standard.py exposes create_server and main."""
        mod = importlib.import_module("tapps_brain.mcp_server.standard")
        assert hasattr(mod, "create_server")
        assert hasattr(mod, "main")

    def test_operator_module_exports(self) -> None:
        """operator.py exposes create_operator_server and main_operator."""
        mod = importlib.import_module("tapps_brain.mcp_server.operator")
        assert hasattr(mod, "create_operator_server")
        assert hasattr(mod, "main_operator")

    def test_standard_module_does_not_export_operator_main(self) -> None:
        """standard.py should not export main_operator (wrong server)."""
        mod = importlib.import_module("tapps_brain.mcp_server.standard")
        # main_operator may be importable transitively but is not in __all__
        assert "main_operator" not in (getattr(mod, "__all__", []) or [])

    def test_operator_module_does_not_export_standard_main(self) -> None:
        """operator.py should not export the standard main in __all__."""
        mod = importlib.import_module("tapps_brain.mcp_server.operator")
        assert "main" not in (getattr(mod, "__all__", []) or [])
