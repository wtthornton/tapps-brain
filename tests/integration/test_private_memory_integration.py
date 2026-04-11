"""Integration tests for PostgresPrivateBackend — round-trip save/recall.

EPIC-059 STORY-059.5 acceptance criteria:
- Round-trip save/recall with N entries.
- Tenant isolation: two agents in the same project cannot read each other's data.
- FTS search via search_vector tsvector index.
- No ``.tapps-brain/agents/<id>/memory.db`` created when Postgres backend is active.

Requires: ``TAPPS_TEST_POSTGRES_DSN`` environment variable (skipped otherwise).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from tapps_brain._protocols import PrivateBackend

# ---------------------------------------------------------------------------
# Skip guard — all tests require a live Postgres instance
# ---------------------------------------------------------------------------

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN

pytestmark = pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _apply_migrations() -> None:
    """Apply private-memory migrations to the test DB (idempotent)."""
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_backend(project_id: str, agent_id: str) -> Any:
    """Return a ``PostgresPrivateBackend`` connected to the test DB."""
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_private import PostgresPrivateBackend

    cm = PostgresConnectionManager(_PG_DSN)
    return PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)


def _unique_project() -> str:
    """Generate a unique project_id for test isolation."""
    return f"test-proj-{uuid.uuid4().hex[:8]}"


def _unique_agent() -> str:
    """Generate a unique agent_id for test isolation."""
    return f"test-agent-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module", autouse=True)
def _migrate() -> None:
    """Apply migrations once per module."""
    _apply_migrations()


@pytest.fixture
def backend(request: Any) -> Any:
    """Scoped backend fixture — project_id and agent_id unique per test."""
    project_id = _unique_project()
    agent_id = _unique_agent()
    b = _make_backend(project_id, agent_id)
    yield b
    b.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(key: str, value: str, **kwargs: Any) -> Any:
    from tapps_brain.models import MemoryEntry

    return MemoryEntry(key=key, value=value, **kwargs)


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_is_private_backend(self, backend: Any) -> None:
        assert isinstance(backend, PrivateBackend)


# ---------------------------------------------------------------------------
# Round-trip: save → load_all
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Verify save/load_all semantics with a real Postgres DB."""

    def test_save_and_load_single_entry(self, backend: Any) -> None:
        entry = _make_entry("rt-single", "hello from postgres")
        backend.save(entry)
        loaded = backend.load_all()
        assert len(loaded) == 1
        assert loaded[0].key == "rt-single"
        assert loaded[0].value == "hello from postgres"

    def test_round_trip_n_entries(self, backend: Any) -> None:
        """Save N entries and verify all are loaded back correctly."""
        n = 10
        keys = [f"rt-key-{i:02d}" for i in range(n)]
        for k in keys:
            backend.save(_make_entry(k, f"value for {k}"))

        loaded = backend.load_all()
        loaded_keys = {e.key for e in loaded}
        assert len(loaded) == n
        for k in keys:
            assert k in loaded_keys

    def test_upsert_updates_value(self, backend: Any) -> None:
        """Saving the same key twice should update (not duplicate)."""
        entry_v1 = _make_entry("upsert-key", "version 1")
        entry_v2 = _make_entry("upsert-key", "version 2")
        backend.save(entry_v1)
        backend.save(entry_v2)
        loaded = backend.load_all()
        assert len(loaded) == 1
        assert loaded[0].value == "version 2"

    def test_load_all_empty_initially(self, backend: Any) -> None:
        assert backend.load_all() == []

    def test_confidence_preserved(self, backend: Any) -> None:
        entry = _make_entry("conf-key", "confidence test", confidence=0.92)
        backend.save(entry)
        loaded = backend.load_all()
        assert abs(loaded[0].confidence - 0.92) < 1e-5

    def test_tags_preserved(self, backend: Any) -> None:
        entry = _make_entry("tag-key", "tagged memory", tags=["alpha", "beta"])
        backend.save(entry)
        loaded = backend.load_all()
        assert set(loaded[0].tags) == {"alpha", "beta"}

    def test_tier_preserved(self, backend: Any) -> None:
        from tapps_brain.models import MemoryTier

        entry = _make_entry("tier-key", "architectural memory", tier=MemoryTier.ARCHITECTURAL)
        backend.save(entry)
        loaded = backend.load_all()
        assert loaded[0].tier == MemoryTier.ARCHITECTURAL


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_returns_true_for_existing_key(self, backend: Any) -> None:
        backend.save(_make_entry("del-key", "to be deleted"))
        result = backend.delete("del-key")
        assert result is True

    def test_delete_removes_entry_from_load_all(self, backend: Any) -> None:
        backend.save(_make_entry("del-check", "will be gone"))
        backend.delete("del-check")
        loaded = {e.key for e in backend.load_all()}
        assert "del-check" not in loaded

    def test_delete_returns_false_for_missing_key(self, backend: Any) -> None:
        assert backend.delete("nonexistent-key") is False


# ---------------------------------------------------------------------------
# Full-text search
# ---------------------------------------------------------------------------


class TestFTSSearch:
    """Verify the tsvector search index is functional."""

    def test_search_finds_saved_entry(self, backend: Any) -> None:
        backend.save(_make_entry("fts-1", "postgres is a powerful database system"))
        results = backend.search("powerful database")
        keys = [e.key for e in results]
        assert "fts-1" in keys

    def test_search_empty_query_returns_empty(self, backend: Any) -> None:
        backend.save(_make_entry("fts-2", "some content here"))
        assert backend.search("   ") == []

    def test_search_with_n_entries_returns_relevant(self, backend: Any) -> None:
        """Search in a pool of N entries returns only the matching one."""
        # Seed 5 entries; only one contains "saffron"
        entries = [
            _make_entry("fts-pool-0", "memory about python programming"),
            _make_entry("fts-pool-1", "notes on database indexing"),
            _make_entry("fts-pool-2", "saffron spice is expensive and aromatic"),
            _make_entry("fts-pool-3", "agent memory recall techniques"),
            _make_entry("fts-pool-4", "vector embeddings for similarity search"),
        ]
        for e in entries:
            backend.save(e)

        results = backend.search("saffron")
        assert len(results) >= 1
        result_keys = [e.key for e in results]
        assert "fts-pool-2" in result_keys

    def test_search_no_match_returns_empty(self, backend: Any) -> None:
        backend.save(_make_entry("fts-nomatch", "ordinary content with common words"))
        # "xyzzy" is absent
        assert backend.search("xyzzy") == []

    def test_search_with_memory_group_filter(self, backend: Any) -> None:
        from tapps_brain.models import MemoryEntry

        backend.save(MemoryEntry(key="grp-a", value="filtered by group alpha", memory_group="alpha"))
        backend.save(MemoryEntry(key="grp-b", value="filtered by group beta", memory_group="beta"))
        results = backend.search("filtered", memory_group="alpha")
        keys = [e.key for e in results]
        assert "grp-a" in keys
        assert "grp-b" not in keys


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    """Two agents in the same project cannot read each other's data.

    Two agents sharing a project_id must have disjoint views of ``load_all()``
    and ``search()``.
    """

    def test_agents_see_only_own_entries(self) -> None:
        project_id = _unique_project()
        agent_a = _make_backend(project_id, "agent-a")
        agent_b = _make_backend(project_id, "agent-b")
        try:
            agent_a.save(_make_entry("iso-key-a", "agent-a secret"))
            agent_b.save(_make_entry("iso-key-b", "agent-b secret"))

            a_keys = {e.key for e in agent_a.load_all()}
            b_keys = {e.key for e in agent_b.load_all()}

            assert "iso-key-a" in a_keys
            assert "iso-key-b" not in a_keys

            assert "iso-key-b" in b_keys
            assert "iso-key-a" not in b_keys
        finally:
            agent_a.close()
            agent_b.close()

    def test_search_is_scoped_per_agent(self) -> None:
        project_id = _unique_project()
        agent_a = _make_backend(project_id, "agent-search-a")
        agent_b = _make_backend(project_id, "agent-search-b")
        try:
            agent_a.save(_make_entry("sa-key", "canary information for agent-a"))
            agent_b.save(_make_entry("sb-key", "some other content for agent-b"))

            # Search in agent_b should not find agent_a's canary
            results_b = agent_b.search("canary")
            keys_b = [e.key for e in results_b]
            assert "sa-key" not in keys_b

            # Search in agent_a should find it
            results_a = agent_a.search("canary")
            keys_a = [e.key for e in results_a]
            assert "sa-key" in keys_a
        finally:
            agent_a.close()
            agent_b.close()

    def test_projects_isolated_from_each_other(self) -> None:
        project_x = _unique_project()
        project_y = _unique_project()
        bx = _make_backend(project_x, "same-agent")
        by = _make_backend(project_y, "same-agent")
        try:
            bx.save(_make_entry("cross-x", "project x memory"))
            by.save(_make_entry("cross-y", "project y memory"))

            x_keys = {e.key for e in bx.load_all()}
            y_keys = {e.key for e in by.load_all()}

            assert "cross-x" in x_keys
            assert "cross-y" not in x_keys
            assert "cross-y" in y_keys
            assert "cross-x" not in y_keys
        finally:
            bx.close()
            by.close()


# ---------------------------------------------------------------------------
# No SQLite files created
# ---------------------------------------------------------------------------


class TestNoSQLiteFiles:
    """Verify that using PostgresPrivateBackend does not create memory.db files."""

    def test_no_memory_db_in_tmp_path(self, tmp_path: Path) -> None:
        """MemoryStore with Postgres backend must not create any .db files."""
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.postgres_private import PostgresPrivateBackend
        from tapps_brain.store import MemoryStore

        cm = PostgresConnectionManager(_PG_DSN)
        backend = PostgresPrivateBackend(
            cm, project_id=_unique_project(), agent_id=_unique_agent()
        )
        try:
            _apply_migrations()
            store = MemoryStore(tmp_path, private_backend=backend)
            # Trigger a save to verify write path doesn't create SQLite files
            store.save("no-sqlite-key", "no sqlite should exist")
            db_files = list(tmp_path.rglob("*.db"))
            assert db_files == [], f"Unexpected .db files found: {db_files}"
        finally:
            backend.close()


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_schema_version_is_positive_int(self, backend: Any) -> None:
        version = backend.get_schema_version()
        assert isinstance(version, int)
        assert version >= 1
