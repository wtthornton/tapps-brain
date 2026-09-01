"""The promotion-state filter, executed against a real Postgres (TAP-6826).

``tests/unit/test_recall_learning_status.py`` asserts the SQL *shape* and the
service wiring with a double.  This module runs the same clause against a live
database under row-level security, because the defect being fixed is precisely
"the field exists on the model and in the query layer but does not reach the
wire" — a shape assertion alone could pass on a clause Postgres rejects.

It lives under ``tests/integration/`` deliberately: CI runs ``tests/unit/``,
``tests/integration/`` and ``tests/compat/`` and nothing else, so a
Postgres-backed test parked at the ``tests/`` root would never execute there —
and a test CI never runs is not evidence about CI.

Skip-free (see ``tests/_pg_fixture.py``): CI provides a DSN, and locally a
throwaway ``pgvector`` container is started.  Never the deployed
``tapps-brain-db`` — ``resolve_fixture_dsn`` refuses a production-named DSN.

The negative control is the load-bearing part.  ``test_unfiltered_recall_is_the
_pre_change_result`` shows the unpromoted rows *are* returned without the
filter, so the filtered run's smaller count is the filter working and not the
fixture failing to write rows.
"""

from __future__ import annotations

import uuid

import pytest

from tapps_brain.models import LearningStatus, MemoryEntry, PromotionSignal
from tapps_brain.postgres_connection import PostgresConnectionManager
from tapps_brain.postgres_private import PostgresPrivateBackend
from tapps_brain.services import memory_service
from tapps_brain.store import ConsolidationConfig, MemoryStore
from tests._pg_fixture import ensure_rls_role, resolve_fixture_dsn

_AGENT = "tap6826-agent"
_QUERY = "phosphorescent kingfisher telemetry"
_RLS_ROLE = "tap6826_recall_probe"
_RLS_PASSWORD = "tap6826-fixture-only"  # disposable fixture container, never a real credential


@pytest.fixture(scope="module")
def owner_dsn() -> str:
    return resolve_fixture_dsn()


@pytest.fixture(scope="module")
def dsn(owner_dsn: str) -> str:
    """A non-privileged role, so recall runs with RLS actually in force.

    ``PostgresConnectionManager`` refuses to pool a superuser connection
    (``_assert_non_privileged_role``), and a superuser bypasses every policy —
    so the filter must be shown working *under* RLS, not around it.
    """
    role_dsn = ensure_rls_role(owner_dsn, role=_RLS_ROLE, password=_RLS_PASSWORD, writable=True)
    # ``MemoryStore`` bootstraps ``private_relations`` at runtime
    # (``_postgres_private_sql.py``), so the deployed runtime role necessarily
    # holds CREATE on ``public``.  The fixture role must match that shape or the
    # store cannot be constructed at all — this grants the privilege production
    # already has, it does not relax the RLS the tests depend on.
    import psycopg
    from psycopg import sql as _pgsql

    with psycopg.connect(owner_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            _pgsql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(_pgsql.Identifier(_RLS_ROLE))
        )
    return role_dsn


@pytest.fixture(scope="module")
def cm(dsn: str):
    manager = PostgresConnectionManager(dsn)
    yield manager
    manager.close()


@pytest.fixture()
def project_id() -> str:
    return f"tap6826-{uuid.uuid4().hex[:10]}"


def _entry(
    key: str, status: LearningStatus, *, confidence: float, access_count: int
) -> MemoryEntry:
    """A row the real schema will actually accept.

    ``private_memories_approved_needs_provenance`` (migration 030) refuses an
    ``approved`` row with no ``promoted_by`` / ``promoted_at`` /
    ``promotion_signal``, so the fixture has to carry promotion provenance —
    which is the point: an approved row in production always does.
    """
    provenance = (
        {
            "promoted_by": "tap6826-eval",
            "promoted_at": "2026-08-01T00:00:00+00:00",
            "promotion_signal": PromotionSignal.eval,
        }
        if status is LearningStatus.approved
        else {}
    )
    return MemoryEntry(
        key=key,
        value=f"{_QUERY} — {key}",
        tier="pattern",
        learning_status=status,
        confidence=confidence,
        access_count=access_count,
        **provenance,
    )


@pytest.fixture()
def seeded(cm, project_id: str, tmp_path) -> tuple[PostgresPrivateBackend, MemoryStore]:
    """Six candidates that outrank three approved learnings on every signal.

    The inversion is deliberate: it is the only arrangement in which a Python
    post-filter applied after the ``max_results`` cut differs observably from a
    SQL pre-filter, and reproducing that difference is the point of the suite.
    """
    backend = PostgresPrivateBackend(cm, project_id=project_id, agent_id=_AGENT)
    backend.save_many(
        [
            *(
                _entry(f"cand-{i}", LearningStatus.candidate, confidence=0.99, access_count=100)
                for i in range(6)
            ),
            *(
                _entry(f"appr-{i}", LearningStatus.approved, confidence=0.10, access_count=0)
                for i in range(3)
            ),
        ]
    )
    store = MemoryStore(
        tmp_path / project_id,
        agent_id=_AGENT,
        private_backend=backend,
        consolidation_config=ConsolidationConfig(enabled=False),
    )
    yield backend, store
    store.close()


class TestSqlExecutes:
    """The clause is valid SQL against the real schema, not just a string."""

    def test_backend_search_honours_the_filter(self, seeded) -> None:
        backend, _ = seeded
        unfiltered = backend.search(_QUERY)
        filtered = backend.search(_QUERY, learning_status=["approved"])
        assert len(unfiltered) == 9
        assert {e.key for e in filtered} == {"appr-0", "appr-1", "appr-2"}
        assert len(filtered) < len(unfiltered)

    def test_multi_status_binds_as_a_set(self, seeded) -> None:
        backend, _ = seeded
        both = backend.search(_QUERY, learning_status=["approved", "candidate"])
        assert len(both) == 9
        demoted_only = backend.search(_QUERY, learning_status=["demoted"])
        assert demoted_only == []


class TestDiscriminatingControl:
    """Filtered recall must return strictly fewer rows than unfiltered."""

    def test_unfiltered_recall_is_the_pre_change_result(self, seeded) -> None:
        """Negative control: without the argument every row still comes back."""
        _, store = seeded
        results = memory_service.brain_recall(store, "proj", _AGENT, query=_QUERY, max_results=50)
        assert len(results) == 9
        assert {item["learning_status"] for item in results} == {"candidate", "approved"}

    def test_filtered_recall_returns_strictly_fewer(self, seeded) -> None:
        _, store = seeded
        unfiltered = memory_service.brain_recall(
            store, "proj", _AGENT, query=_QUERY, max_results=50
        )
        filtered = memory_service.brain_recall(
            store,
            "proj",
            _AGENT,
            query=_QUERY,
            max_results=50,
            filter_learning_status=["approved"],
        )
        assert len(filtered) == 3 < len(unfiltered) == 9
        assert {item["key"] for item in filtered} == {"appr-0", "appr-1", "appr-2"}


class TestMaxResultsInteraction:
    """The exact failure a Python post-filter would introduce."""

    def test_control_unpromoted_rows_win_the_unfiltered_cut(self, seeded) -> None:
        _, store = seeded
        results = memory_service.brain_recall(store, "proj", _AGENT, query=_QUERY, max_results=3)
        assert all(item["learning_status"] == "candidate" for item in results)

    def test_filter_still_returns_the_requested_count(self, seeded) -> None:
        """Post-filtering after the cut would return 0 here, not 3."""
        _, store = seeded
        results = memory_service.brain_recall(
            store,
            "proj",
            _AGENT,
            query=_QUERY,
            max_results=3,
            filter_learning_status=["approved"],
        )
        assert len(results) == 3
        assert all(item["learning_status"] == "approved" for item in results)


class TestStatusIsReported:
    def test_promotion_state_reaches_every_result(self, seeded) -> None:
        _, store = seeded
        results = memory_service.brain_recall(store, "proj", _AGENT, query=_QUERY, max_results=50)
        by_key = {item["key"]: item["learning_status"] for item in results}
        assert by_key["appr-0"] == "approved"
        assert by_key["cand-0"] == "candidate"
