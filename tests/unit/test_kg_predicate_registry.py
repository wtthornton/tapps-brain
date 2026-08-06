"""TAP-5508: KG predicate registry with cardinality metadata.

Brain stores free-form predicates. This registry lets a project declare what
one *means* — chiefly ``max_count``, so a consumer can ask deterministic ledger
questions like "has this order been refunded more than once".

Two properties carry the weight: the registry is open by default (an
unregistered predicate stays writable), and it is tenant-scoped (one project
cannot see or overwrite another's declarations).

The story asks for an integration test covering tenant isolation. The
Postgres-backed integration suite is parked (TAP-5459), so isolation is covered
here at the two layers a unit test can reach honestly: the SQL is asserted to
carry a tenant predicate on every statement, and the migration is asserted to
enable FORCE RLS. Neither replaces a live-cluster test; both fail loudly if the
tenant scoping is dropped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tapps_brain import _postgres_kg_sql as _sql
from tapps_brain.services import kg_service

_PRIVATE = Path(__file__).resolve().parents[2] / "src" / "tapps_brain" / "migrations" / "private"
_UP = _PRIVATE / "032_kg_predicate_registry.sql"
_DOWN = _PRIVATE / "032_kg_predicate_registry.down.sql"


class _FakeKGStore:
    """Records what the service layer asked the store to do."""

    def __init__(self) -> None:
        self.registered: list[dict[str, Any]] = []
        self.predicates: list[dict[str, Any]] = []

    def register_predicate(self, **kwargs: Any) -> dict[str, Any]:
        self.registered.append(kwargs)
        stored = {"id": "uuid-1", **kwargs}
        self.predicates.append(stored)
        return stored

    def list_predicates(self) -> list[dict[str, Any]]:
        return list(self.predicates)


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _FakeKGStore:
    store = _FakeKGStore()
    monkeypatch.setattr(kg_service, "_kg_store", lambda *_a, **_kw: store)
    return store


class TestRegisterPredicate:
    def test_registers_with_cardinality(self, fake_store: _FakeKGStore) -> None:
        result = kg_service.register_predicate(
            None, "proj", "brain", predicate="refunded", max_count=1
        )
        assert result["registered"] is True
        assert result["predicate"]["max_count"] == 1
        assert fake_store.registered[0]["predicate"] == "refunded"

    def test_omitted_max_count_means_unbounded(self, fake_store: _FakeKGStore) -> None:
        """Unbounded is the permissive default, so it must survive as None."""
        kg_service.register_predicate(None, "proj", "brain", predicate="mentions")
        assert fake_store.registered[0]["max_count"] is None

    def test_optional_domain_and_range_are_recorded(self, fake_store: _FakeKGStore) -> None:
        kg_service.register_predicate(
            None,
            "proj",
            "brain",
            predicate="refunded",
            domain_type="order",
            range_type="payment",
        )
        call = fake_store.registered[0]
        assert call["domain_type"] == "order"
        assert call["range_type"] == "payment"

    def test_empty_strings_become_null_not_empty(self, fake_store: _FakeKGStore) -> None:
        """An unset domain is absent, not the empty string — readers check for null."""
        kg_service.register_predicate(None, "proj", "brain", predicate="p", domain_type="")
        assert fake_store.registered[0]["domain_type"] is None

    def test_predicate_is_trimmed(self, fake_store: _FakeKGStore) -> None:
        kg_service.register_predicate(None, "proj", "brain", predicate="  refunded  ")
        assert fake_store.registered[0]["predicate"] == "refunded"


class TestRegisterValidation:
    @pytest.mark.parametrize("bad", ["", "   "])
    def test_blank_predicate_is_rejected(self, bad: str, fake_store: _FakeKGStore) -> None:
        result = kg_service.register_predicate(None, "proj", "brain", predicate=bad)
        assert result["error"] == "invalid_request"
        assert fake_store.registered == []

    @pytest.mark.parametrize("bad", [0, -1, 10_001])
    def test_out_of_range_max_count_is_rejected(self, bad: int, fake_store: _FakeKGStore) -> None:
        """max_count=0 would declare a predicate that may never hold."""
        result = kg_service.register_predicate(None, "proj", "brain", predicate="p", max_count=bad)
        assert result["error"] == "invalid_request"
        assert fake_store.registered == []

    def test_one_is_accepted_as_a_functional_edge(self, fake_store: _FakeKGStore) -> None:
        result = kg_service.register_predicate(None, "proj", "brain", predicate="p", max_count=1)
        assert result["registered"] is True


class TestListPredicates:
    def test_empty_registry_is_not_an_error(self, fake_store: _FakeKGStore) -> None:
        """A project that has declared nothing is open by default, not broken."""
        result = kg_service.list_predicates(None, "proj", "brain")
        assert result == {"count": 0, "predicates": []}
        assert "error" not in result

    def test_lists_what_was_registered(self, fake_store: _FakeKGStore) -> None:
        kg_service.register_predicate(None, "proj", "brain", predicate="a", max_count=1)
        kg_service.register_predicate(None, "proj", "brain", predicate="b")
        result = kg_service.list_predicates(None, "proj", "brain")
        assert result["count"] == 2
        assert {p["predicate"] for p in result["predicates"]} == {"a", "b"}


class TestTenantIsolationSql:
    """Every registry statement must be tenant-scoped in the SQL itself."""

    @pytest.mark.parametrize(
        "sql_name",
        ["UPSERT_PREDICATE_SQL", "LIST_PREDICATES_SQL", "GET_PREDICATE_SQL"],
    )
    def test_statement_filters_or_writes_tenant_id(self, sql_name: str) -> None:
        sql = getattr(_sql, sql_name)
        assert "tenant_id" in sql, f"{sql_name} has no tenant scoping"

    def test_reads_filter_on_tenant_and_brain(self) -> None:
        for sql in (_sql.LIST_PREDICATES_SQL, _sql.GET_PREDICATE_SQL):
            assert "WHERE tenant_id = %s AND brain_id = %s" in sql

    def test_upsert_conflict_target_is_tenant_scoped(self) -> None:
        """A conflict target without tenant_id would let one project's re-register
        overwrite another's row for the same predicate name."""
        assert "ON CONFLICT (tenant_id, brain_id, predicate)" in _sql.UPSERT_PREDICATE_SQL


class TestMigrationShape:
    def test_up_and_down_exist(self) -> None:
        assert _UP.is_file(), f"missing: {_UP}"
        assert _DOWN.is_file(), f"missing: {_DOWN}"

    def test_creates_registry_with_cardinality_and_optional_domain_range(self) -> None:
        body = _UP.read_text()
        assert "CREATE TABLE IF NOT EXISTS kg_predicates" in body
        for column in ("predicate", "max_count", "domain_type", "range_type"):
            assert column in body, f"missing column: {column}"

    def test_max_count_zero_is_unrepresentable(self) -> None:
        """A max_count of 0 is a deletion expressed as a constraint."""
        body = _UP.read_text()
        assert "kg_predicates_max_count_positive" in body
        assert "max_count IS NULL OR max_count >= 1" in body

    def test_rls_is_enabled_and_forced(self) -> None:
        """FORCE matters: without it the table owner bypasses tenant isolation."""
        body = _UP.read_text()
        assert "ALTER TABLE kg_predicates ENABLE ROW LEVEL SECURITY" in body
        assert "ALTER TABLE kg_predicates FORCE ROW LEVEL SECURITY" in body
        assert "kg_predicates_tenant_isolation" in body
        assert "tenant_id = current_setting('app.project_id', TRUE)" in body

    def test_rls_policy_is_fail_closed(self) -> None:
        """An unset app.project_id must match nothing, not everything."""
        body = _UP.read_text()
        assert "current_setting('app.project_id', TRUE) IS NOT NULL" in body

    def test_one_declaration_per_predicate_per_brain(self) -> None:
        body = _UP.read_text()
        assert "uix_kg_predicates_brain_predicate" in body
        assert "(tenant_id, brain_id, predicate)" in body

    def test_up_is_rerunnable(self) -> None:
        body = _UP.read_text()
        assert "CREATE TABLE IF NOT EXISTS" in body
        assert "CREATE UNIQUE INDEX IF NOT EXISTS" in body
        assert "DROP POLICY IF EXISTS" in body

    def test_records_schema_version(self) -> None:
        body = _UP.read_text()
        assert "INSERT INTO private_schema_version" in body
        assert "VALUES (32," in body

    def test_down_reverses_everything(self) -> None:
        body = _DOWN.read_text()
        assert "DROP TABLE IF EXISTS kg_predicates" in body
        assert "DROP POLICY IF EXISTS kg_predicates_tenant_isolation" in body
        assert "DELETE FROM private_schema_version WHERE version = 32" in body

    def test_down_leaves_kg_edges_alone(self) -> None:
        """The registry describes predicates; it does not own the edges."""
        body = _DOWN.read_text()
        assert "DROP TABLE IF EXISTS kg_edges" not in body
        assert "DELETE FROM kg_edges" not in body


class TestOpenByDefault:
    def test_strict_predicates_defaults_off(self) -> None:
        """Rejecting unregistered predicates on introduction would break every
        current writer to buy a guarantee nobody asked for yet."""
        from tapps_brain.profile import KGProfileConfig

        assert KGProfileConfig().strict_predicates is False

    def test_strict_mode_is_opt_in_per_project(self) -> None:
        from tapps_brain.profile import KGProfileConfig

        assert KGProfileConfig(strict_predicates=True).strict_predicates is True

    def test_migration_does_not_constrain_kg_edges_predicate(self) -> None:
        """No FK from kg_edges.predicate — an unregistered predicate stays legal."""
        body = _UP.read_text()
        assert "REFERENCES kg_predicates" not in body
        assert "ALTER TABLE kg_edges" not in body
