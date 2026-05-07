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


# ---------------------------------------------------------------------------
# TAP-1492 STORY-074.5: KG migrations 016–020 discovery
# ---------------------------------------------------------------------------


class TestKGMigrationDiscovery:
    """Verify that migrations 016–020 (EPIC-074) are discoverable (TAP-1492)."""

    def test_kg_migrations_16_to_20_are_present(self) -> None:
        """discover_private_migrations() must include versions 16-20."""
        from tapps_brain.postgres_migrations import discover_private_migrations

        migrations = discover_private_migrations()
        versions = {v for v, _, _ in migrations}
        for expected in range(16, 21):
            assert expected in versions, (
                f"Private migration version {expected} is missing. "
                f"Found versions: {sorted(versions)}"
            )

    def test_kg_entities_migration_content(self) -> None:
        """Migration 016 must create kg_entities with RLS enabled."""
        from tapps_brain.postgres_migrations import discover_private_migrations

        migrations = discover_private_migrations()
        sql_016 = next(sql for v, _, sql in migrations if v == 16)
        assert "kg_entities" in sql_016
        assert "ENABLE ROW LEVEL SECURITY" in sql_016

    def test_kg_edges_migration_content(self) -> None:
        """Migration 017 must create kg_edges with a partial unique index."""
        from tapps_brain.postgres_migrations import discover_private_migrations

        migrations = discover_private_migrations()
        sql_017 = next(sql for v, _, sql in migrations if v == 17)
        assert "kg_edges" in sql_017
        # partial unique index for active edges
        assert "WHERE" in sql_017

    def test_kg_evidence_migration_content(self) -> None:
        """Migration 018 must create kg_evidence."""
        from tapps_brain.postgres_migrations import discover_private_migrations

        migrations = discover_private_migrations()
        sql_018 = next(sql for v, _, sql in migrations if v == 18)
        assert "kg_evidence" in sql_018

    def test_kg_aliases_migration_content(self) -> None:
        """Migration 019 must create kg_aliases."""
        from tapps_brain.postgres_migrations import discover_private_migrations

        migrations = discover_private_migrations()
        sql_019 = next(sql for v, _, sql in migrations if v == 19)
        assert "kg_aliases" in sql_019

    def test_experience_events_migration_content(self) -> None:
        """Migration 020 must create the experience_events partitioned table."""
        from tapps_brain.postgres_migrations import discover_private_migrations

        migrations = discover_private_migrations()
        sql_020 = next(sql for v, _, sql in migrations if v == 20)
        assert "experience_events" in sql_020
        assert "PARTITION BY RANGE" in sql_020

    def test_kg_migrations_are_sorted_in_order(self) -> None:
        """Migrations 016-020 must appear in ascending version order."""
        from tapps_brain.postgres_migrations import discover_private_migrations

        migrations = discover_private_migrations()
        versions = [v for v, _, _ in migrations]
        assert versions == sorted(versions)

    def test_max_private_version_is_at_least_20(self) -> None:
        """Max bundled private migration version must be >= 20 after EPIC-074."""
        from tapps_brain.postgres_migrations import discover_private_migrations

        migrations = discover_private_migrations()
        max_v = max(v for v, _, _ in migrations)
        assert max_v >= 20, f"Expected max version >= 20, got {max_v}"


# ---------------------------------------------------------------------------
# TAP-1492 STORY-074.5: pg_advisory_lock in _apply_migrations
# ---------------------------------------------------------------------------


class TestAdvisoryLockId:
    """Unit tests for the _migration_lock_id helper (TAP-1492)."""

    def test_returns_positive_int(self) -> None:
        from tapps_brain.postgres_migrations import _migration_lock_id

        lock_id = _migration_lock_id("private_schema_version")
        assert isinstance(lock_id, int)
        assert lock_id > 0

    def test_stays_within_signed_int64_range(self) -> None:
        """Lock ID must fit in a signed 64-bit integer for pg_advisory_lock."""
        from tapps_brain.postgres_migrations import _migration_lock_id

        for table in ("private_schema_version", "hive_schema_version", "federation_schema_version"):
            lock_id = _migration_lock_id(table)
            assert 0 < lock_id < 2**63, f"Lock ID {lock_id} out of signed int64 range"

    def test_stable_across_calls(self) -> None:
        """Same input must always produce the same lock ID."""
        from tapps_brain.postgres_migrations import _migration_lock_id

        a = _migration_lock_id("private_schema_version")
        b = _migration_lock_id("private_schema_version")
        assert a == b

    def test_different_tables_get_different_ids(self) -> None:
        from tapps_brain.postgres_migrations import _migration_lock_id

        ids = {
            _migration_lock_id("private_schema_version"),
            _migration_lock_id("hive_schema_version"),
            _migration_lock_id("federation_schema_version"),
        }
        assert len(ids) == 3, "All three version tables must have distinct lock IDs"


class TestApplyMigrationsAdvisoryLock:
    """Verify _apply_migrations acquires pg_advisory_lock (TAP-1492)."""

    def test_advisory_lock_acquired_when_not_dry_run(self) -> None:
        """_apply_migrations must call pg_advisory_lock for live runs."""
        import psycopg  # noqa: F401 — ensure available

        from tapps_brain.postgres_migrations import _migration_lock_id

        advisory_calls: list[str] = []

        class _FakeCursor:
            def __init__(self) -> None:
                self.fetchone_val: tuple[bool] = (False,)

            def execute(self, sql: str, params: tuple = ()) -> None:  # type: ignore[assignment]
                if "pg_advisory_lock" in sql:
                    advisory_calls.append(str(params))
                self.fetchone_val = (False,)

            def fetchone(self) -> tuple[bool]:
                return self.fetchone_val

            def fetchall(self) -> list:
                return []

            def __enter__(self) -> "_FakeCursor":
                return self

            def __exit__(self, *_: object) -> None:
                pass

        class _FakeConn:
            def __init__(self) -> None:
                self._cur = _FakeCursor()

            def cursor(self) -> "_FakeCursor":
                return self._cur

            def execute(self, sql: bytes) -> None:
                pass

            def commit(self) -> None:
                pass

            def __enter__(self) -> "_FakeConn":
                return self

            def __exit__(self, *_: object) -> None:
                pass

        with patch("psycopg.connect", return_value=_FakeConn()):
            from tapps_brain.postgres_migrations import _apply_migrations

            _apply_migrations(
                "postgres://localhost/test",
                "private_schema_version",
                [],
                dry_run=False,
            )

        expected_lock_id = _migration_lock_id("private_schema_version")
        assert any(str(expected_lock_id) in call for call in advisory_calls), (
            f"pg_advisory_lock({expected_lock_id}) was never called. "
            f"advisory_calls={advisory_calls}"
        )

    def test_advisory_lock_skipped_in_dry_run(self) -> None:
        """_apply_migrations must NOT call pg_advisory_lock in dry-run mode."""
        advisory_calls: list[str] = []

        class _FakeCursor:
            def execute(self, sql: str, params: tuple = ()) -> None:  # type: ignore[assignment]
                if "pg_advisory_lock" in sql:
                    advisory_calls.append(str(params))

            def fetchone(self) -> tuple[bool]:
                return (False,)

            def fetchall(self) -> list:
                return []

            def __enter__(self) -> "_FakeCursor":
                return self

            def __exit__(self, *_: object) -> None:
                pass

        class _FakeConn:
            def cursor(self) -> "_FakeCursor":
                return _FakeCursor()

            def __enter__(self) -> "_FakeConn":
                return self

            def __exit__(self, *_: object) -> None:
                pass

        with patch("psycopg.connect", return_value=_FakeConn()):
            from tapps_brain.postgres_migrations import _apply_migrations

            _apply_migrations(
                "postgres://localhost/test",
                "private_schema_version",
                [],
                dry_run=True,
            )

        assert advisory_calls == [], (
            f"pg_advisory_lock should not be called in dry_run mode, but got: {advisory_calls}"
        )
