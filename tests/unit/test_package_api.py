"""Tests for tapps_brain public API surface (EPIC-009, STORY-009.2)."""

from __future__ import annotations

from pathlib import Path

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
