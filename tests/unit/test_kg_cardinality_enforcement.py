"""TAP-5510: cardinality enforced on the edge write path.

TAP-5509 gave callers a check. A check without write enforcement is advisory:
an agent that skips it can write the edge anyway, so the ledger guarantees
nothing. This closes that by gating the insert itself.

Two invariants carry the weight: reinforcing an existing edge is never blocked
(it adds no object), and a violation raises inside the caller's transaction so
the whole experience write rolls back rather than landing half-applied.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tapps_brain.errors import ConflictError
from tapps_brain.postgres_kg import PostgresKnowledgeGraphStore


class _FakeCursor:
    """Replays scripted rows and records the SQL it was handed."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.executed: list[str] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append(sql)

    def fetchone(self) -> Any:
        return self._responses.pop(0) if self._responses else None


def _store(*, strict: bool = False) -> PostgresKnowledgeGraphStore:
    return PostgresKnowledgeGraphStore(
        MagicMock(),
        project_id="proj",
        brain_id="brain",
        evidence_required=False,
        strict_predicates=strict,
    )


def _predicate_row(max_count: int | None) -> tuple[Any, ...]:
    """A GET_PREDICATE_SQL row: [0]=id [1]=predicate [2]=max_count ..."""
    return ("uuid-1", "refunded", max_count, None, None, None, "tester", None, None)


class TestEnforcement:
    def test_at_the_limit_raises(self) -> None:
        cur = _FakeCursor([_predicate_row(1), (1,)])
        with pytest.raises(ConflictError, match="max_count=1"):
            _store()._assert_cardinality_allows(cur, "subj-1", "refunded")

    def test_over_the_limit_raises(self) -> None:
        cur = _FakeCursor([_predicate_row(2), (7,)])
        with pytest.raises(ConflictError):
            _store()._assert_cardinality_allows(cur, "subj-1", "refunded")

    def test_below_the_limit_passes(self) -> None:
        cur = _FakeCursor([_predicate_row(3), (1,)])
        _store()._assert_cardinality_allows(cur, "subj-1", "refunded")

    def test_error_names_the_limit_and_the_count(self) -> None:
        """A rollback the caller cannot explain is a rollback they will retry."""
        cur = _FakeCursor([_predicate_row(1), (1,)])
        with pytest.raises(ConflictError) as excinfo:
            _store()._assert_cardinality_allows(cur, "subj-1", "refunded")
        message = str(excinfo.value)
        assert "refunded" in message
        assert "max_count=1" in message
        assert "1 object(s)" in message

    def test_conflict_error_maps_to_409(self) -> None:
        """Cardinality breach is a state conflict, not a malformed request."""
        from tapps_brain.errors import ErrorCode, http_status

        assert ConflictError.error_code is ErrorCode.CONFLICT
        assert http_status(ErrorCode.CONFLICT) == 409


class TestOpenByDefault:
    def test_unregistered_predicate_passes_in_open_mode(self) -> None:
        """The registry stays open by default; enforcement is per declaration."""
        cur = _FakeCursor([None])
        _store(strict=False)._assert_cardinality_allows(cur, "subj-1", "anything")

    def test_registered_without_max_count_passes(self) -> None:
        cur = _FakeCursor([_predicate_row(None)])
        _store()._assert_cardinality_allows(cur, "subj-1", "refunded")

    def test_unbounded_predicate_does_not_even_count(self) -> None:
        """No point scanning edges for a predicate with nothing to enforce."""
        cur = _FakeCursor([_predicate_row(None)])
        _store()._assert_cardinality_allows(cur, "subj-1", "refunded")
        assert len(cur.executed) == 1


class TestStrictMode:
    def test_unregistered_predicate_raises_in_strict_mode(self) -> None:
        cur = _FakeCursor([None])
        with pytest.raises(ConflictError, match="strict mode"):
            _store(strict=True)._assert_cardinality_allows(cur, "subj-1", "anything")

    def test_registered_predicate_still_passes_in_strict_mode(self) -> None:
        cur = _FakeCursor([_predicate_row(None)])
        _store(strict=True)._assert_cardinality_allows(cur, "subj-1", "refunded")

    def test_strict_mode_still_enforces_max_count(self) -> None:
        cur = _FakeCursor([_predicate_row(1), (1,)])
        with pytest.raises(ConflictError, match="max_count"):
            _store(strict=True)._assert_cardinality_allows(cur, "subj-1", "refunded")

    def test_strict_defaults_off(self) -> None:
        """Turning rejection on by default would break every existing writer."""
        store = PostgresKnowledgeGraphStore(
            MagicMock(), project_id="p", brain_id="b", evidence_required=False
        )
        assert store._strict_predicates is False


class TestStrictModeResolution:
    def test_resolution_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A profile that cannot be read means the project never asked for strict."""
        from tapps_brain.services import kg_service

        monkeypatch.setattr(
            kg_service,
            "resolve_profile",
            MagicMock(side_effect=OSError("boom")),
            raising=False,
        )
        monkeypatch.setenv("TAPPS_BRAIN_PROJECT_DIR", "/nonexistent-path-xyz")
        assert kg_service._resolve_strict_predicates() is False

    def test_result_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Profile resolution reads YAML; it must not sit on the write path."""
        from tapps_brain.services import kg_service

        monkeypatch.setattr(kg_service, "_STRICT_PREDICATES", None)
        calls = MagicMock(return_value=True)
        monkeypatch.setattr(kg_service, "_resolve_strict_predicates", calls)
        assert kg_service._strict_predicates_enabled() is True
        assert kg_service._strict_predicates_enabled() is True
        assert calls.call_count == 1
        monkeypatch.setattr(kg_service, "_STRICT_PREDICATES", None)


class TestReinforcementIsExempt:
    def test_enforcement_is_not_reached_when_an_edge_already_exists(self) -> None:
        """upsert_edge reinforces and returns before the gate.

        Pinned structurally: the gate call must sit in the else-branch of the
        existing-edge check, so a re-assert can never be blocked by a limit the
        subject is already at.
        """
        import inspect

        source = inspect.getsource(PostgresKnowledgeGraphStore.upsert_edge)
        gate = source.index("_assert_cardinality_allows")
        else_branch = source.index("else:")
        reinforce = source.index("if existing is not None:")
        assert reinforce < else_branch < gate, "cardinality gate must run only on the new-edge path"

    def test_gate_runs_before_the_insert(self) -> None:
        """Checking after the insert would commit the violation it detects."""
        import inspect

        source = inspect.getsource(PostgresKnowledgeGraphStore.upsert_edge)
        assert source.index("_assert_cardinality_allows") < source.index("INSERT_EDGE_SQL")
