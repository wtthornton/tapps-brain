"""Unit tests for graceful SQLite corruption handling (story-014.3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tapps_brain.persistence import MemoryPersistence
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    pass


def _write_corrupt_db(db_path: Path) -> None:
    """Write garbage bytes to the given path to simulate a corrupt SQLite file."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"NOT A VALID SQLITE DATABASE FILE - corrupt content here")


class TestSQLiteCorruptionHandling:
    """Tests for graceful handling of corrupt SQLite databases."""

    def test_memory_persistence_raises_on_corrupt_db(self, tmp_path: Path) -> None:
        """MemoryPersistence.__init__ raises sqlite3.DatabaseError on corrupt DB."""
        db_path = tmp_path / ".tapps-brain" / "memory" / "memory.db"
        _write_corrupt_db(db_path)

        with pytest.raises(sqlite3.DatabaseError):
            MemoryPersistence(tmp_path)

    def test_memory_persistence_logs_error_on_corrupt_db(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """MemoryPersistence logs an actionable error message for corrupt DB."""
        import logging

        db_path = tmp_path / ".tapps-brain" / "memory" / "memory.db"
        _write_corrupt_db(db_path)

        with caplog.at_level(logging.ERROR), pytest.raises(sqlite3.DatabaseError):
            MemoryPersistence(tmp_path)

        # The structured log event should contain the corruption message
        assert any(
            "database_corrupt" in record.message or "database_corrupt" in str(record.__dict__)
            for record in caplog.records
        )

    def test_memory_store_raises_on_corrupt_db(self, tmp_path: Path) -> None:
        """MemoryStore.__init__ raises sqlite3.DatabaseError on corrupt DB."""
        db_path = tmp_path / ".tapps-brain" / "memory" / "memory.db"
        _write_corrupt_db(db_path)

        with pytest.raises(sqlite3.DatabaseError):
            MemoryStore(tmp_path)

    def test_memory_store_custom_store_dir_raises_on_corrupt_db(
        self, tmp_path: Path
    ) -> None:
        """MemoryStore raises sqlite3.DatabaseError for custom store_dir corrupt DB."""
        db_path = tmp_path / ".tapps-mcp" / "memory" / "memory.db"
        _write_corrupt_db(db_path)

        with pytest.raises(sqlite3.DatabaseError):
            MemoryStore(tmp_path, store_dir=".tapps-mcp")

    def test_memory_persistence_clean_db_succeeds(self, tmp_path: Path) -> None:
        """MemoryPersistence initializes normally when no DB file exists yet."""
        p = MemoryPersistence(tmp_path)
        assert (tmp_path / ".tapps-brain" / "memory" / "memory.db").exists()

    def test_memory_store_clean_db_succeeds(self, tmp_path: Path) -> None:
        """MemoryStore initializes normally when no DB file exists yet."""
        s = MemoryStore(tmp_path)
        s.close()
