"""Unit tests for tapps_brain.markdown_sync — durability and fsync helpers.

Covers: _fsync_parent_dir POSIX/Windows behaviour, directory fsync after every
os.replace call in _save_sync_state and sync_to_markdown.

Part of TAP-1809 (os.replace atomic write missing parent-dir fsync).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import tapps_brain.markdown_sync as md_sync
from tapps_brain.markdown_sync import (
    _fsync_parent_dir,
    _save_sync_state,
    sync_to_markdown,
)

# ---------------------------------------------------------------------------
# _fsync_parent_dir — POSIX path
# ---------------------------------------------------------------------------


class TestFsyncParentDirPosix:
    """Tests for _fsync_parent_dir on non-Windows platforms."""

    def test_opens_parent_dir_and_fsyncs(self, tmp_path: Path) -> None:
        """fsync is called on the parent directory fd after open."""
        target = tmp_path / "subdir" / "file.txt"
        target.parent.mkdir(parents=True)

        fake_fd = 99
        with (
            patch.object(md_sync.sys, "platform", "linux"),
            patch("tapps_brain.markdown_sync.os.open", return_value=fake_fd) as mock_open,
            patch("tapps_brain.markdown_sync.os.fsync") as mock_fsync,
            patch("tapps_brain.markdown_sync.os.close") as mock_close,
        ):
            _fsync_parent_dir(target)

        mock_open.assert_called_once_with(str(target.parent), os.O_RDONLY)
        mock_fsync.assert_called_once_with(fake_fd)
        mock_close.assert_called_once_with(fake_fd)

    def test_closes_fd_even_when_fsync_raises(self, tmp_path: Path) -> None:
        """os.close is called even if os.fsync raises OSError."""
        target = tmp_path / "file.txt"
        fake_fd = 77

        with (
            patch.object(md_sync.sys, "platform", "linux"),
            patch("tapps_brain.markdown_sync.os.open", return_value=fake_fd),
            patch("tapps_brain.markdown_sync.os.fsync", side_effect=OSError("disk error")),
            patch("tapps_brain.markdown_sync.os.close") as mock_close,
        ):
            # Should not raise — error is logged, not re-raised.
            _fsync_parent_dir(target)

        mock_close.assert_called_once_with(fake_fd)

    def test_oserror_on_open_does_not_propagate(self, tmp_path: Path) -> None:
        """An OSError opening the directory fd is caught and logged, not re-raised."""
        target = tmp_path / "file.txt"

        with (
            patch.object(md_sync.sys, "platform", "linux"),
            patch("tapps_brain.markdown_sync.os.open", side_effect=OSError("permission denied")),
        ):
            _fsync_parent_dir(target)  # must not raise


# ---------------------------------------------------------------------------
# _fsync_parent_dir — Windows path
# ---------------------------------------------------------------------------


class TestFsyncParentDirWindows:
    """Tests for _fsync_parent_dir on Windows (no-op with one-time warning)."""

    def setup_method(self) -> None:
        # Reset the module-level warning flag before each test.
        md_sync._FSYNC_PARENT_UNAVAILABLE_WARNED = False

    def test_noop_on_windows_no_exception(self, tmp_path: Path) -> None:
        """No exception is raised and os.fsync is never called on Windows."""
        target = tmp_path / "file.txt"

        with (
            patch.object(md_sync.sys, "platform", "win32"),
            patch("tapps_brain.markdown_sync.os.fsync") as mock_fsync,
        ):
            _fsync_parent_dir(target)

        mock_fsync.assert_not_called()

    def test_warning_emitted_only_once(self, tmp_path: Path) -> None:
        """The one-time-warning flag prevents duplicate log entries."""
        target = tmp_path / "file.txt"

        warning_calls: list[str] = []

        with (
            patch.object(md_sync.sys, "platform", "win32"),
            patch.object(
                md_sync.logger,
                "warning",
                side_effect=lambda *a, **kw: warning_calls.append(a[0]),
            ),
        ):
            _fsync_parent_dir(target)
            _fsync_parent_dir(target)
            _fsync_parent_dir(target)

        # Only one "unavailable" warning should have been logged.
        unavailable = [c for c in warning_calls if "fsync_parent_dir_unavailable" in c]
        assert len(unavailable) == 1


# ---------------------------------------------------------------------------
# _save_sync_state — fsync called after os.replace
# ---------------------------------------------------------------------------


class TestSaveSyncStateFsync:
    """Verify that _save_sync_state calls _fsync_parent_dir after os.replace."""

    def test_fsync_called_after_replace(self, tmp_path: Path) -> None:
        """_fsync_parent_dir is invoked with the state file path after os.replace."""
        fsync_calls: list[Path] = []

        with patch("tapps_brain.markdown_sync._fsync_parent_dir", side_effect=fsync_calls.append):
            _save_sync_state(tmp_path, {"version": 1, "key": "value"})

        assert len(fsync_calls) == 1
        assert fsync_calls[0].name == "sync_state.json"
        assert fsync_calls[0].parent.name == ".tapps-brain"

    def test_replace_called_before_fsync(self, tmp_path: Path) -> None:
        """os.replace happens strictly before _fsync_parent_dir (order check)."""
        call_order: list[str] = []

        original_replace = md_sync.os.replace

        def fake_replace(src: object, dst: object) -> None:
            call_order.append("replace")
            original_replace(src, dst)  # type: ignore[arg-type]

        with (
            patch("tapps_brain.markdown_sync.os.replace", side_effect=fake_replace),
            patch(
                "tapps_brain.markdown_sync._fsync_parent_dir",
                side_effect=lambda _: call_order.append("fsync"),
            ),
        ):
            _save_sync_state(tmp_path, {"version": 1})

        assert call_order == ["replace", "fsync"], f"Expected [replace, fsync], got {call_order}"


# ---------------------------------------------------------------------------
# sync_to_markdown — fsync called after both os.replace sites
# ---------------------------------------------------------------------------


class TestSyncToMarkdownFsync:
    """Verify that sync_to_markdown calls _fsync_parent_dir after every os.replace."""

    def test_fsync_called_for_memory_md(self, tmp_path: Path) -> None:
        """_fsync_parent_dir is called for the MEMORY.md rename in sync_to_markdown."""
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path / "store")
        workspace = tmp_path / "ws"
        workspace.mkdir()

        fsync_calls: list[Path] = []

        with patch("tapps_brain.markdown_sync._fsync_parent_dir", side_effect=fsync_calls.append):
            sync_to_markdown(store, workspace)

        memory_md_calls = [p for p in fsync_calls if p.name == "MEMORY.md"]
        assert memory_md_calls, f"Expected fsync for MEMORY.md; got {fsync_calls}"

    def test_fsync_called_for_state_file(self, tmp_path: Path) -> None:
        """_fsync_parent_dir is called for the sync_state.json rename."""
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path / "store")
        workspace = tmp_path / "ws"
        workspace.mkdir()

        fsync_calls: list[Path] = []

        with patch("tapps_brain.markdown_sync._fsync_parent_dir", side_effect=fsync_calls.append):
            sync_to_markdown(store, workspace)

        state_calls = [p for p in fsync_calls if p.name == "sync_state.json"]
        assert state_calls, f"Expected fsync for sync_state.json; got {fsync_calls}"

    def test_both_fsyncs_fire_per_sync(self, tmp_path: Path) -> None:
        """Every sync_to_markdown call fires exactly two _fsync_parent_dir calls."""
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path / "store")
        workspace = tmp_path / "ws"
        workspace.mkdir()

        fsync_calls: list[Path] = []

        with patch("tapps_brain.markdown_sync._fsync_parent_dir", side_effect=fsync_calls.append):
            sync_to_markdown(store, workspace)

        assert len(fsync_calls) == 2, (
            f"Expected exactly 2 fsync calls (MEMORY.md + sync_state.json); "
            f"got {len(fsync_calls)}: {fsync_calls}"
        )
