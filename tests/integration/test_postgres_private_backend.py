"""Postgres integration tests for PostgresPrivateBackend — CRUD round-trip.

STORY-066.13: Replaces deleted SQLite-coupled test_memory_persistence and
test_memory_foundation_integration test files with Postgres-backed equivalents.

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
    """Apply private-memory migrations to the test DB (idempotent)."""
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_backend(project_id: str, agent_id: str) -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_private import PostgresPrivateBackend

    cm = PostgresConnectionManager(_PG_DSN)
    return PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)


def _unique_project() -> str:
    return f"test-proj-{uuid.uuid4().hex[:8]}"


def _unique_agent() -> str:
    return f"test-agent-{uuid.uuid4().hex[:8]}"


def _make_entry(key: str, value: str, **kwargs: Any) -> Any:
    from tapps_brain.models import MemoryEntry

    return MemoryEntry(key=key, value=value, **kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _migrate() -> None:
    """Apply migrations once per module."""
    _apply_migrations()


@pytest.fixture
def backend(request: Any) -> Any:
    """Scoped backend — unique project_id and agent_id per test."""
    project_id = _unique_project()
    agent_id = _unique_agent()
    b = _make_backend(project_id, agent_id)
    yield b
    b.close()


# ---------------------------------------------------------------------------
# Save / load_all round-trip
# ---------------------------------------------------------------------------


class TestSaveLoadAll:
    """Verify basic save → load_all semantics with a real Postgres database."""

    def test_load_all_empty_initially(self, backend: Any) -> None:
        assert backend.load_all() == []

    def test_save_and_load_single_entry(self, backend: Any) -> None:
        backend.save(_make_entry("pg-single", "hello from postgres"))
        loaded = backend.load_all()
        assert len(loaded) == 1
        assert loaded[0].key == "pg-single"
        assert loaded[0].value == "hello from postgres"

    def test_round_trip_multiple_entries(self, backend: Any) -> None:
        n = 5
        keys = [f"pg-key-{i:02d}" for i in range(n)]
        for k in keys:
            backend.save(_make_entry(k, f"value for {k}"))
        loaded = backend.load_all()
        loaded_keys = {e.key for e in loaded}
        assert len(loaded) == n
        for k in keys:
            assert k in loaded_keys

    def test_upsert_updates_existing_value(self, backend: Any) -> None:
        backend.save(_make_entry("upsert-k", "version 1"))
        backend.save(_make_entry("upsert-k", "version 2"))
        loaded = backend.load_all()
        assert len(loaded) == 1
        assert loaded[0].value == "version 2"

    def test_confidence_round_trip(self, backend: Any) -> None:
        backend.save(_make_entry("conf-k", "confidence test", confidence=0.87))
        loaded = backend.load_all()
        assert abs(loaded[0].confidence - 0.87) < 1e-4

    def test_tags_round_trip(self, backend: Any) -> None:
        backend.save(_make_entry("tag-k", "tagged entry", tags=["alpha", "beta"]))
        loaded = backend.load_all()
        assert set(loaded[0].tags) == {"alpha", "beta"}

    def test_tier_round_trip(self, backend: Any) -> None:
        from tapps_brain.models import MemoryTier

        backend.save(_make_entry("tier-k", "architectural fact", tier=MemoryTier.ARCHITECTURAL))
        loaded = backend.load_all()
        assert loaded[0].tier == MemoryTier.ARCHITECTURAL


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_existing_key_returns_true(self, backend: Any) -> None:
        backend.save(_make_entry("del-k", "to delete"))
        assert backend.delete("del-k") is True

    def test_deleted_entry_absent_from_load_all(self, backend: Any) -> None:
        backend.save(_make_entry("del-check", "will be removed"))
        backend.delete("del-check")
        assert "del-check" not in {e.key for e in backend.load_all()}

    def test_delete_missing_key_returns_false(self, backend: Any) -> None:
        assert backend.delete("no-such-key") is False


# ---------------------------------------------------------------------------
# Full-text search
# ---------------------------------------------------------------------------


class TestSearch:
    """Verify the tsvector search index is functional."""

    def test_search_finds_matching_entry(self, backend: Any) -> None:
        backend.save(_make_entry("fts-k1", "postgres is a powerful relational database"))
        results = backend.search("powerful relational")
        assert any(e.key == "fts-k1" for e in results)

    def test_search_empty_query_returns_empty(self, backend: Any) -> None:
        backend.save(_make_entry("fts-k2", "some content here"))
        assert backend.search("   ") == []

    def test_search_no_match_returns_empty(self, backend: Any) -> None:
        backend.save(_make_entry("fts-k3", "ordinary unremarkable content"))
        assert backend.search("xyzzy") == []

    def test_search_scoped_to_agent(self, backend: Any) -> None:
        """Search must not return another agent's matching entry."""
        project_id = _unique_project()
        other = _make_backend(project_id, _unique_agent())
        try:
            other.save(_make_entry("other-entry", "canary for other agent"))
            # Our backend (different agent on different project) must not see it
            results = backend.search("canary")
            assert not any(e.key == "other-entry" for e in results)
        finally:
            other.close()
