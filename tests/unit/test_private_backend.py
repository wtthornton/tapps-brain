"""Unit tests for PostgresPrivateBackend and factory functions.

EPIC-059 STORY-059.5 — Postgres-backed private memory wiring.

All DB interactions are mocked; no real Postgres connection is required.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain._protocols import PrivateBackend
from tapps_brain.backends import create_private_backend, derive_project_id

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class _FakeCM:
    """Minimal context-manager stub that returns *obj* on ``__enter__``."""

    def __init__(self, obj: Any) -> None:
        self._obj = obj

    def __enter__(self) -> Any:
        return self._obj

    def __exit__(self, *args: Any) -> bool:
        return False


def _make_mocks(
    rows: list[Any] | None = None,
    col_names: list[str] | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Build (connection_manager, connection, cursor, _) mocks.

    Returns ``(cm, conn, cur, _)`` so callers can inspect the cursor directly.
    """
    cur = MagicMock()
    cur.fetchall.return_value = rows or []
    cur.fetchone.return_value = (0,)
    cur.rowcount = 1
    cur.description = [(name,) for name in (col_names or [])]

    conn = MagicMock()
    conn.cursor.return_value = _FakeCM(cur)

    cm = MagicMock()
    cm.get_connection.return_value = _FakeCM(conn)
    # EPIC-069 STORY-069.8: project_context is the tenant-scoped path; route
    # it to the same fake connection so tests exercising RLS-wired code paths
    # still see the configured cursor / rows.
    cm.project_context.return_value = _FakeCM(conn)
    cm.admin_context.return_value = _FakeCM(conn)
    return cm, conn, cur, conn  # last element unused; kept for symmetry


def _make_backend(
    rows: list[Any] | None = None,
    col_names: list[str] | None = None,
) -> tuple[Any, MagicMock]:
    """Return ``(PostgresPrivateBackend, cursor_mock)``."""
    from tapps_brain.postgres_private import PostgresPrivateBackend

    cm, _conn, cur, _ = _make_mocks(rows=rows, col_names=col_names)
    backend = PostgresPrivateBackend(cm, project_id="proj-abc", agent_id="agent-1")
    return backend, cur


def _minimal_entry() -> Any:
    from tapps_brain.models import MemoryEntry

    return MemoryEntry(key="test-key", value="test value")


# ---------------------------------------------------------------------------
# Factory: create_private_backend
# ---------------------------------------------------------------------------


class TestCreatePrivateBackend:
    def test_rejects_empty_dsn(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_private_backend("", project_id="p", agent_id="a")

    def test_rejects_whitespace(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_private_backend("   ", project_id="p", agent_id="a")

    def test_rejects_sqlite_path(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_private_backend("/tmp/memory.db", project_id="p", agent_id="a")

    def test_rejects_mysql_dsn(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_private_backend("mysql://localhost/brain", project_id="p", agent_id="a")

    def test_postgres_prefix_accepted(self) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend

        backend = create_private_backend("postgres://localhost/brain", project_id="p", agent_id="a")
        try:
            assert isinstance(backend, PostgresPrivateBackend)
            assert isinstance(backend, PrivateBackend)
        finally:
            backend.close()

    def test_postgresql_prefix_accepted(self) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend

        backend = create_private_backend(
            "postgresql://localhost/brain", project_id="p", agent_id="a"
        )
        try:
            assert isinstance(backend, PostgresPrivateBackend)
        finally:
            backend.close()


# ---------------------------------------------------------------------------
# derive_project_id
# ---------------------------------------------------------------------------


class TestDeriveProjectId:
    def test_returns_16_hex_chars(self, tmp_path: Path) -> None:
        pid = derive_project_id(tmp_path)
        assert len(pid) == 16
        assert all(c in "0123456789abcdef" for c in pid)

    def test_stable_for_same_path(self, tmp_path: Path) -> None:
        assert derive_project_id(tmp_path) == derive_project_id(tmp_path)

    def test_different_paths_differ(self, tmp_path: Path) -> None:
        other = tmp_path / "sub"
        other.mkdir()
        assert derive_project_id(tmp_path) != derive_project_id(other)

    def test_accepts_string(self, tmp_path: Path) -> None:
        assert derive_project_id(str(tmp_path)) == derive_project_id(tmp_path)


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestPrivateBackendProtocol:
    """Verify PostgresPrivateBackend satisfies the PrivateBackend protocol."""

    def test_isinstance_private_backend(self) -> None:
        backend, _ = _make_backend()
        assert isinstance(backend, PrivateBackend)

    def test_sentinel_paths_are_path_objects(self) -> None:
        backend, _ = _make_backend()
        assert isinstance(backend.db_path, Path)
        assert isinstance(backend.store_dir, Path)
        assert isinstance(backend.audit_path, Path)

    def test_encryption_key_is_none(self) -> None:
        backend, _ = _make_backend()
        assert backend.encryption_key is None

    def test_get_schema_version_returns_int(self) -> None:
        backend, _ = _make_backend()
        # Falls back to _PRIVATE_SCHEMA_VERSION (1) when query returns (0,)
        result = backend.get_schema_version()
        assert isinstance(result, int)

    def test_vector_row_count_returns_int(self) -> None:
        backend, _ = _make_backend()
        result = backend.vector_row_count()
        assert isinstance(result, int)

    def test_knn_search_empty_for_empty_embedding(self) -> None:
        backend, _ = _make_backend()
        assert backend.knn_search([], k=5) == []

    def test_append_audit_is_noop_no_raise(self) -> None:
        backend, _ = _make_backend()
        backend.append_audit("test_action", "test-key", {"foo": "bar"})

    def test_count_relations_returns_int(self) -> None:
        backend, _ = _make_backend()
        with patch.object(backend, "_ensure_relations_table"):
            result = backend.count_relations()
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


class TestSave:
    def test_save_calls_execute(self) -> None:
        backend, cur = _make_backend()
        entry = _minimal_entry()
        backend.save(entry)
        assert cur.execute.called

    def test_save_passes_project_and_agent_id(self) -> None:
        backend, cur = _make_backend()
        backend.save(_minimal_entry())
        params = cur.execute.call_args[0][1]
        assert params[0] == "proj-abc"  # project_id
        assert params[1] == "agent-1"  # agent_id
        assert params[2] == "test-key"  # key

    def test_save_encodes_tags_as_json(self) -> None:
        from tapps_brain.models import MemoryEntry

        backend, cur = _make_backend()
        entry = MemoryEntry(key="k", value="v", tags=["foo", "bar"])
        backend.save(entry)
        params = cur.execute.call_args[0][1]
        tags_json = params[11]  # tags_json at index 11
        assert json.loads(tags_json) == ["foo", "bar"]


class TestLoadAll:
    def test_load_all_returns_empty_list_when_no_rows(self) -> None:
        backend, _ = _make_backend()
        assert backend.load_all() == []

    def test_load_all_converts_rows(self) -> None:
        col_names = [
            "project_id",
            "agent_id",
            "key",
            "value",
            "tier",
            "confidence",
            "source",
            "source_agent",
            "scope",
            "agent_scope",
            "memory_group",
            "tags",
            "created_at",
            "updated_at",
            "last_accessed",
            "access_count",
            "useful_access_count",
            "total_access_count",
            "branch",
            "last_reinforced",
            "reinforce_count",
            "contradicted",
            "contradiction_reason",
            "seeded_from",
            "valid_at",
            "invalid_at",
            "superseded_by",
            "valid_from",
            "valid_until",
            "source_session_id",
            "source_channel",
            "source_message_id",
            "triggered_by",
            "stability",
            "difficulty",
            "positive_feedback_count",
            "negative_feedback_count",
            "integrity_hash",
            "embedding_model_id",
            "search_vector",
            "embedding",
        ]
        now = datetime.now(tz=UTC)
        row = (
            "proj-abc",
            "agent-1",
            "my-key",
            "my value",
            "pattern",
            0.8,
            "agent",
            "test-agent",
            "project",
            "private",
            None,
            ["tag1"],
            now,
            now,
            now,
            1,
            0,
            0,
            None,
            None,
            0,
            False,
            None,
            None,
            None,
            None,
            None,
            "",
            "",
            "",
            "",
            "",
            "",
            0.0,
            0.0,
            0.0,
            0.0,
            None,
            None,
            None,
            None,  # search_vector, embedding
        )
        backend, _ = _make_backend(rows=[row], col_names=col_names)
        entries = backend.load_all()
        assert len(entries) == 1
        e = entries[0]
        assert e.key == "my-key"
        assert e.value == "my value"
        assert e.confidence == 0.8
        assert "tag1" in e.tags


class TestDelete:
    def test_delete_returns_true_when_row_deleted(self) -> None:
        backend, cur = _make_backend()
        cur.rowcount = 1
        assert backend.delete("some-key") is True

    def test_delete_returns_false_when_no_row(self) -> None:
        backend, cur = _make_backend()
        cur.rowcount = 0
        assert backend.delete("missing-key") is False

    def test_delete_passes_project_agent_key(self) -> None:
        backend, cur = _make_backend()
        backend.delete("my-key")
        params = cur.execute.call_args[0][1]
        assert params == ("proj-abc", "agent-1", "my-key")


class TestSearch:
    def test_empty_query_returns_empty(self) -> None:
        backend, _ = _make_backend()
        assert backend.search("   ") == []

    def test_invalid_time_field_raises(self) -> None:
        backend, _ = _make_backend()
        with pytest.raises(ValueError, match="time_field"):
            backend.search("something", time_field="invalid_col")

    def test_search_includes_plainto_tsquery(self) -> None:
        backend, cur = _make_backend(rows=[], col_names=[])
        backend.search("test query")
        assert cur.execute.called
        sql = cur.execute.call_args[0][0]
        assert "plainto_tsquery" in sql

    def test_search_with_memory_group_appends_filter(self) -> None:
        backend, cur = _make_backend(rows=[], col_names=[])
        backend.search("query", memory_group="team-a")
        sql = cur.execute.call_args[0][0]
        assert "memory_group" in sql


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------


class TestRelations:
    def test_list_relations_returns_empty(self) -> None:
        backend, _ = _make_backend()
        with patch.object(backend, "_ensure_relations_table"):
            result = backend.list_relations()
        assert result == []

    def test_save_relations_empty_list_returns_zero(self) -> None:
        backend, _ = _make_backend()
        assert backend.save_relations("key", []) == 0

    def test_load_relations_filters_by_key(self) -> None:
        backend, _ = _make_backend()
        rels = [
            {
                "subject": "A",
                "predicate": "is",
                "object_entity": "B",
                "source_entry_keys": ["key1"],
                "confidence": 0.9,
                "created_at": "2024-01-01T00:00:00+00:00",
            },
            {
                "subject": "C",
                "predicate": "has",
                "object_entity": "D",
                "source_entry_keys": ["key2"],
                "confidence": 0.8,
                "created_at": "2024-01-01T00:00:00+00:00",
            },
        ]
        with patch.object(backend, "list_relations", return_value=rels):
            result = backend.load_relations("key1")
        assert len(result) == 1
        assert result[0]["subject"] == "A"


# ---------------------------------------------------------------------------
# MemoryStore wiring
# ---------------------------------------------------------------------------


class TestMemoryStoreWiring:
    """MemoryStore correctly routes persistence through PostgresPrivateBackend."""

    def test_private_backend_replaces_sqlite_persistence(self, tmp_path: Path) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend
        from tapps_brain.store import MemoryStore

        cm, _conn, _cur, _ = _make_mocks()
        backend = PostgresPrivateBackend(cm, project_id="p", agent_id="a")
        store = MemoryStore(tmp_path, private_backend=backend)

        # The store wraps the supplied PostgresPrivateBackend directly.
        from tapps_brain.postgres_private import PostgresPrivateBackend as _PPB

        assert isinstance(store._persistence, _PPB)
        assert store._persistence is backend

    def test_no_sqlite_memory_db_created(self, tmp_path: Path) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend
        from tapps_brain.store import MemoryStore

        cm, _conn, _cur, _ = _make_mocks()
        backend = PostgresPrivateBackend(cm, project_id="p", agent_id="a")
        MemoryStore(tmp_path, private_backend=backend)

        db_files = list(tmp_path.rglob("memory.db"))
        assert db_files == [], "No memory.db files should be created — v3 is Postgres-only (ADR-007)"

    def test_no_private_backend_and_no_dsn_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tapps_brain.store import MemoryStore

        # Ensure neither v3 unified DSN nor legacy hive DSN are set, and
        # disable the conftest in-memory fixture so the production hard-fail
        # is exercised directly.
        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
        monkeypatch.setenv("TAPPS_BRAIN_TEST_NO_INMEMORY_BACKEND", "1")
        with pytest.raises(ValueError, match="Postgres"):
            MemoryStore(tmp_path)


# ---------------------------------------------------------------------------
# resolve_private_backend_from_env
# ---------------------------------------------------------------------------


class TestResolvePrivateBackendFromEnv:
    def test_returns_none_when_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.backends import resolve_private_backend_from_env

        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
        assert resolve_private_backend_from_env("project-id", "agent-id") is None

    def test_uses_database_url_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.backends import resolve_private_backend_from_env
        from tapps_brain.postgres_private import PostgresPrivateBackend

        monkeypatch.setenv("TAPPS_BRAIN_DATABASE_URL", "postgres://localhost/brain")
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
        backend = resolve_private_backend_from_env("project-id", "agent-id")
        try:
            assert isinstance(backend, PostgresPrivateBackend)
        finally:
            if backend:
                backend.close()

    def test_falls_back_to_hive_dsn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.backends import resolve_private_backend_from_env
        from tapps_brain.postgres_private import PostgresPrivateBackend

        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_DSN", "postgres://localhost/brain")
        backend = resolve_private_backend_from_env("project-id", "agent-id")
        try:
            assert isinstance(backend, PostgresPrivateBackend)
        finally:
            if backend:
                backend.close()

    def test_returns_none_for_invalid_dsn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.backends import resolve_private_backend_from_env

        monkeypatch.setenv("TAPPS_BRAIN_DATABASE_URL", "mysql://bad")
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
        assert resolve_private_backend_from_env("project-id", "agent-id") is None
