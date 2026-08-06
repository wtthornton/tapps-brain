"""TAP-5542: shape of the gated-learning migration (030).

Static checks that don't require Postgres. Applying the SQL against a live
cluster is covered by the Postgres-backed integration suite.
"""

from __future__ import annotations

from pathlib import Path

_PRIVATE = Path(__file__).resolve().parents[2] / "src" / "tapps_brain" / "migrations" / "private"
_UP = _PRIVATE / "030_learning_status.sql"
_DOWN = _PRIVATE / "030_learning_status.down.sql"


def test_up_migration_file_exists() -> None:
    assert _UP.is_file(), f"missing: {_UP}"


def test_down_migration_file_exists() -> None:
    assert _DOWN.is_file(), f"missing: {_DOWN}"


def test_up_adds_learning_status_with_the_three_promotion_values() -> None:
    body = _UP.read_text()
    assert "ADD COLUMN IF NOT EXISTS learning_status" in body
    for value in ("'candidate'", "'approved'", "'demoted'"):
        assert value in body, f"learning_status CHECK is missing {value}"


def test_up_defaults_existing_rows_to_candidate() -> None:
    """Nothing written before this migration passed a gate.

    A DEFAULT of ``approved`` would silently mark the entire existing corpus
    injectable, which is the exact failure the epic exists to prevent.
    """
    body = _UP.read_text()
    assert "DEFAULT 'candidate'" in body


def test_up_adds_provenance_columns() -> None:
    body = _UP.read_text()
    for column in ("promoted_by", "promoted_at", "promotion_signal", "demotion_reason"):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in body, f"missing column: {column}"


def test_promotion_signal_accepts_only_eval_and_human() -> None:
    """Frequency cannot approve — promotion needs an explicit eval or human signal."""
    body = _UP.read_text()
    assert "CHECK (promotion_signal IN ('eval', 'human'))" in body


def test_approved_rows_require_provenance() -> None:
    """An approval with no recorded actor/time/signal must be unrepresentable."""
    body = _UP.read_text()
    assert "private_memories_approved_needs_provenance" in body
    assert "learning_status <> 'approved'" in body
    for column in ("promoted_by IS NOT NULL", "promoted_at IS NOT NULL"):
        assert column in body, f"provenance CHECK is missing: {column}"
    assert "promotion_signal IS NOT NULL" in body


def test_up_is_rerunnable() -> None:
    """Re-applying on a healthy DB must be a no-op, not an error."""
    body = _UP.read_text()
    assert "ADD COLUMN IF NOT EXISTS" in body
    assert "CREATE INDEX IF NOT EXISTS idx_private_memories_learning_status" in body
    # The named constraint has no IF NOT EXISTS form; it must be catalog-guarded.
    assert "FROM pg_constraint" in body


def test_up_records_schema_version_row() -> None:
    body = _UP.read_text()
    assert "INSERT INTO private_schema_version" in body
    assert "VALUES (30," in body
    # Must land outside the DO block guarding the constraint, so the migration
    # is recorded as applied on every path through the file.
    assert body.find("INSERT INTO private_schema_version") > body.rfind("END\n$$")


def test_down_reverses_every_object_the_up_creates() -> None:
    body = _DOWN.read_text()
    assert "DROP INDEX IF EXISTS idx_private_memories_learning_status" in body
    assert "DROP CONSTRAINT IF EXISTS private_memories_approved_needs_provenance" in body
    for column in (
        "learning_status",
        "promoted_by",
        "promoted_at",
        "promotion_signal",
        "demotion_reason",
    ):
        assert f"DROP COLUMN IF EXISTS {column}" in body, f"down leaves column behind: {column}"
    assert "DELETE FROM private_schema_version WHERE version = 30" in body
