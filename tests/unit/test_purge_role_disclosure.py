"""Regression tests for TAP-5895 — purge-test-tenants role disclosure.

A row count taken over an RLS-scoped Postgres connection returns a CONFIDENT,
PLAUSIBLE, WRONG zero: RLS silently filters rows the connected role cannot
see, and nothing distinguishes that zero from an authoritative zero taken as
the table owner. These tests assert the two cases produce DIFFERENT verdict
labels for the identical underlying row state — never that they agree. A test
asserting sameness here would lock the disclosure bug in as correct behaviour.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from tapps_brain.cli.maintenance import _probe_connection_role, _purge_verdict


class _FakeCursor:
    """Records the SQL it was asked to execute and returns a canned row."""

    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.executed: list[str] = []
        self._row = row

    def execute(self, sql: str, *_params: object) -> None:
        self.executed.append(sql)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    @contextmanager
    def cursor(self) -> Iterator[_FakeCursor]:
        yield self._cursor


class _FakeConnectionManager:
    """Duck-types the subset of PostgresConnectionManager _probe_connection_role uses."""

    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.cursor = _FakeCursor(row)
        self._conn = _FakeConnection(self.cursor)

    @contextmanager
    def get_connection(self) -> Iterator[_FakeConnection]:
        yield self._conn


class TestProbeConnectionRole:
    """_probe_connection_role must read the actually-connected role, not guess."""

    def test_runtime_role_with_rls_enforced_is_scoped(self) -> None:
        # rolname, rolsuper, rolbypassrls — an ordinary runtime role.
        cm = _FakeConnectionManager(row=("tapps_runtime", False, False))

        role, rls_scoped = _probe_connection_role(cm)

        assert role == "tapps_runtime"
        assert rls_scoped is True
        # Mechanism check (VAL-05 positive control): the probe actually queried
        # pg_roles for the connected role rather than returning a hardcoded verdict.
        assert "pg_roles" in cm.cursor.executed[0]
        assert "current_user" in cm.cursor.executed[0]

    def test_bypassrls_role_is_not_scoped(self) -> None:
        cm = _FakeConnectionManager(row=("tapps_owner", False, True))

        role, rls_scoped = _probe_connection_role(cm)

        assert role == "tapps_owner"
        assert rls_scoped is False

    def test_superuser_role_is_not_scoped(self) -> None:
        cm = _FakeConnectionManager(row=("postgres", True, False))

        role, rls_scoped = _probe_connection_role(cm)

        assert role == "postgres"
        assert rls_scoped is False

    def test_missing_pg_roles_row_is_treated_as_scoped(self) -> None:
        """Unknown must never read as clean — fail toward 'scoped', not 'authoritative'."""
        cm = _FakeConnectionManager(row=None)

        role, rls_scoped = _probe_connection_role(cm)

        assert role == "(unknown)"
        assert rls_scoped is True

    def test_two_roles_over_the_same_fake_row_state_yield_distinct_role_names(self) -> None:
        """VAL-05 positive control: same shape of probe, two roles, two distinct outputs —
        proves the label tracks the connected role rather than a constant."""
        scoped_cm = _FakeConnectionManager(row=("tapps_runtime", False, False))
        owner_cm = _FakeConnectionManager(row=("tapps_owner", False, True))

        scoped_role, scoped_rls = _probe_connection_role(scoped_cm)
        owner_role, owner_rls = _probe_connection_role(owner_cm)

        assert scoped_role != owner_role
        assert scoped_rls != owner_rls


class TestPurgeVerdictLabelsDifferForScopedVsOwner:
    """Box 5 — the regression test most likely to be written backwards.

    These assert the scoped and owner verdicts DIFFER for the SAME underlying
    row state (total_rows=0). Do not "fix" this test to assert equality — that
    is exactly the defect TAP-5895 describes.
    """

    def test_zero_rows_scoped_vs_owner_produce_different_verdicts(self) -> None:
        same_total_rows = 0

        scoped_verdict = _purge_verdict(total_rows=same_total_rows, apply=False, rls_scoped=True)
        owner_verdict = _purge_verdict(total_rows=same_total_rows, apply=False, rls_scoped=False)

        assert scoped_verdict != owner_verdict, (
            "scoped and owner zeros must never share a verdict label — "
            f"both produced {scoped_verdict!r}"
        )
        assert scoped_verdict == "scoped-zero"
        assert owner_verdict == "authoritative-zero"

    def test_nonzero_rows_do_not_need_the_scoped_label(self) -> None:
        """Once rows are found, the RLS-scope caveat is moot — both roles agree."""
        scoped_verdict = _purge_verdict(total_rows=3, apply=False, rls_scoped=True)
        owner_verdict = _purge_verdict(total_rows=3, apply=False, rls_scoped=False)

        assert scoped_verdict == owner_verdict == "rows-found"

    def test_apply_mode_reports_purged_regardless_of_rls_scope(self) -> None:
        assert _purge_verdict(total_rows=5, apply=True, rls_scoped=True) == "purged"
        assert _purge_verdict(total_rows=5, apply=True, rls_scoped=False) == "purged"
