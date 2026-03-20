"""Tests for tapps_brain public API surface (EPIC-009, STORY-009.2)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import tapps_brain


class TestPublicAPI:
    """Verify __all__ exports and py.typed marker."""

    def test_all_symbols_importable(self) -> None:
        """Every name in __all__ is importable from tapps_brain."""
        missing = []
        for name in tapps_brain.__all__:
            if not hasattr(tapps_brain, name):
                missing.append(name)
        assert missing == [], f"Missing from tapps_brain: {missing}"

    def test_all_is_complete(self) -> None:
        """__all__ contains all re-exported public symbols."""
        # Collect public names that are actually modules' classes/functions
        public_attrs = {
            name for name in dir(tapps_brain) if not name.startswith("_") and name != "annotations"
        }
        # Exclude module-level private helpers and submodule names
        exported = set(tapps_brain.__all__)
        # Every public attr should be in __all__ (minus submodule refs)
        for attr in public_attrs:
            obj = getattr(tapps_brain, attr)
            # Skip submodules (they're importable but not part of the API)
            if hasattr(obj, "__path__") or hasattr(obj, "__file__"):
                continue
            assert attr in exported, f"{attr} is public but missing from __all__"

    def test_py_typed_marker_exists(self) -> None:
        """py.typed marker file exists in the package directory."""
        package_dir = Path(tapps_brain.__file__).parent
        assert (package_dir / "py.typed").exists()


class TestGracefulImportErrors:
    """Verify CLI and MCP server produce clear errors when extras are missing."""

    def test_cli_raises_system_exit_without_typer(self) -> None:
        """cli.py raises SystemExit with helpful message when typer is missing."""
        # Remove cached cli module so reimport triggers the guard
        mods_to_remove = [k for k in sys.modules if k.startswith("tapps_brain.cli")]
        saved = {k: sys.modules.pop(k) for k in mods_to_remove}

        orig_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def _fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "typer":
                raise ImportError("No module named 'typer'")
            return orig_import(name, *args, **kwargs)

        try:
            with (
                patch("builtins.__import__", side_effect=_fake_import),
                pytest.raises(SystemExit, match="typer"),
            ):
                importlib.import_module("tapps_brain.cli")
        finally:
            # Restore modules
            for k in list(sys.modules):
                if k.startswith("tapps_brain.cli"):
                    sys.modules.pop(k, None)
            sys.modules.update(saved)

    def test_mcp_server_exits_without_mcp_package(self) -> None:
        """mcp_server._lazy_import_mcp() exits with message when mcp missing."""
        # Remove cached module
        mods_to_remove = [k for k in sys.modules if k.startswith("tapps_brain.mcp_server")]
        saved = {k: sys.modules.pop(k) for k in mods_to_remove}

        try:
            mod = importlib.import_module("tapps_brain.mcp_server")
            with (
                patch.dict(sys.modules, {"mcp": None, "mcp.server.fastmcp": None}),
                pytest.raises(SystemExit),
            ):
                mod._lazy_import_mcp()
        finally:
            for k in list(sys.modules):
                if k.startswith("tapps_brain.mcp_server"):
                    sys.modules.pop(k, None)
            sys.modules.update(saved)
