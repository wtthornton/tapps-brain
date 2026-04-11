"""Unit tests for private-memory migration discovery and wiring (EPIC-059 STORY-059.4).

These tests exercise migration file discovery and the migration helper functions
WITHOUT a live Postgres connection. DB-applied behaviour is validated by the
integration test in tests/integration/ when a Postgres fixture is available.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from tapps_brain.postgres_migrations import (
    SchemaStatus,
    apply_private_migrations,
    discover_private_migrations,
    get_private_schema_status,
)


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


class TestDiscoverPrivateMigrations:
    def test_returns_at_least_one_migration(self) -> None:
        migrations = discover_private_migrations()
        assert len(migrations) >= 1, "Expected at least one private migration file"

    def test_returns_sorted_by_version(self) -> None:
        migrations = discover_private_migrations()
        versions = [v for v, _, _ in migrations]
        assert versions == sorted(versions), "Migrations must be sorted by version number"

    def test_first_migration_is_version_1(self) -> None:
        migrations = discover_private_migrations()
        assert migrations[0][0] == 1

    def test_first_migration_filename_matches_pattern(self) -> None:
        _, filename, _ = discover_private_migrations()[0]
        assert re.match(r"^\d+_.+\.sql$", filename), f"Unexpected filename: {filename!r}"

    def test_sql_content_is_non_empty_string(self) -> None:
        for version, fname, sql in discover_private_migrations():
            assert isinstance(sql, str), f"SQL for {fname} (v{version}) is not a string"
            assert sql.strip(), f"SQL for {fname} (v{version}) is empty"

    def test_migration_file_exists_on_disk(self) -> None:
        """Sanity: the physical SQL file must exist in the package tree."""
        pkg_dir = Path(__file__).parent.parent.parent / "src" / "tapps_brain" / "migrations" / "private"
        sql_files = list(pkg_dir.glob("*.sql"))
        assert sql_files, "No .sql files found in migrations/private/"

    def test_version_1_creates_private_memories_table(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "private_memories" in sql, "Migration must create the private_memories table"

    def test_version_1_creates_schema_version_table(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "private_schema_version" in sql

    def test_version_1_has_project_id_column(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "project_id" in sql

    def test_version_1_has_agent_id_column(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "agent_id" in sql

    def test_version_1_has_primary_key_on_three_columns(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "PRIMARY KEY (project_id, agent_id, key)" in sql

    def test_version_1_creates_search_vector_trigger(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "trg_private_memories_search_vector" in sql

    def test_version_1_creates_ivfflat_embedding_index(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "ivfflat" in sql.lower()
        assert "embedding" in sql

    def test_version_1_records_version_row(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        # The migration must self-record by inserting into private_schema_version.
        assert "INSERT INTO private_schema_version" in sql

    def test_version_1_includes_tags_jsonb(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "tags" in sql and "JSONB" in sql.upper()

    def test_version_1_includes_embedding_vector_column(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "vector(384)" in sql

    def test_version_1_includes_confidence_column(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "confidence" in sql

    def test_version_1_includes_search_vector_gin_index(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "idx_priv_search_vector_gin" in sql

    def test_version_1_includes_project_agent_confidence_index(self) -> None:
        _, _, sql = discover_private_migrations()[0]
        assert "idx_priv_project_agent_confidence" in sql


# ---------------------------------------------------------------------------
# Schema status
# ---------------------------------------------------------------------------


class TestGetPrivateSchemaStatus:
    def test_raises_import_error_without_psycopg(self) -> None:
        """Without psycopg installed (or importable), should raise ImportError."""
        import sys

        with patch.dict(sys.modules, {"psycopg": None}):
            with pytest.raises((ImportError, TypeError)):
                get_private_schema_status("postgres://localhost/brain")

    def test_returns_schema_status_instance(self) -> None:
        """With a mocked psycopg connection, returns a SchemaStatus."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        # Table does not exist yet → version table absent.
        mock_cur.fetchone.return_value = (False,)
        mock_cur.fetchall.return_value = []

        mock_psycopg = MagicMock()
        mock_psycopg.connect.return_value = mock_conn

        import sys

        with patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            status = get_private_schema_status("postgres://localhost/brain")

        assert isinstance(status, SchemaStatus)

    def test_no_applied_versions_when_table_absent(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = (False,)
        mock_cur.fetchall.return_value = []

        mock_psycopg = MagicMock()
        mock_psycopg.connect.return_value = mock_conn

        import sys

        with patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            status = get_private_schema_status("postgres://localhost/brain")

        assert status.current_version == 0
        assert status.applied_versions == []

    def test_pending_migrations_listed_when_table_absent(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = (False,)
        mock_cur.fetchall.return_value = []

        mock_psycopg = MagicMock()
        mock_psycopg.connect.return_value = mock_conn

        import sys

        with patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            status = get_private_schema_status("postgres://localhost/brain")

        # At minimum migration 001 is pending.
        assert len(status.pending_migrations) >= 1
        assert status.pending_migrations[0][0] == 1

    def test_no_pending_when_all_applied(self) -> None:
        migrations = discover_private_migrations()
        all_versions = [v for v, _, _ in migrations]

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        # Table exists.
        mock_cur.fetchone.return_value = (True,)
        # All versions already applied.
        mock_cur.fetchall.return_value = [(v,) for v in all_versions]

        mock_psycopg = MagicMock()
        mock_psycopg.connect.return_value = mock_conn

        import sys

        with patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            status = get_private_schema_status("postgres://localhost/brain")

        assert status.pending_migrations == []
        assert status.current_version == max(all_versions)


# ---------------------------------------------------------------------------
# Apply migrations
# ---------------------------------------------------------------------------


class TestApplyPrivateMigrations:
    def test_raises_import_error_without_psycopg(self) -> None:
        import sys

        with patch.dict(sys.modules, {"psycopg": None}):
            with pytest.raises((ImportError, TypeError)):
                apply_private_migrations("postgres://localhost/brain")

    def test_dry_run_returns_version_list_without_executing(self) -> None:
        migrations = discover_private_migrations()
        all_versions = [v for v, _, _ in migrations]

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        # Table does not exist yet.
        mock_cur.fetchone.return_value = (False,)
        mock_cur.fetchall.return_value = []

        mock_psycopg = MagicMock()
        mock_psycopg.connect.return_value = mock_conn

        import sys

        with patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            applied = apply_private_migrations("postgres://localhost/brain", dry_run=True)

        assert applied == all_versions
        # In dry_run mode, conn.execute / conn.commit must NOT be called.
        mock_conn.execute.assert_not_called()
        mock_conn.commit.assert_not_called()

    def test_noop_when_all_already_applied(self) -> None:
        migrations = discover_private_migrations()
        all_versions = [v for v, _, _ in migrations]

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        # Table exists, all versions applied.
        mock_cur.fetchone.return_value = (True,)
        mock_cur.fetchall.return_value = [(v,) for v in all_versions]

        mock_psycopg = MagicMock()
        mock_psycopg.connect.return_value = mock_conn

        import sys

        with patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            applied = apply_private_migrations("postgres://localhost/brain")

        assert applied == []
        mock_conn.execute.assert_not_called()
        mock_conn.commit.assert_not_called()
