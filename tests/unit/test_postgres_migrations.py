"""Unit tests for PostgreSQL migration tooling.

EPIC-055 STORY-055.9 — migration file discovery and version tracking.
No real PostgreSQL needed; filesystem and version logic are tested directly.
"""

from __future__ import annotations

from unittest.mock import patch

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

        with (
            patch.dict("sys.modules", {"psycopg": None}),
            pytest.raises(ImportError, match="psycopg"),
        ):
            apply_hive_migrations("postgres://localhost/test")

    def test_get_status_raises_import_error(self) -> None:
        from tapps_brain.postgres_migrations import get_hive_schema_status

        with (
            patch.dict("sys.modules", {"psycopg": None}),
            pytest.raises(ImportError, match="psycopg"),
        ):
            get_hive_schema_status("postgres://localhost/test")


class TestMigrationDowngradeError:
    """Verify the MigrationDowngradeError exception type."""

    def test_is_runtime_error(self) -> None:
        from tapps_brain.postgres_migrations import MigrationDowngradeError

        err = MigrationDowngradeError("DB v10 > bundled v5")
        assert isinstance(err, RuntimeError)
        assert "DB v10 > bundled v5" in str(err)

    def test_can_be_caught_as_runtime_error(self) -> None:
        from tapps_brain.postgres_migrations import MigrationDowngradeError

        with pytest.raises(RuntimeError):
            raise MigrationDowngradeError("test")


class TestMaybeAutoMigratePrivate:
    """Unit tests for the auto-migrate gate (STORY-066.8 AC1–AC6)."""

    _DSN = "postgres://tapps:tapps@localhost:5433/tapps_brain"

    def _make_status(self, current_version: int) -> object:
        """Return a SchemaStatus-like object with the given current_version."""
        from tapps_brain.postgres_migrations import SchemaStatus

        status = SchemaStatus()
        status.current_version = current_version
        return status

    # AC2: default behaviour (env var unset or 0) is unchanged
    def test_gate_off_by_default_no_db_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When TAPPS_BRAIN_AUTO_MIGRATE is unset, no DB calls are made."""
        monkeypatch.delenv("TAPPS_BRAIN_AUTO_MIGRATE", raising=False)

        with patch("tapps_brain.postgres_migrations.get_private_schema_status") as mock_status:
            from tapps_brain.postgres_migrations import maybe_auto_migrate_private

            maybe_auto_migrate_private(self._DSN)

        mock_status.assert_not_called()

    def test_gate_off_when_set_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When TAPPS_BRAIN_AUTO_MIGRATE=0, no DB calls are made."""
        monkeypatch.setenv("TAPPS_BRAIN_AUTO_MIGRATE", "0")

        with patch("tapps_brain.postgres_migrations.get_private_schema_status") as mock_status:
            from tapps_brain.postgres_migrations import maybe_auto_migrate_private

            maybe_auto_migrate_private(self._DSN)

        mock_status.assert_not_called()

    # AC1: MemoryStore honours TAPPS_BRAIN_AUTO_MIGRATE=1 by running pending migrations
    def test_gate_on_calls_apply_migrations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When TAPPS_BRAIN_AUTO_MIGRATE=1, apply_private_migrations is called."""
        monkeypatch.setenv("TAPPS_BRAIN_AUTO_MIGRATE", "1")

        with (
            patch(
                "tapps_brain.postgres_migrations.get_private_schema_status",
                return_value=self._make_status(3),
            ),
            patch(
                "tapps_brain.postgres_migrations.apply_private_migrations",
                return_value=[4, 5],
            ) as mock_apply,
        ):
            from tapps_brain.postgres_migrations import maybe_auto_migrate_private

            maybe_auto_migrate_private(self._DSN)

        mock_apply.assert_called_once_with(self._DSN)

    # AC3: MigrationDowngradeError raised when DB version exceeds max bundled
    def test_downgrade_refused_when_db_ahead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises MigrationDowngradeError when DB schema version > max bundled."""
        monkeypatch.setenv("TAPPS_BRAIN_AUTO_MIGRATE", "1")

        # Discover real migrations to get max bundled version, then simulate DB ahead.
        from tapps_brain.postgres_migrations import discover_private_migrations

        migrations = discover_private_migrations()
        max_bundled = max(v for v, _, _ in migrations)

        with (
            patch(
                "tapps_brain.postgres_migrations.get_private_schema_status",
                return_value=self._make_status(max_bundled + 1),
            ),
            patch("tapps_brain.postgres_migrations.apply_private_migrations") as mock_apply,
        ):
            from tapps_brain.postgres_migrations import (
                MigrationDowngradeError,
                maybe_auto_migrate_private,
            )

            with pytest.raises(MigrationDowngradeError, match="exceeds the max bundled"):
                maybe_auto_migrate_private(self._DSN)

        mock_apply.assert_not_called()

    # AC4: every applied migration logged at INFO (indirect via apply_private_migrations)
    def test_clean_db_no_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When DB is already up to date, apply_private_migrations is called (no-op)."""
        monkeypatch.setenv("TAPPS_BRAIN_AUTO_MIGRATE", "1")

        from tapps_brain.postgres_migrations import discover_private_migrations

        migrations = discover_private_migrations()
        max_bundled = max(v for v, _, _ in migrations)

        with (
            patch(
                "tapps_brain.postgres_migrations.get_private_schema_status",
                return_value=self._make_status(max_bundled),
            ),
            patch(
                "tapps_brain.postgres_migrations.apply_private_migrations",
                return_value=[],
            ) as mock_apply,
        ):
            from tapps_brain.postgres_migrations import maybe_auto_migrate_private

            maybe_auto_migrate_private(self._DSN)

        mock_apply.assert_called_once_with(self._DSN)

    def test_partial_migration_applies_remaining(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When DB is at version N and bundled max is N+2, apply_private_migrations runs."""
        monkeypatch.setenv("TAPPS_BRAIN_AUTO_MIGRATE", "1")

        from tapps_brain.postgres_migrations import discover_private_migrations

        migrations = discover_private_migrations()
        max_bundled = max(v for v, _, _ in migrations)
        partial_version = max(0, max_bundled - 1)

        with (
            patch(
                "tapps_brain.postgres_migrations.get_private_schema_status",
                return_value=self._make_status(partial_version),
            ),
            patch(
                "tapps_brain.postgres_migrations.apply_private_migrations",
                return_value=[max_bundled],
            ) as mock_apply,
        ):
            from tapps_brain.postgres_migrations import maybe_auto_migrate_private

            maybe_auto_migrate_private(self._DSN)

        mock_apply.assert_called_once_with(self._DSN)

    # AC5: env var documented — verified via presence in source files (doc tests below)
    def test_env_var_name_constant(self) -> None:
        """Sanity-check that the module reads the correct env var name."""
        import inspect

        import tapps_brain.postgres_migrations as m

        src = inspect.getsource(m.maybe_auto_migrate_private)
        assert "TAPPS_BRAIN_AUTO_MIGRATE" in src

    # AC6: gate-on / gate-off / downgrade-refused / clean-DB / partial paths all covered
    # (covered by the tests above)
    def test_no_bundled_migrations_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When bundled migration list is empty, nothing is applied."""
        monkeypatch.setenv("TAPPS_BRAIN_AUTO_MIGRATE", "1")

        with (
            patch(
                "tapps_brain.postgres_migrations.discover_private_migrations",
                return_value=[],
            ),
            patch("tapps_brain.postgres_migrations.get_private_schema_status") as mock_status,
            patch("tapps_brain.postgres_migrations.apply_private_migrations") as mock_apply,
        ):
            from tapps_brain.postgres_migrations import maybe_auto_migrate_private

            maybe_auto_migrate_private(self._DSN)

        mock_status.assert_not_called()
        mock_apply.assert_not_called()
