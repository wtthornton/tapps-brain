"""TAP-4587: memory scorecard — bucket counts and temporal drop-rate sample.

Covers ``MemoryStore.memory_scorecard()`` (Box 1) and
``MemoryStore.memory_temporal_drop_rate_sample()`` (Box 2), plus the two
controls the lane prompt requires (VAL-13):

* POSITIVE control — a row closed via ``reason="supersession"`` and a row
  closed via ``reason="age"`` must land in mutually-exclusive lifecycle-status
  buckets (``superseded`` vs ``stale``), never both in ``superseded``. This is
  the control that would catch a scorecard built on ``MemoryEntry.is_superseded``
  instead of the lifecycle ``status`` field — that property returns ``True``
  for *both* rows, and a scorecard bucketing on it would silently collapse the
  two buckets into one (the trap the lane prompt calls out explicitly).
* NEGATIVE control — RLS scoping: run against a real Postgres role RLS is
  enforced against (``@pytest.mark.requires_postgres``, skipped without a live
  ``TAPPS_BRAIN_DATABASE_URL``, matching the existing repo convention), with
  two distinct ``project_id`` values, and confirm each store's scorecard only
  ever sees its own rows — including after the *other* project writes more
  data, which a merely-application-level filter (not real RLS) could get
  wrong.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest

from tapps_brain.models import MemoryStatus
from tapps_brain.store import MemoryStore, _scorecard_base_key


@pytest.fixture
def store() -> Any:
    """A MemoryStore on the autouse in-memory backend (tests/conftest.py)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        s = MemoryStore(project_root=Path(tmpdir), embedding_provider=None)
        try:
            yield s
        finally:
            s.close()


# ---------------------------------------------------------------------------
# _scorecard_base_key
# ---------------------------------------------------------------------------


class TestScorecardBaseKey:
    def test_strips_trailing_version_suffix(self) -> None:
        assert _scorecard_base_key("foo.v1") == "foo"
        assert _scorecard_base_key("foo.v23") == "foo"

    def test_key_without_suffix_is_unchanged(self) -> None:
        assert _scorecard_base_key("foo") == "foo"

    def test_only_strips_trailing_suffix(self) -> None:
        # A key that legitimately contains ".v" mid-string, not as a trailing
        # version suffix, must not be mangled.
        assert _scorecard_base_key("foo.version-notes") == "foo.version-notes"


# ---------------------------------------------------------------------------
# Box 1 — memory_scorecard() bucket counts (in-memory backend, fast)
# ---------------------------------------------------------------------------


class TestMemoryScorecardBuckets:
    def test_empty_store_reports_all_zero(self, store: MemoryStore) -> None:
        card = store.memory_scorecard()
        assert card.total == 0
        assert card.live == 0
        assert card.expired == 0
        assert card.superseded == 0
        assert card.stale == 0
        assert card.duplicate_key_clusters == 0

    def test_live_entry_counted_as_live_not_expired(self, store: MemoryStore) -> None:
        store.save(key="k1", value="a live fact", tier="pattern")

        card = store.memory_scorecard()

        assert card.total == 1
        assert card.live == 1
        assert card.expired == 0
        assert card.superseded == 0
        assert card.stale == 0

    def test_supersession_control_lands_only_in_superseded_bucket(self, store: MemoryStore) -> None:
        """POSITIVE control, half 1 of 2: a genuinely-superseded row.

        Seeded via the real ``store.supersede()`` write path (not a
        hand-written ``status`` value), matching the item-7 guidance in the
        lane prompt: seed against the write path that actually populates the
        column, not a guess at its shape.
        """
        store.save(key="super-src", value="the old value", tier="pattern")
        store.supersede("super-src", "the new value")

        card = store.memory_scorecard()

        assert card.superseded == 1
        assert card.stale == 0
        assert card.total == 2  # old row + its successor

    def test_age_close_control_lands_only_in_stale_bucket(self, store: MemoryStore) -> None:
        """POSITIVE control, half 2 of 2: a genuinely age-closed row.

        Seeded via ``store.close_validity(reason="age", ...)`` — the same
        helper the scheduled decay refresh uses — never a hand-written status.
        """
        store.save(key="age-src", value="a fact that decayed", tier="context")
        store.close_validity("age-src", reason="age", detail="unit test")

        card = store.memory_scorecard()

        assert card.stale == 1
        assert card.superseded == 0

    def test_supersession_and_age_buckets_do_not_collapse(self, store: MemoryStore) -> None:
        """The control the lane prompt names explicitly: run both seeds
        together and confirm the ``superseded`` bucket does not silently
        absorb the ``stale`` row (the ``MemoryEntry.is_superseded`` trap —
        that property is ``True`` for *both* rows below, so a scorecard that
        bucketed on it would report ``superseded=2, stale=0``).
        """
        store.save(key="super-src", value="the old value", tier="pattern")
        store.supersede("super-src", "the new value")
        store.save(key="age-src", value="a fact that decayed", tier="context")
        store.close_validity("age-src", reason="age", detail="unit test")

        # Confirm the trap is real before trusting the control: is_superseded
        # is True for both rows, even though only one has status=superseded.
        entries = {e.key: e for e in store._persistence.load_all()}
        assert entries["super-src"].is_superseded is True
        assert entries["age-src"].is_superseded is True
        assert entries["super-src"].status == MemoryStatus.superseded
        assert entries["age-src"].status == MemoryStatus.stale

        card = store.memory_scorecard()

        assert card.superseded == 1
        assert card.stale == 1

    def test_contradicted_and_archived_buckets(self, store: MemoryStore) -> None:
        store.save(key="c1", value="a refuted fact", tier="pattern")
        store.close_validity("c1", reason="contradiction", detail="unit test")

        card = store.memory_scorecard()

        assert card.contradicted == 1
        assert card.superseded == 0
        assert card.stale == 0

    def test_expired_axis_is_independent_of_status(self, store: MemoryStore) -> None:
        """``expired`` is a temporal-validity count, not a status bucket —
        the superseded row above is *also* expired (closing validity always
        stamps ``invalid_at``), so ``expired`` legitimately overlaps
        ``superseded``/``stale`` by design (see ``MemoryScorecard`` docstring).
        """
        store.save(key="super-src", value="the old value", tier="pattern")
        store.supersede("super-src", "the new value")

        card = store.memory_scorecard()

        # super-src is both superseded (status bucket) and expired (temporal
        # axis); its successor is live and not expired.
        assert card.superseded == 1
        assert card.expired == 1
        assert card.live == 1

    def test_duplicate_key_cluster_counts_base_key_and_successor(self, store: MemoryStore) -> None:
        store.save(key="dup", value="v0", tier="pattern")
        store.supersede("dup", "v1")

        card = store.memory_scorecard()

        assert card.duplicate_key_clusters == 1
        assert card.duplicate_key_rows == 2

    def test_unrelated_keys_are_not_a_duplicate_cluster(self, store: MemoryStore) -> None:
        store.save(key="a", value="x", tier="pattern")
        store.save(key="b", value="y", tier="pattern")

        card = store.memory_scorecard()

        assert card.duplicate_key_clusters == 0
        assert card.duplicate_key_rows == 0

    def test_project_id_is_labelled_on_the_report(self, store: MemoryStore) -> None:
        card = store.memory_scorecard()
        assert card.project_id == store._project_id

    def test_rls_scoped_false_on_in_memory_backend(self) -> None:
        """The in-memory unit-test backend has no ``_scoped_conn`` / RLS path,
        so the report must say so rather than claiming a scoping guarantee it
        cannot provide.

        Constructs :class:`InMemoryPrivateBackend` explicitly rather than
        relying on the autouse ``_inject_in_memory_private_backend`` fixture
        (tests/conftest.py:450-478), which only injects it when
        ``TAPPS_BRAIN_DATABASE_URL`` is unset. CI's unit job sets that DSN, so
        a test that got its backend from the fixture was really asserting on
        the *absence of an env var*, not on the in-memory backend it names —
        it passed locally and failed in CI (TAP-4587)."""
        from tests.conftest import InMemoryPrivateBackend

        with tempfile.TemporaryDirectory() as tmpdir:
            s = MemoryStore(
                project_root=Path(tmpdir),
                embedding_provider=None,
                private_backend=InMemoryPrivateBackend(),
            )
            try:
                card = s.memory_scorecard()
                assert card.rls_scoped is False
            finally:
                s.close()


# ---------------------------------------------------------------------------
# Box 2 — memory_temporal_drop_rate_sample()
# ---------------------------------------------------------------------------


class TestTemporalDropRateSample:
    def test_all_live_sample_has_zero_drop_rate(self, store: MemoryStore) -> None:
        store.save(key="k1", value="a live fact", tier="pattern")
        store.save(key="k2", value="another live fact", tier="pattern")

        sample = store.memory_temporal_drop_rate_sample(sample_size=10)

        assert sample.included_count == 2
        assert sample.excluded_count == 0
        assert sample.drop_rate == 0.0

    def test_expired_rows_in_sample_raise_drop_rate(self, store: MemoryStore) -> None:
        store.save(key="k1", value="a live fact", tier="pattern")
        store.save(key="age-src", value="a fact that decayed", tier="context")
        store.close_validity("age-src", reason="age", detail="unit test")

        sample = store.memory_temporal_drop_rate_sample(sample_size=10)

        assert sample.included_count == 1
        assert sample.excluded_count == 1
        assert sample.drop_rate == pytest.approx(0.5)

    def test_empty_store_reports_zero_drop_rate_not_a_crash(self, store: MemoryStore) -> None:
        sample = store.memory_temporal_drop_rate_sample(sample_size=10)
        assert sample.included_count == 0
        assert sample.drop_rate == 0.0

    def test_sample_size_caps_how_many_entries_are_considered(self, store: MemoryStore) -> None:
        for i in range(5):
            store.save(key=f"k{i}", value=f"fact {i}", tier="pattern")

        sample = store.memory_temporal_drop_rate_sample(sample_size=2)

        assert sample.included_count == 2


# ---------------------------------------------------------------------------
# Box 3 — NEGATIVE CONTROL: RLS scoping against a real Postgres role
# ---------------------------------------------------------------------------


@pytest.mark.requires_postgres
def test_scorecard_is_rls_scoped_across_two_projects() -> None:
    """Two ``MemoryStore``s on distinct ``project_id``s, same disposable
    Postgres. Each store's scorecard must only ever count its own rows —
    proven by re-reading project A's scorecard *after* project B writes more
    data, which only a real RLS row filter (not an accidental
    already-consistent application-level filter) guarantees stays unchanged.

    Skipped automatically unless ``TAPPS_BRAIN_DATABASE_URL`` points at a live
    Postgres (repo convention, see ``tests/conftest.py``). Never run against
    the live brain database — point this at a disposable instance only.
    """
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_private import PostgresPrivateBackend

    dsn = os.environ["TAPPS_BRAIN_DATABASE_URL"]
    suffix = uuid.uuid4().hex[:8]
    project_a = f"test-scorecard-rls-a-{suffix}"
    project_b = f"test-scorecard-rls-b-{suffix}"

    cm_a = PostgresConnectionManager(dsn)
    backend_a = PostgresPrivateBackend(cm_a, project_id=project_a, agent_id="test-agent")
    cm_b = PostgresConnectionManager(dsn)
    backend_b = PostgresPrivateBackend(cm_b, project_id=project_b, agent_id="test-agent")

    store_a = MemoryStore(
        Path(tempfile.mkdtemp()), private_backend=backend_a, embedding_provider=None
    )
    store_b = MemoryStore(
        Path(tempfile.mkdtemp()), private_backend=backend_b, embedding_provider=None
    )
    try:
        store_a.save(key="a-1", value="project A fact one", tier="pattern")
        store_a.save(key="a-2", value="project A fact two", tier="pattern")

        card_a_before = store_a.memory_scorecard()
        assert card_a_before.project_id == project_a
        assert card_a_before.rls_scoped is True
        assert card_a_before.total == 2

        store_b.save(key="b-1", value="project B's private fact", tier="pattern")

        card_b = store_b.memory_scorecard()
        assert card_b.project_id == project_b
        assert card_b.total == 1

        # The proof: project A's count is unchanged after B's write. A false
        # zero or a count that silently absorbed B's row would both be a
        # scoping failure.
        card_a_after = store_a.memory_scorecard()
        assert card_a_after.total == 2
        assert card_a_after.live == 2
    finally:
        store_a.close()
        store_b.close()
