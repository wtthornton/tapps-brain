"""Unit tests for tenant-purge helpers (TAP-4465).

Covers the pure prefix logic and the empty-input no-op (which must not touch
the database). DB-backed deletion is exercised in
``tests/integration/test_maintenance_purge.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from tapps_brain.maintenance_purge import (
    RESERVED_TEST_PROJECT_PREFIXES,
    is_reserved_test_project,
    purge_by_prefix,
    purge_projects,
)


class TestReservedPrefix:
    def test_smoke_and_test_are_reserved(self) -> None:
        assert is_reserved_test_project("smoke-abc123")
        assert is_reserved_test_project("test-proj-9f")

    def test_real_project_is_not_reserved(self) -> None:
        assert not is_reserved_test_project("tapps-brain")
        assert not is_reserved_test_project("47b68539070a3423")

    def test_reserved_prefixes_constant(self) -> None:
        assert "smoke-" in RESERVED_TEST_PROJECT_PREFIXES
        assert "test-" in RESERVED_TEST_PROJECT_PREFIXES


class TestPurgeNoOps:
    def test_purge_projects_empty_is_noop(self) -> None:
        cm = MagicMock()
        assert purge_projects(cm, []) == {}
        cm.admin_context.assert_not_called()
        cm.get_connection.assert_not_called()

    def test_purge_projects_blank_ids_filtered(self) -> None:
        cm = MagicMock()
        assert purge_projects(cm, ["", ""]) == {}
        cm.admin_context.assert_not_called()

    def test_purge_by_prefix_empty_is_noop(self) -> None:
        cm = MagicMock()
        assert purge_by_prefix(cm, ()) == {}
        cm.admin_context.assert_not_called()
