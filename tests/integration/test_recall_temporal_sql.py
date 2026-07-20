"""TAP-4586: recall SQL excludes expired/superseded rows from top-K.

Proves the fix pushed the temporal/supersession predicate into the KNN and FTS
recall SQL (``_postgres_private_sql._LIVE_ROW_PREDICATE_SQL``), so stale rows no
longer occupy a top-K slot when live rows exist — even when the stale rows have
a *closer* vector distance / *higher* FTS rank than the live rows.

Complements the Python-side ``is_temporally_valid`` defense-in-depth filter in
``retrieval.py`` (which stays): this test asserts the *SQL layer* now does the
filtering so the budget is spent on live rows.

Also asserts RLS / ``project_id`` scoping is unchanged: a different project's
rows never leak into recall.

Requires: ``TAPPS_BRAIN_DATABASE_URL`` (skipped otherwise).
Mark: ``requires_postgres``
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")

# Embedding dimension used by the schema (migration 001: vector(384))
_EMBED_DIM = 384


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_backend(project_id: str, agent_id: str) -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_private import PostgresPrivateBackend

    cm = PostgresConnectionManager(_PG_DSN)
    return PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)


def _unique_project() -> str:
    return f"test-proj-{uuid.uuid4().hex[:8]}"


def _unique_agent() -> str:
    return f"test-agent-{uuid.uuid4().hex[:8]}"


def _make_entry(key: str, value: str, **kwargs: Any) -> Any:
    from tapps_brain.models import MemoryEntry

    return MemoryEntry(key=key, value=value, **kwargs)


def _unit_vector(index: int, dim: int = _EMBED_DIM) -> list[float]:
    """Unit vector with 1.0 at *index*, 0.0 elsewhere (predictable cosine dist)."""
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


def _set_embedding(backend: Any, key: str, embedding: list[float]) -> None:
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    with backend._cm.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE private_memories SET embedding = %s::vector "
            "WHERE project_id = %s AND agent_id = %s AND key = %s",
            (vec_str, backend._project_id, backend._agent_id, key),
        )


def _past_iso() -> str:
    return (datetime.now(tz=UTC) - timedelta(days=7)).isoformat()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _migrate() -> None:
    _apply_migrations()


@pytest.fixture
def backend() -> Any:
    b = _make_backend(_unique_project(), _unique_agent())
    yield b
    b.close()


# ---------------------------------------------------------------------------
# KNN recall — stale rows must not win top-K
# ---------------------------------------------------------------------------


class TestKnnExcludesStale:
    def test_expired_row_does_not_occupy_topk_slot(self, backend: Any) -> None:
        """An expired row with a *closer* vector than the live row must not win.

        Query vector points at index 5. The expired row is placed at the exact
        query vector (distance ~0 — would win top-K on distance), the live row
        is orthogonal (distance ~1). With k=1, only the live row may return.
        """
        query_vec = _unit_vector(5)

        # Expired row: closest possible vector, but valid_until is in the past.
        backend.save(_make_entry("knn-expired", "expired but closest", valid_until=_past_iso()))
        _set_embedding(backend, "knn-expired", _unit_vector(5))

        # Live row: farther vector, no expiry.
        backend.save(_make_entry("knn-live", "live but farther"))
        _set_embedding(backend, "knn-live", _unit_vector(6))

        results = backend.knn_search(query_vec, k=1)
        keys = [k for k, _ in results]
        assert "knn-expired" not in keys, "expired row leaked into top-K SQL slot"
        assert keys == ["knn-live"], f"expected only the live row, got {keys}"

    def test_superseded_row_does_not_occupy_topk_slot(self, backend: Any) -> None:
        query_vec = _unit_vector(3)

        # Superseded row: closest vector, but superseded_by points at another key.
        backend.save(
            _make_entry("knn-superseded", "superseded but closest", superseded_by="knn-live-2")
        )
        _set_embedding(backend, "knn-superseded", _unit_vector(3))

        backend.save(_make_entry("knn-live-2", "live replacement"))
        _set_embedding(backend, "knn-live-2", _unit_vector(4))

        results = backend.knn_search(query_vec, k=1)
        keys = [k for k, _ in results]
        assert "knn-superseded" not in keys, "superseded row leaked into top-K SQL slot"
        assert keys == ["knn-live-2"], f"expected only the live row, got {keys}"

    def test_live_row_with_empty_valid_until_is_returned(self, backend: Any) -> None:
        """The default live row (valid_until = '') must not be filtered out."""
        vec = _unit_vector(7)
        backend.save(_make_entry("knn-default-live", "default live row"))
        _set_embedding(backend, "knn-default-live", vec)

        results = backend.knn_search(vec, k=5)
        assert [k for k, _ in results] == ["knn-default-live"]

    def test_future_valid_until_is_still_live(self, backend: Any) -> None:
        """valid_until in the *future* means the row is still valid → returned."""
        future = (datetime.now(tz=UTC) + timedelta(days=7)).isoformat()
        vec = _unit_vector(8)
        backend.save(_make_entry("knn-future", "valid until future", valid_until=future))
        _set_embedding(backend, "knn-future", vec)

        results = backend.knn_search(vec, k=5)
        assert [k for k, _ in results] == ["knn-future"]

    def test_include_expired_true_surfaces_stale_rows(self, backend: Any) -> None:
        """The include_expired escape hatch (include_historical path) keeps stale rows."""
        query_vec = _unit_vector(9)
        backend.save(_make_entry("knn-hist-expired", "expired", valid_until=_past_iso()))
        _set_embedding(backend, "knn-hist-expired", _unit_vector(9))

        # Default: excluded.
        assert backend.knn_search(query_vec, k=5) == []
        # include_expired=True: surfaced (defense-in-depth Python filter decides after).
        keys = [k for k, _ in backend.knn_search(query_vec, k=5, include_expired=True)]
        assert keys == ["knn-hist-expired"]


# ---------------------------------------------------------------------------
# FTS recall — stale rows must not win top-K
# ---------------------------------------------------------------------------


class TestFtsExcludesStale:
    def test_expired_and_superseded_rows_excluded_from_fts(self, backend: Any) -> None:
        """Expired + superseded rows are dropped; the live row survives FTS recall."""
        backend.save(
            _make_entry("fts-expired", "kubernetes deployment guide", valid_until=_past_iso())
        )
        backend.save(
            _make_entry("fts-superseded", "kubernetes deployment guide", superseded_by="fts-live")
        )
        backend.save(_make_entry("fts-live", "kubernetes deployment guide"))

        results = backend.search("kubernetes deployment")
        keys = {e.key for e in results}
        assert "fts-expired" not in keys, "expired row leaked into FTS recall"
        assert "fts-superseded" not in keys, "superseded row leaked into FTS recall"
        assert "fts-live" in keys, "live row missing from FTS recall"

    def test_include_expired_true_surfaces_stale_rows_fts(self, backend: Any) -> None:
        backend.save(_make_entry("fts-hist", "prometheus alerting rules", valid_until=_past_iso()))

        # Default: excluded.
        assert backend.search("prometheus alerting") == []
        # include_expired=True: surfaced.
        keys = {e.key for e in backend.search("prometheus alerting", include_expired=True)}
        assert "fts-hist" in keys

    def test_as_of_point_in_time_still_returns_superseded_version(self, backend: Any) -> None:
        """A point-in-time (`as_of`) query must still see the version valid then.

        The live-row predicate must stand down for `as_of` so the bi-temporal
        window governs.  A superseded row (invalid_at set) that was valid at
        `as_of` is expected to be returned — the TAP-4586 predicate must not
        clobber this path.
        """
        now = datetime.now(tz=UTC)
        past = (now - timedelta(days=3)).isoformat()
        # Old version: valid in the past, invalidated & superseded now.
        backend.save(
            _make_entry(
                "asof-old",
                "elasticsearch cluster tuning",
                valid_at=past,
                invalid_at=now.isoformat(),
                superseded_by="asof-new",
            )
        )
        results = backend.search("elasticsearch cluster", as_of=past)
        keys = {e.key for e in results}
        assert "asof-old" in keys, "as_of point-in-time recall lost the superseded version"

    def test_as_of_point_in_time_knn_returns_superseded_version(self, backend: Any) -> None:
        """KNN must honour `as_of` the same way FTS does (hybrid dense parity)."""
        now = datetime.now(tz=UTC)
        past = (now - timedelta(days=3)).isoformat()
        vec = _unit_vector(12)
        backend.save(
            _make_entry(
                "asof-knn-old",
                "vector point-in-time row",
                valid_at=past,
                invalid_at=now.isoformat(),
                superseded_by="asof-knn-new",
            )
        )
        _set_embedding(backend, "asof-knn-old", vec)

        # Live-row default excludes the superseded version.
        assert backend.knn_search(vec, k=5) == []
        # Point-in-time: bi-temporal window stands live-row down.
        keys = [k for k, _ in backend.knn_search(vec, k=5, as_of=past)]
        assert keys == ["asof-knn-old"], f"as_of KNN lost superseded version: {keys}"


# ---------------------------------------------------------------------------
# RLS / project scoping is unchanged by the new predicate
# ---------------------------------------------------------------------------


class TestTenantScopingUnchanged:
    def test_other_project_live_rows_never_leak(self) -> None:
        """A different project's live rows must never appear in recall (RLS intact)."""
        proj_a = _make_backend("proj-a-" + uuid.uuid4().hex[:8], "agent-x")
        proj_b = _make_backend("proj-b-" + uuid.uuid4().hex[:8], "agent-x")
        try:
            # proj_b has a LIVE row that matches the query — but wrong project.
            proj_b.save(_make_entry("other-live", "grafana dashboard config"))
            # proj_a has its own live row.
            proj_a.save(_make_entry("mine-live", "grafana dashboard config"))

            results = proj_a.search("grafana dashboard")
            keys = {e.key for e in results}
            assert keys == {"mine-live"}, f"tenant leak or missing row: {keys}"
            assert "other-live" not in keys, "cross-project row leaked (RLS broken)"
        finally:
            proj_a.close()
            proj_b.close()

    def test_other_project_rows_absent_from_knn(self) -> None:
        proj_a = _make_backend("proj-a-" + uuid.uuid4().hex[:8], "agent-y")
        proj_b = _make_backend("proj-b-" + uuid.uuid4().hex[:8], "agent-y")
        try:
            vec = _unit_vector(11)
            proj_b.save(_make_entry("other-knn", "b row"))
            _set_embedding(proj_b, "other-knn", vec)
            proj_a.save(_make_entry("mine-knn", "a row"))
            _set_embedding(proj_a, "mine-knn", vec)

            keys = [k for k, _ in proj_a.knn_search(vec, k=5)]
            assert keys == ["mine-knn"], f"tenant leak in knn recall: {keys}"
        finally:
            proj_a.close()
            proj_b.close()
