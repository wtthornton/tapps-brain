"""Unit tests for PostgreSQL migration tooling.

EPIC-055 STORY-055.9 — migration file discovery and version tracking.
No real PostgreSQL needed; filesystem and version logic are tested directly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestMigrationDiscovery:
    """Test that migration files are discovered and sorted correctly."""

    def test_discover_hive_migrations_returns_sorted(self) -> None:
        from tapps_brain.postgres_migrations import discover_hive_migrations

        migrations = discover_hive_migrations()
        assert len(migrations) >= 1
        # First migration should be version 1.
        assert migrations[0][0] == 1
        assert "001_initial.sql" in migrations[0][1]
        # SQL content should contain table creation.
        assert "hive_memories" in migrations[0][2]

    def test_discover_federation_migrations_returns_sorted(self) -> None:
        from tapps_brain.postgres_migrations import discover_federation_migrations

        migrations = discover_federation_migrations()
        assert len(migrations) >= 1
        assert migrations[0][0] == 1
        assert "001_initial.sql" in migrations[0][1]
        assert "federated_memories" in migrations[0][2]

    def test_migration_files_are_ordered_by_version(self) -> None:
        from tapps_brain.postgres_migrations import discover_hive_migrations

        migrations = discover_hive_migrations()
        versions = [v for v, _, _ in migrations]
        assert versions == sorted(versions)


class TestMigrationFilePattern:
    """Test the migration filename regex."""

    def test_valid_filenames(self) -> None:
        from tapps_brain.postgres_migrations import _MIGRATION_FILE_RE

        assert _MIGRATION_FILE_RE.match("001_initial.sql") is not None
        assert _MIGRATION_FILE_RE.match("002_add_indexes.sql") is not None
        assert _MIGRATION_FILE_RE.match("100_big_change.sql") is not None

    def test_invalid_filenames(self) -> None:
        from tapps_brain.postgres_migrations import _MIGRATION_FILE_RE

        assert _MIGRATION_FILE_RE.match("README.md") is None
        assert _MIGRATION_FILE_RE.match("__init__.py") is None
        assert _MIGRATION_FILE_RE.match("no_number.sql") is None


class TestSchemaStatus:
    """Test SchemaStatus dataclass."""

    def test_schema_status_defaults(self) -> None:
        from tapps_brain.postgres_migrations import SchemaStatus

        status = SchemaStatus()
        assert status.current_version == 0
        assert status.applied_versions == []
        assert status.pending_migrations == []


class TestHiveMigrationContent:
    """Verify the SQL content of hive migrations contains expected objects."""

    def test_001_contains_required_tables(self) -> None:
        from tapps_brain.postgres_migrations import discover_hive_migrations

        migrations = discover_hive_migrations()
        sql = migrations[0][2]

        # Required tables.
        assert "hive_memories" in sql
        assert "hive_groups" in sql
        assert "hive_group_members" in sql
        assert "hive_feedback_events" in sql
        assert "agent_registry" in sql
        assert "hive_schema_version" in sql
        assert "hive_write_notify" in sql

    def test_001_contains_required_indexes(self) -> None:
        from tapps_brain.postgres_migrations import discover_hive_migrations

        migrations = discover_hive_migrations()
        sql = migrations[0][2]

        assert "idx_hive_tags_gin" in sql
        assert "idx_hive_search_vector_gin" in sql
        assert "idx_hive_namespace_confidence" in sql
        assert "idx_hive_embedding_ivfflat" in sql

    def test_001_contains_tsvector_trigger(self) -> None:
        from tapps_brain.postgres_migrations import discover_hive_migrations

        migrations = discover_hive_migrations()
        sql = migrations[0][2]

        assert "hive_memories_search_vector_update" in sql
        assert "trg_hive_memories_search_vector" in sql

    def test_001_contains_notify_trigger(self) -> None:
        from tapps_brain.postgres_migrations import discover_hive_migrations

        migrations = discover_hive_migrations()
        sql = migrations[0][2]

        assert "hive_memories_notify" in sql
        assert "pg_notify" in sql
        assert "hive_memories_changed" in sql


class TestFederationMigrationContent:
    """Verify the SQL content of federation migrations."""

    def test_001_contains_required_tables(self) -> None:
        from tapps_brain.postgres_migrations import discover_federation_migrations

        migrations = discover_federation_migrations()
        sql = migrations[0][2]

        assert "federated_memories" in sql
        assert "federation_subscriptions" in sql
        assert "federation_schema_version" in sql
        assert "federation_meta" in sql

    def test_001_contains_vector_index(self) -> None:
        from tapps_brain.postgres_migrations import discover_federation_migrations

        migrations = discover_federation_migrations()
        sql = migrations[0][2]

        assert "vector(384)" in sql
        assert "ivfflat" in sql


class TestApplyMigrationsRequiresPsycopg:
    """Verify that apply/status functions raise ImportError when psycopg is missing."""

    def test_apply_hive_raises_import_error(self) -> None:
        from tapps_brain.postgres_migrations import apply_hive_migrations

        with patch.dict("sys.modules", {"psycopg": None}):
            with pytest.raises(ImportError, match="psycopg"):
                apply_hive_migrations("postgres://localhost/test")

    def test_get_status_raises_import_error(self) -> None:
        from tapps_brain.postgres_migrations import get_hive_schema_status

        with patch.dict("sys.modules", {"psycopg": None}):
            with pytest.raises(ImportError, match="psycopg"):
                get_hive_schema_status("postgres://localhost/test")
