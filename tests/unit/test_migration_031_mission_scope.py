"""TAP-5544: shape of the mission-scope migration (031).

Static checks that don't require Postgres. Applying the SQL against a live
cluster is covered by the Postgres-backed integration suite.
"""

from __future__ import annotations

from pathlib import Path

_PRIVATE = Path(__file__).resolve().parents[2] / "src" / "tapps_brain" / "migrations" / "private"
_UP = _PRIVATE / "031_mission_scope.sql"
_DOWN = _PRIVATE / "031_mission_scope.down.sql"


def test_up_migration_file_exists() -> None:
    assert _UP.is_file(), f"missing: {_UP}"


def test_down_migration_file_exists() -> None:
    assert _DOWN.is_file(), f"missing: {_DOWN}"


def test_up_adds_the_mission_companion_columns() -> None:
    body = _UP.read_text()
    for column in ("mission_id", "run_id"):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in body, f"missing column: {column}"


def test_mission_scoped_rows_must_name_their_mission() -> None:
    """A mission-scoped row with no mission is unowned, not merely incomplete."""
    body = _UP.read_text()
    assert "private_memories_mission_scope_needs_mission_id" in body
    assert "scope <> 'mission' OR mission_id IS NOT NULL" in body


def test_up_indexes_the_isolation_key() -> None:
    """Mission isolation is a query predicate, so it needs an index behind it."""
    body = _UP.read_text()
    assert "CREATE INDEX IF NOT EXISTS idx_private_memories_mission" in body
    assert "project_id, agent_id, mission_id" in body


def test_up_is_rerunnable() -> None:
    body = _UP.read_text()
    assert "ADD COLUMN IF NOT EXISTS" in body
    assert "CREATE INDEX IF NOT EXISTS" in body
    # The named constraint has no IF NOT EXISTS form; it must be catalog-guarded.
    assert "FROM pg_constraint" in body


def test_up_records_schema_version_row() -> None:
    body = _UP.read_text()
    assert "INSERT INTO private_schema_version" in body
    assert "VALUES (31," in body
    # Must land outside the DO block guarding the constraint, so the migration
    # is recorded as applied on every path through the file.
    assert body.find("INSERT INTO private_schema_version") > body.rfind("END\n$$")


def test_down_does_not_strand_mission_scoped_rows() -> None:
    """Dropping the columns without folding the scope leaves unreachable rows."""
    body = _DOWN.read_text()
    assert "UPDATE private_memories SET scope = 'project' WHERE scope = 'mission'" in body
    assert body.find("UPDATE private_memories") < body.find("DROP COLUMN IF EXISTS mission_id")


def test_down_reverses_every_object_the_up_creates() -> None:
    body = _DOWN.read_text()
    assert "DROP INDEX IF EXISTS idx_private_memories_mission" in body
    assert "DROP CONSTRAINT IF EXISTS private_memories_mission_scope_needs_mission_id" in body
    for column in ("mission_id", "run_id"):
        assert f"DROP COLUMN IF EXISTS {column}" in body, f"down leaves column behind: {column}"
    assert "DELETE FROM private_schema_version WHERE version = 31" in body
