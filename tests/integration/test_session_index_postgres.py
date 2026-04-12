"""Postgres integration tests for SessionIndex — save_chunks / search / delete_expired.

STORY-066.13: Replaces deleted SQLite-coupled test_session_index and
test_session_index_integration test files with Postgres-backed equivalents.

Requires: ``TAPPS_BRAIN_DATABASE_URL`` environment variable (skipped otherwise).
Mark: ``requires_postgres``
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_session_index(project_id: str, agent_id: str) -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.session_index import SessionIndex

    cm = PostgresConnectionManager(_PG_DSN)
    return SessionIndex(cm, project_id=project_id, agent_id=agent_id)


def _unique_project() -> str:
    return f"test-proj-{uuid.uuid4().hex[:8]}"


def _unique_agent() -> str:
    return f"test-agent-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _migrate() -> None:
    _apply_migrations()


@pytest.fixture
def session_index(request: Any) -> Any:
    """SessionIndex scoped to unique (project_id, agent_id) per test."""
    si = _make_session_index(_unique_project(), _unique_agent())
    yield si


# ---------------------------------------------------------------------------
# save_chunks
# ---------------------------------------------------------------------------


class TestSaveChunks:
    def test_save_chunks_returns_count(self, session_index: Any) -> None:
        count = session_index.save_chunks("sess-001", ["chunk one", "chunk two", "chunk three"])
        assert count == 3

    def test_save_empty_chunks_returns_zero(self, session_index: Any) -> None:
        assert session_index.save_chunks("sess-empty", []) == 0

    def test_save_chunks_blank_only_returns_zero(self, session_index: Any) -> None:
        assert session_index.save_chunks("sess-blank", ["   ", "\t", ""]) == 0

    def test_save_chunks_empty_session_id_returns_zero(self, session_index: Any) -> None:
        assert session_index.save_chunks("", ["content"]) == 0

    def test_save_chunks_upsert_is_idempotent(self, session_index: Any) -> None:
        """Re-saving the same (session_id, chunk_index) must not duplicate rows."""
        session_index.save_chunks("sess-idem", ["first version"])
        session_index.save_chunks("sess-idem", ["second version"])
        results = session_index.search("second")
        assert len(results) == 1
        assert results[0]["content"] == "second version"

    def test_save_chunks_respects_max_chunks_limit(self, session_index: Any) -> None:
        chunks = [f"chunk {i}" for i in range(10)]
        count = session_index.save_chunks("sess-limit", chunks, max_chunks=3)
        assert count == 3

    def test_save_chunks_truncates_at_max_chars(self, session_index: Any) -> None:
        long_chunk = "a" * 600
        count = session_index.save_chunks(
            "sess-truncate", [long_chunk], max_chars_per_chunk=50
        )
        assert count == 1
        # Verify the stored content is truncated
        results = session_index.search("aaaa")
        assert len(results) == 1
        assert len(results[0]["content"]) <= 50


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_finds_saved_chunk(self, session_index: Any) -> None:
        session_index.save_chunks("sess-search-1", ["elasticsearch is a distributed search engine"])
        results = session_index.search("distributed search")
        assert len(results) >= 1
        assert any(r["session_id"] == "sess-search-1" for r in results)

    def test_search_empty_query_returns_empty(self, session_index: Any) -> None:
        session_index.save_chunks("sess-search-2", ["content for empty query test"])
        assert session_index.search("") == []
        assert session_index.search("   ") == []

    def test_search_no_match_returns_empty(self, session_index: Any) -> None:
        session_index.save_chunks("sess-search-3", ["typical memory content words"])
        assert session_index.search("xyzzy") == []

    def test_search_result_structure(self, session_index: Any) -> None:
        session_index.save_chunks("sess-struct", ["structured content for key check"])
        results = session_index.search("structured content")
        assert len(results) >= 1
        r = results[0]
        assert "session_id" in r
        assert "chunk_index" in r
        assert "content" in r
        assert "created_at" in r

    def test_search_scoped_per_agent(self) -> None:
        """One agent's chunks must not appear in another agent's search."""
        project_id = _unique_project()
        agent_a = _make_session_index(project_id, "agent-a")
        agent_b = _make_session_index(project_id, "agent-b")

        agent_a.save_chunks("sess-a", ["canary phrase only in agent-a"])
        results_b = agent_b.search("canary phrase")
        assert results_b == []

        results_a = agent_a.search("canary phrase")
        assert len(results_a) >= 1


# ---------------------------------------------------------------------------
# delete_expired
# ---------------------------------------------------------------------------


class TestDeleteExpired:
    def test_delete_expired_zero_ttl_returns_zero(self, session_index: Any) -> None:
        session_index.save_chunks("sess-exp-0", ["content"])
        deleted = session_index.delete_expired(0)
        assert deleted == 0

    def test_delete_expired_large_ttl_keeps_recent_chunks(self, session_index: Any) -> None:
        session_index.save_chunks("sess-keep", ["keep this chunk"])
        # TTL of 1000 days — nothing should be expired
        deleted = session_index.delete_expired(1000)
        assert deleted == 0
        # Chunk is still searchable
        results = session_index.search("keep this chunk")
        assert len(results) >= 1

    def test_delete_expired_removes_old_chunks_via_sql(self) -> None:
        """Insert old rows directly, then verify delete_expired removes them."""
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.session_index import SessionIndex

        cm = PostgresConnectionManager(_PG_DSN)
        project_id = _unique_project()
        agent_id = _unique_agent()
        si = SessionIndex(cm, project_id=project_id, agent_id=agent_id)

        # Save a chunk normally first
        si.save_chunks("sess-old", ["old chunk content"])

        # Back-date the created_at to 100 days ago via direct SQL
        with cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE session_chunks SET created_at = now() - interval '100 days' "
                "WHERE project_id = %s AND agent_id = %s AND session_id = %s",
                (project_id, agent_id, "sess-old"),
            )

        deleted = si.delete_expired(30)
        assert deleted == 1
        # Must not be searchable anymore
        assert si.search("old chunk content") == []
