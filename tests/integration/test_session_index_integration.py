"""Integration tests for session indexing via MemoryStore.

Uses real MemoryStore (no mocks), real SQLite/FTS5, and real session_index
module. All databases use tmp_path for isolation.

Story: STORY-002.4 from EPIC-002
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    """Create a real MemoryStore backed by SQLite in a temp directory."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIndexSession:
    """Tests for MemoryStore.index_session()."""

    def test_index_three_chunks(self, store: MemoryStore) -> None:
        """Index a session with 3 chunks and verify all 3 are stored."""
        chunks = [
            "Refactored the authentication middleware",
            "Added rate limiting to API endpoints",
            "Updated the database schema for users table",
        ]
        count = store.index_session("session-abc", chunks)
        assert count == 3

    def test_index_empty_chunks(self, store: MemoryStore) -> None:
        """Index with empty chunk list returns 0."""
        count = store.index_session("session-empty", [])
        assert count == 0

    def test_index_session_empty_id(self, store: MemoryStore) -> None:
        """Index with empty session ID returns 0."""
        count = store.index_session("", ["some content"])
        assert count == 0


class TestSearchSessions:
    """Tests for MemoryStore.search_sessions()."""

    def test_search_finds_matching_session(self, store: MemoryStore) -> None:
        """Index 3 sessions with distinct content, search for a unique term."""
        store.index_session("sess-alpha", ["quantum computing breakthroughs in 2025"])
        store.index_session("sess-beta", ["gardening tips for growing tomatoes"])
        store.index_session("sess-gamma", ["best practices for Python testing"])

        results = store.search_sessions("quantum")
        assert len(results) >= 1
        session_ids = [r["session_id"] for r in results]
        assert "sess-alpha" in session_ids
        assert "sess-beta" not in session_ids
        assert "sess-gamma" not in session_ids

    def test_search_no_match_returns_empty(self, store: MemoryStore) -> None:
        """Search with no matching content returns an empty list."""
        store.index_session("sess-one", ["information about databases"])
        store.index_session("sess-two", ["network protocol design"])

        results = store.search_sessions("xylophone")
        assert results == []

    def test_search_empty_index_returns_empty(self, store: MemoryStore) -> None:
        """Search on an empty session index returns an empty list."""
        results = store.search_sessions("anything")
        assert results == []

    def test_search_empty_query_returns_empty(self, store: MemoryStore) -> None:
        """Search with empty query returns an empty list."""
        store.index_session("sess-q", ["some indexed content"])
        results = store.search_sessions("")
        assert results == []

    def test_search_returns_expected_keys(self, store: MemoryStore) -> None:
        """Verify result dicts contain required keys."""
        store.index_session("sess-keys", ["testing search result structure"])
        results = store.search_sessions("testing")
        assert len(results) >= 1
        result = results[0]
        assert "session_id" in result
        assert "chunk_index" in result
        assert "content" in result
        assert "created_at" in result

    def test_search_respects_limit(self, store: MemoryStore) -> None:
        """Search respects the limit parameter."""
        for i in range(5):
            store.index_session(f"sess-lim-{i}", [f"common keyword chunk {i}"])

        results = store.search_sessions("common", limit=2)
        assert len(results) <= 2


class TestCleanupSessions:
    """Tests for MemoryStore.cleanup_sessions()."""

    def test_cleanup_deletes_old_sessions(self, store: MemoryStore, tmp_path: Path) -> None:
        """Index sessions, backdate them in the DB, then cleanup with ttl_days=1."""
        store.index_session("sess-old", ["old session data about networks"])
        store.index_session("sess-new", ["new session data about networks"])

        # Backdate sess-old to 100 days ago by directly manipulating the DB.
        db_path = tmp_path / ".tapps-brain" / "memory" / "memory.db"
        assert db_path.exists(), f"DB not found at {db_path}"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            UPDATE session_index
            SET created_at = datetime('now', '-100 days')
            WHERE session_id = 'sess-old'
            """
        )
        conn.commit()
        conn.close()

        deleted = store.cleanup_sessions(ttl_days=1)
        assert deleted >= 1

        # Verify the old session is gone but the new one remains.
        results = store.search_sessions("networks")
        session_ids = [r["session_id"] for r in results]
        assert "sess-old" not in session_ids
        assert "sess-new" in session_ids

    def test_cleanup_nothing_to_delete(self, store: MemoryStore) -> None:
        """Cleanup with no expired sessions returns 0."""
        store.index_session("sess-fresh", ["fresh session content"])
        deleted = store.cleanup_sessions(ttl_days=90)
        assert deleted == 0

    def test_cleanup_empty_index(self, store: MemoryStore) -> None:
        """Cleanup on empty session index returns 0."""
        deleted = store.cleanup_sessions(ttl_days=90)
        assert deleted == 0


class TestErrorHandling:
    """Session methods should not raise even when things go wrong."""

    def test_index_session_does_not_raise_on_error(self, store: MemoryStore) -> None:
        """index_session returns 0 on internal failure, does not raise."""
        with patch(
            "tapps_brain.session_index.index_session",
            side_effect=RuntimeError("boom"),
        ):
            result = store.index_session("sess-err", ["chunk"])
        assert result == 0

    def test_search_sessions_does_not_raise_on_error(self, store: MemoryStore) -> None:
        """search_sessions returns [] on internal failure, does not raise."""
        with patch(
            "tapps_brain.session_index.search_session_index",
            side_effect=RuntimeError("boom"),
        ):
            result = store.search_sessions("query")
        assert result == []

    def test_cleanup_sessions_does_not_raise_on_error(self, store: MemoryStore) -> None:
        """cleanup_sessions returns 0 on internal failure, does not raise."""
        with patch(
            "tapps_brain.session_index.delete_expired_sessions",
            side_effect=RuntimeError("boom"),
        ):
            result = store.cleanup_sessions(ttl_days=30)
        assert result == 0
