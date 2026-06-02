"""Unit tests for HNSW index maintenance routine (TAP-2729).

Tests verify that run_hnsw_maintenance issues the correct REINDEX and VACUUM
SQL statements via a mocked psycopg connection.  No real database required.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_psycopg_mock(found_indexes: list[str]) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Return (mock_psycopg, mock_conn, mock_cur) wired for run_hnsw_maintenance.

    ``found_indexes`` controls the rows returned by the pg_class discovery query.
    """
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [(name,) for name in found_indexes]

    # Cursor context-manager protocol
    mock_cur_cm = MagicMock()
    mock_cur_cm.__enter__ = lambda self: mock_cur
    mock_cur_cm.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur_cm

    # Connection context-manager protocol (with psycopg.connect() as conn:)
    mock_conn.__enter__ = lambda self: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_psycopg = MagicMock()
    mock_psycopg.connect.return_value = mock_conn

    return mock_psycopg, mock_conn, mock_cur


# ---------------------------------------------------------------------------
# Tests: dry_run
# ---------------------------------------------------------------------------


class TestRunHNSWMaintenanceDryRun:
    """Dry-run mode returns the plan without issuing SQL."""

    def test_dry_run_returns_all_indexes(self) -> None:
        from tapps_brain.gc import HNSWMaintenanceResult, run_hnsw_maintenance

        pairs = [
            ("idx_priv_embedding_hnsw", "private_memories"),
            ("idx_hive_embedding_hnsw", "hive_memories"),
        ]
        result = run_hnsw_maintenance(
            "postgresql://localhost/test", index_table_pairs=pairs, dry_run=True
        )

        assert isinstance(result, HNSWMaintenanceResult)
        assert result.dry_run is True
        assert result.reindexed_indexes == ["idx_priv_embedding_hnsw", "idx_hive_embedding_hnsw"]
        assert result.vacuumed_tables == ["private_memories", "hive_memories"]
        assert result.error is None

    def test_dry_run_deduplicates_tables(self) -> None:
        from tapps_brain.gc import run_hnsw_maintenance

        # Two indexes on the same table — table should appear once in vacuumed.
        pairs = [
            ("idx_one_hnsw", "shared_table"),
            ("idx_two_hnsw", "shared_table"),
        ]
        result = run_hnsw_maintenance(
            "postgresql://localhost/test", index_table_pairs=pairs, dry_run=True
        )

        assert result.vacuumed_tables == ["shared_table"]

    def test_dry_run_does_not_call_connect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_psycopg = MagicMock()
        monkeypatch.setitem(sys.modules, "psycopg", mock_psycopg)

        from tapps_brain.gc import run_hnsw_maintenance

        run_hnsw_maintenance("postgresql://localhost/test", dry_run=True)
        mock_psycopg.connect.assert_not_called()

    def test_dry_run_duration_is_non_negative(self) -> None:
        from tapps_brain.gc import run_hnsw_maintenance

        result = run_hnsw_maintenance("postgresql://localhost/test", dry_run=True)
        assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# Tests: live run — SQL assertions
# ---------------------------------------------------------------------------


class TestRunHNSWMaintenanceLiveSQL:
    """Live-mode tests assert REINDEX and VACUUM SQL are issued correctly."""

    def test_reindex_called_for_each_found_index(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """REINDEX INDEX CONCURRENTLY is executed for every index present in pg_class."""
        pairs = [
            ("idx_priv_embedding_hnsw", "private_memories"),
            ("idx_hive_embedding_hnsw", "hive_memories"),
        ]
        found = ["idx_priv_embedding_hnsw", "idx_hive_embedding_hnsw"]
        mock_psycopg, mock_conn, _ = _make_psycopg_mock(found)
        monkeypatch.setitem(sys.modules, "psycopg", mock_psycopg)

        from tapps_brain.gc import run_hnsw_maintenance

        result = run_hnsw_maintenance("postgresql://localhost/test", index_table_pairs=pairs)

        execute_calls = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
        reindex_calls = [s for s in execute_calls if "REINDEX" in s]
        assert len(reindex_calls) == 2
        assert any("idx_priv_embedding_hnsw" in s for s in reindex_calls)
        assert any("idx_hive_embedding_hnsw" in s for s in reindex_calls)
        assert result.reindexed_indexes == ["idx_priv_embedding_hnsw", "idx_hive_embedding_hnsw"]

    def test_vacuum_called_for_each_found_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VACUUM (ANALYZE) is executed for each table whose index was found."""
        pairs = [
            ("idx_priv_embedding_hnsw", "private_memories"),
            ("idx_hive_embedding_hnsw", "hive_memories"),
        ]
        found = ["idx_priv_embedding_hnsw", "idx_hive_embedding_hnsw"]
        mock_psycopg, mock_conn, _ = _make_psycopg_mock(found)
        monkeypatch.setitem(sys.modules, "psycopg", mock_psycopg)

        from tapps_brain.gc import run_hnsw_maintenance

        result = run_hnsw_maintenance("postgresql://localhost/test", index_table_pairs=pairs)

        execute_calls = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
        vacuum_calls = [s for s in execute_calls if "VACUUM" in s]
        assert len(vacuum_calls) == 2
        assert any("private_memories" in s for s in vacuum_calls)
        assert any("hive_memories" in s for s in vacuum_calls)
        assert result.vacuumed_tables == ["private_memories", "hive_memories"]

    def test_reindex_before_vacuum_in_call_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All REINDEX calls precede VACUUM calls."""
        pairs = [
            ("idx_priv_embedding_hnsw", "private_memories"),
            ("idx_hive_embedding_hnsw", "hive_memories"),
        ]
        mock_psycopg, mock_conn, _ = _make_psycopg_mock(
            ["idx_priv_embedding_hnsw", "idx_hive_embedding_hnsw"]
        )
        monkeypatch.setitem(sys.modules, "psycopg", mock_psycopg)

        from tapps_brain.gc import run_hnsw_maintenance

        run_hnsw_maintenance("postgresql://localhost/test", index_table_pairs=pairs)

        calls = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
        reindex_positions = [i for i, s in enumerate(calls) if "REINDEX" in s]
        vacuum_positions = [i for i, s in enumerate(calls) if "VACUUM" in s]
        assert reindex_positions, "No REINDEX calls found"
        assert vacuum_positions, "No VACUUM calls found"
        assert max(reindex_positions) < min(vacuum_positions), (
            "REINDEX calls must all precede VACUUM calls"
        )

    def test_skipped_index_not_reindexed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Indexes absent from pg_class are skipped; no REINDEX or VACUUM issued."""
        pairs = [
            ("idx_priv_embedding_hnsw", "private_memories"),
            ("idx_hive_embedding_hnsw", "hive_memories"),
        ]
        # Only private index exists; hive index is absent (migration not applied).
        mock_psycopg, mock_conn, _ = _make_psycopg_mock(["idx_priv_embedding_hnsw"])
        monkeypatch.setitem(sys.modules, "psycopg", mock_psycopg)

        from tapps_brain.gc import run_hnsw_maintenance

        result = run_hnsw_maintenance("postgresql://localhost/test", index_table_pairs=pairs)

        assert result.reindexed_indexes == ["idx_priv_embedding_hnsw"]
        assert result.skipped_indexes == ["idx_hive_embedding_hnsw"]
        assert result.vacuumed_tables == ["private_memories"]
        # hive_memories must NOT appear in any execute call
        execute_calls = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
        assert not any("hive_memories" in s for s in execute_calls)

    def test_no_indexes_found_returns_empty_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no indexes exist, skipped_indexes is populated and no SQL is run."""
        pairs = [("idx_priv_embedding_hnsw", "private_memories")]
        mock_psycopg, mock_conn, _ = _make_psycopg_mock([])  # no indexes found
        monkeypatch.setitem(sys.modules, "psycopg", mock_psycopg)

        from tapps_brain.gc import run_hnsw_maintenance

        result = run_hnsw_maintenance("postgresql://localhost/test", index_table_pairs=pairs)

        assert result.reindexed_indexes == []
        assert result.skipped_indexes == ["idx_priv_embedding_hnsw"]
        assert result.vacuumed_tables == []
        assert result.error is None
        # conn.execute should not have been called with REINDEX or VACUUM
        execute_calls = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
        assert not any("REINDEX" in s or "VACUUM" in s for s in execute_calls)

    def test_table_vacuumed_once_when_two_indexes_share_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A table shared by two indexes is only vacuumed once."""
        pairs = [
            ("idx_one_hnsw", "shared_table"),
            ("idx_two_hnsw", "shared_table"),
        ]
        mock_psycopg, mock_conn, _ = _make_psycopg_mock(["idx_one_hnsw", "idx_two_hnsw"])
        monkeypatch.setitem(sys.modules, "psycopg", mock_psycopg)

        from tapps_brain.gc import run_hnsw_maintenance

        result = run_hnsw_maintenance("postgresql://localhost/test", index_table_pairs=pairs)

        vacuum_calls = [c for c in mock_conn.execute.call_args_list if "VACUUM" in str(c.args[0])]
        assert len(vacuum_calls) == 1
        assert result.vacuumed_tables == ["shared_table"]

    def test_connect_called_with_autocommit_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """psycopg.connect must be invoked with autocommit=True."""
        mock_psycopg, _, _ = _make_psycopg_mock([])
        monkeypatch.setitem(sys.modules, "psycopg", mock_psycopg)

        from tapps_brain.gc import run_hnsw_maintenance

        run_hnsw_maintenance("postgresql://localhost/test")

        mock_psycopg.connect.assert_called_once()
        _, kwargs = mock_psycopg.connect.call_args
        assert kwargs.get("autocommit") is True, (
            "autocommit=True is required for REINDEX CONCURRENTLY and VACUUM"
        )


# ---------------------------------------------------------------------------
# Tests: default pair list
# ---------------------------------------------------------------------------


class TestHNSWIndexTablePairs:
    """Sanity-check the module-level _HNSW_INDEX_TABLE_PAIRS constant."""

    def test_constant_covers_three_planes(self) -> None:
        from tapps_brain.gc import _HNSW_INDEX_TABLE_PAIRS

        assert len(_HNSW_INDEX_TABLE_PAIRS) == 3

    def test_constant_includes_private_index(self) -> None:
        from tapps_brain.gc import _HNSW_INDEX_TABLE_PAIRS

        index_names = [idx for idx, _ in _HNSW_INDEX_TABLE_PAIRS]
        assert "idx_priv_embedding_hnsw" in index_names

    def test_constant_includes_hive_index(self) -> None:
        from tapps_brain.gc import _HNSW_INDEX_TABLE_PAIRS

        index_names = [idx for idx, _ in _HNSW_INDEX_TABLE_PAIRS]
        assert "idx_hive_embedding_hnsw" in index_names

    def test_constant_includes_federation_index(self) -> None:
        from tapps_brain.gc import _HNSW_INDEX_TABLE_PAIRS

        index_names = [idx for idx, _ in _HNSW_INDEX_TABLE_PAIRS]
        assert "idx_fed_embedding_hnsw" in index_names


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------


class TestRunHNSWMaintenanceErrors:
    """Error paths: DB connection failure, psycopg import error."""

    def test_db_error_sets_error_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A DB exception is caught and reported in result.error (no re-raise)."""
        mock_psycopg = MagicMock()
        mock_psycopg.connect.side_effect = RuntimeError("connection refused")
        monkeypatch.setitem(sys.modules, "psycopg", mock_psycopg)

        from tapps_brain.gc import run_hnsw_maintenance

        result = run_hnsw_maintenance("postgresql://localhost/test")

        assert result.error is not None
        assert "connection refused" in result.error
        assert result.reindexed_indexes == []

    def test_duration_is_positive_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """duration_seconds is set even when an error occurs."""
        mock_psycopg = MagicMock()
        mock_psycopg.connect.side_effect = RuntimeError("boom")
        monkeypatch.setitem(sys.modules, "psycopg", mock_psycopg)

        from tapps_brain.gc import run_hnsw_maintenance

        result = run_hnsw_maintenance("postgresql://localhost/test")
        assert result.duration_seconds >= 0.0

    def test_result_has_no_error_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """result.error is None on a successful run."""
        mock_psycopg, _, _ = _make_psycopg_mock(["idx_priv_embedding_hnsw"])
        monkeypatch.setitem(sys.modules, "psycopg", mock_psycopg)

        from tapps_brain.gc import run_hnsw_maintenance

        result = run_hnsw_maintenance(
            "postgresql://localhost/test",
            index_table_pairs=[("idx_priv_embedding_hnsw", "private_memories")],
        )
        assert result.error is None
        assert result.dry_run is False


# ---------------------------------------------------------------------------
# Tests: HNSWMaintenanceResult model
# ---------------------------------------------------------------------------


class TestHNSWMaintenanceResult:
    """Pydantic model validation for HNSWMaintenanceResult."""

    def test_defaults(self) -> None:
        from tapps_brain.gc import HNSWMaintenanceResult

        r = HNSWMaintenanceResult()
        assert r.reindexed_indexes == []
        assert r.skipped_indexes == []
        assert r.vacuumed_tables == []
        assert r.dry_run is False
        assert r.duration_seconds == pytest.approx(0.0)
        assert r.error is None

    def test_model_dump_mode_json(self) -> None:
        from tapps_brain.gc import HNSWMaintenanceResult

        r = HNSWMaintenanceResult(
            reindexed_indexes=["idx_priv_embedding_hnsw"],
            vacuumed_tables=["private_memories"],
            duration_seconds=1.23,
        )
        d = r.model_dump(mode="json")
        assert d["reindexed_indexes"] == ["idx_priv_embedding_hnsw"]
        assert d["vacuumed_tables"] == ["private_memories"]
        assert d["duration_seconds"] == pytest.approx(1.23)
        assert d["error"] is None
