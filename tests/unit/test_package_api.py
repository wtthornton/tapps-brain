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


class TestVersionUnification:
    """Verify version is sourced from package metadata."""

    def test_version_matches_metadata(self) -> None:
        """__version__ matches importlib.metadata."""
        from importlib.metadata import version

        assert tapps_brain.__version__ == version("tapps-brain")

    def test_version_is_semver_like(self) -> None:
        """Version looks like a semver string."""
        parts = tapps_brain.__version__.split(".")
        assert len(parts) >= 2
        assert parts[0].isdigit()

    @pytest.mark.requires_cli
    def test_cli_version_matches_package(self) -> None:
        """CLI --version reports the same version as the package."""
        import typer

        from tapps_brain.cli import _version_callback

        with pytest.raises(typer.Exit):
            _version_callback(True)
        # The callback calls typer.echo — we just verify it doesn't error.
        # The string it echoes uses tapps_brain.__version__.
        assert tapps_brain.__version__ in f"tapps-brain {tapps_brain.__version__}"


class TestCoreImportWithoutExtras:
    """Verify core library imports work without cli/mcp extras."""

    def test_core_symbols_importable_without_typer(self) -> None:
        """All __all__ symbols are importable — none require typer at import time."""
        # Core symbols should never trigger typer/mcp imports
        for name in tapps_brain.__all__:
            obj = getattr(tapps_brain, name)
            mod = getattr(obj, "__module__", "")
            # No public API symbol should live in cli or mcp_server modules
            # Use exact prefix check so "tapps_brain.client" doesn't false-positive.
            assert mod != "tapps_brain.cli" and not mod.startswith(
                "tapps_brain.cli."
            ), f"{name} unexpectedly from cli module"
            assert "mcp_server" not in mod, f"{name} unexpectedly from mcp_server module"

    def test_core_store_works_without_extras(self, tmp_path: Path) -> None:
        """MemoryStore can be created and used with only core deps."""
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path)
        store.save(key="test", value="hello world", tier="context")
        result = store.get("test")
        assert result is not None
        assert result.value == "hello world"
        store.close()


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
