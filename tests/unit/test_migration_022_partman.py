"""TAP-1974: experience_events pg_partman opt-in migration shape.

Static checks that don't require Postgres. The actual
``partman.create_parent`` invocation is exercised by the integration test
that lives under ``tests/integration/`` (skipped when the test DB has no
``pg_partman`` extension installed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PRIVATE = Path(__file__).resolve().parents[2] / "src" / "tapps_brain" / "migrations" / "private"
_UP = _PRIVATE / "022_partman_experience_events.sql"
_DOWN = _PRIVATE / "022_partman_experience_events.down.sql"


def test_up_migration_file_exists() -> None:
    assert _UP.is_file(), f"missing: {_UP}"


def test_down_migration_file_exists() -> None:
    assert _DOWN.is_file(), f"missing: {_DOWN}"


def test_up_migration_skips_when_pg_partman_absent() -> None:
    """The up migration must guard on pg_extension lookup before touching partman.

    Compares the position of the actual ``PERFORM partman.create_parent``
    invocation against the ``FROM pg_extension`` guard — comments at the top
    of the file that *mention* partman are tolerated.
    """
    body = _UP.read_text()
    pg_ext_idx = body.find("FROM pg_extension")
    partman_call_idx = body.find("PERFORM partman.create_parent")
    assert pg_ext_idx > 0, "missing pg_extension guard"
    assert partman_call_idx > 0, "missing partman.create_parent call"
    assert partman_call_idx > pg_ext_idx, (
        "PERFORM partman.create_parent must come AFTER the pg_extension guard"
    )
    assert "_has_partman" in body, "missing _has_partman flag"
    assert "RETURN;" in body, "missing early-RETURN for the no-extension path"


def test_up_migration_inserts_schema_version_row() -> None:
    """Schema version row must be inserted regardless of extension presence."""
    body = _UP.read_text()
    assert "INSERT INTO private_schema_version" in body
    assert "VALUES (22," in body
    # The version-row insertion must live OUTSIDE the DO $$ ... $$ guard so
    # the migration counts as applied even on clusters without pg_partman.
    schema_idx = body.find("INSERT INTO private_schema_version")
    final_end_idx = body.rfind("END $$")
    assert schema_idx > final_end_idx, (
        "version-row INSERT must follow the DO block; otherwise no-pg_partman "
        "clusters would skip applying the migration."
    )


def test_down_migration_removes_schema_version_row() -> None:
    body = _DOWN.read_text()
    assert "DELETE FROM private_schema_version WHERE version = 22" in body


def test_down_migration_skips_when_pg_partman_absent() -> None:
    body = _DOWN.read_text()
    assert "FROM pg_extension" in body
    assert "partman.undo_partition" in body
    # Must come after the guard, like the up migration.
    assert body.find("partman.undo_partition") > body.find("FROM pg_extension")


@pytest.mark.parametrize(
    "kw",
    [
        # Operator-overridable retention window (default 12).
        "app.events_retention_months",
        # pg_partman config knobs from the doc.
        "retention_keep_table",
        "premake",
    ],
)
def test_up_migration_documents_retention_knobs(kw: str) -> None:
    body = _UP.read_text()
    assert kw in body, f"migration must reference {kw!r}"


def test_up_migration_clamps_retention_to_1_60() -> None:
    body = _UP.read_text()
    # Range is [1, 60] per docs/engineering/partition-retention.md.
    assert "_retention_months < 1" in body
    assert "_retention_months > 60" in body
