"""TAP-5542: gated learning — promotion status, provenance, and persistence shape.

The promotion axis is only trustworthy if every layer agrees on it: the model
defaults to ``candidate``, the upsert carries all five columns, and hydration
reads them back. These tests pin that agreement without a live Postgres.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from tapps_brain import _postgres_private_sql as _sql
from tapps_brain.errors import ConflictError, InvalidRequestError, NotFoundError
from tapps_brain.models import LearningStatus, MemoryEntry, PromotionSignal
from tapps_brain.postgres_private import PostgresPrivateBackend
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture()
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    s = MemoryStore(tmp_path)
    yield s
    s.close()


_PROMOTION_COLUMNS = (
    "learning_status",
    "promoted_by",
    "promoted_at",
    "promotion_signal",
    "demotion_reason",
)


# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------


class TestMemoryEntryDefaults:
    def test_new_entry_is_a_candidate(self) -> None:
        """Nothing is approved on arrival — approval is something you pass, not get."""
        entry = MemoryEntry(key="k", value="v")
        assert entry.learning_status is LearningStatus.candidate

    def test_new_entry_has_no_promotion_provenance(self) -> None:
        entry = MemoryEntry(key="k", value="v")
        assert entry.promoted_by is None
        assert entry.promoted_at is None
        assert entry.promotion_signal is None
        assert entry.demotion_reason is None

    def test_promotion_signal_accepts_only_eval_and_human(self) -> None:
        assert {s.value for s in PromotionSignal} == {"eval", "human"}

    def test_learning_status_values(self) -> None:
        assert {s.value for s in LearningStatus} == {"candidate", "approved", "demoted"}


# ---------------------------------------------------------------------------
# Persistence shape
# ---------------------------------------------------------------------------


class TestSaveUpsertShape:
    def test_upsert_inserts_every_promotion_column(self) -> None:
        for column in _PROMOTION_COLUMNS:
            assert column in _sql.SAVE_UPSERT_SQL, f"column not inserted: {column}"

    def test_upsert_updates_every_promotion_column(self) -> None:
        """Promotion state must survive a re-save, not silently keep the old row's."""
        for column in _PROMOTION_COLUMNS:
            assert f"{column} " in _sql.SAVE_UPSERT_SQL.split("DO UPDATE SET")[1], (
                f"column missing from DO UPDATE SET: {column}"
            )

    def test_placeholder_count_matches_param_count(self) -> None:
        """The guard that catches a column added to two of the three places.

        ``SAVE_UPSERT_SQL``'s column list, its VALUES placeholders, and
        ``build_save_params`` must move together; this fails loudly when they
        don't, instead of shipping a ``ProgrammingError`` at runtime.
        """
        values_clause = _sql.SAVE_UPSERT_SQL.split(") VALUES (")[1].split(")\nON CONFLICT")[0]
        placeholders = len(re.findall(r"%s", values_clause))
        params = _sql.build_save_params(
            entry=MemoryEntry(key="k", value="v"), project_id="p", agent_id="a"
        )
        assert placeholders == len(params)

    def test_params_carry_promotion_state(self) -> None:
        entry = MemoryEntry(
            key="k",
            value="v",
            learning_status=LearningStatus.approved,
            promoted_by="eval-run-42",
            promoted_at="2026-08-05T00:00:00+00:00",
            promotion_signal=PromotionSignal.eval,
        )
        params = _sql.build_save_params(entry=entry, project_id="p", agent_id="a")
        assert "approved" in params
        assert "eval-run-42" in params
        # Enums must serialise to their string value, not "PromotionSignal.eval".
        assert "eval" in params

    def test_entry_columns_select_the_promotion_columns(self) -> None:
        for column in _PROMOTION_COLUMNS:
            assert column in _sql.ENTRY_COLUMNS_SQL, f"column not selected: {column}"


# ---------------------------------------------------------------------------
# Hydration
# ---------------------------------------------------------------------------


class TestRowToEntry:
    @staticmethod
    def _row(**overrides: object) -> dict[str, object]:
        row: dict[str, object] = {"key": "k", "value": "v"}
        row.update(overrides)
        return row

    def test_missing_columns_hydrate_as_candidate(self) -> None:
        """A row read before migration 030 lands must not look approved."""
        entry = PostgresPrivateBackend._row_to_entry(self._row())
        assert entry.learning_status is LearningStatus.candidate

    def test_promotion_state_round_trips(self) -> None:
        entry = PostgresPrivateBackend._row_to_entry(
            self._row(
                learning_status="approved",
                promoted_by="alice",
                promoted_at="2026-08-05T00:00:00+00:00",
                promotion_signal="human",
                demotion_reason=None,
            )
        )
        assert entry.learning_status is LearningStatus.approved
        assert entry.promoted_by == "alice"
        assert entry.promotion_signal is PromotionSignal.human

    def test_unparseable_learning_status_falls_back_to_candidate(self) -> None:
        """Fail closed: an unreadable trust value is not evidence of approval."""
        entry = PostgresPrivateBackend._row_to_entry(self._row(learning_status="bogus"))
        assert entry.learning_status is LearningStatus.candidate

    def test_unparseable_promotion_signal_falls_back_to_none(self) -> None:
        entry = PostgresPrivateBackend._row_to_entry(self._row(promotion_signal="frequency"))
        assert entry.promotion_signal is None

    def test_demoted_row_keeps_its_reason(self) -> None:
        entry = PostgresPrivateBackend._row_to_entry(
            self._row(learning_status="demoted", demotion_reason="contradicted by TAP-1")
        )
        assert entry.learning_status is LearningStatus.demoted
        assert entry.demotion_reason == "contradicted by TAP-1"


# ---------------------------------------------------------------------------
# Promotion gate
# ---------------------------------------------------------------------------


class TestPromoteLearning:
    def test_saved_entry_starts_as_candidate(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        entry = store.get("k")
        assert entry is not None
        assert entry.learning_status is LearningStatus.candidate

    def test_promote_records_signal_and_actor(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        promoted = store.promote_learning("k", signal="eval", actor="eval-run-42")
        assert promoted.learning_status is LearningStatus.approved
        assert promoted.promotion_signal is PromotionSignal.eval
        assert promoted.promoted_by == "eval-run-42"
        assert promoted.promoted_at is not None

    def test_promotion_persists_to_the_store(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        store.promote_learning("k", signal="human", actor="alice")
        reloaded = store.get("k")
        assert reloaded is not None
        assert reloaded.learning_status is LearningStatus.approved

    def test_promote_rejects_a_frequency_signal(self, store: MemoryStore) -> None:
        """The whole point of the epic: only eval/human can approve."""
        store.save(key="k", value="v")
        with pytest.raises(InvalidRequestError):
            store.promote_learning("k", signal="frequency", actor="agent")

    def test_promote_requires_an_actor(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        with pytest.raises(InvalidRequestError):
            store.promote_learning("k", signal="eval", actor="   ")

    def test_promote_unknown_key_is_not_found(self, store: MemoryStore) -> None:
        with pytest.raises(NotFoundError):
            store.promote_learning("nope", signal="eval", actor="eval-run-1")

    def test_promote_already_approved_is_a_conflict(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        store.promote_learning("k", signal="eval", actor="eval-run-1")
        with pytest.raises(ConflictError):
            store.promote_learning("k", signal="eval", actor="eval-run-2")

    def test_promote_does_not_bump_updated_at(self, store: MemoryStore) -> None:
        """Approval is metadata; it must not inflate the recency ranking signal."""
        saved = store.save(key="k", value="v")
        assert isinstance(saved, MemoryEntry)
        promoted = store.promote_learning("k", signal="eval", actor="eval-run-1")
        assert promoted.updated_at == saved.updated_at

    def test_promoting_a_demoted_entry_clears_the_demotion_reason(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        store.promote_learning("k", signal="eval", actor="eval-run-1")
        store.demote_learning("k", reason="contradicted")
        repromoted = store.promote_learning("k", signal="human", actor="alice")
        assert repromoted.learning_status is LearningStatus.approved
        assert repromoted.demotion_reason is None


class TestDemoteLearning:
    def test_demote_records_the_reason(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        store.promote_learning("k", signal="eval", actor="eval-run-1")
        demoted = store.demote_learning("k", reason="contradicted by TAP-1")
        assert demoted.learning_status is LearningStatus.demoted
        assert demoted.demotion_reason == "contradicted by TAP-1"

    def test_demote_keeps_the_withdrawn_approval_provenance(self, store: MemoryStore) -> None:
        """An audit of a bad injection needs to know which approval was withdrawn."""
        store.save(key="k", value="v")
        store.promote_learning("k", signal="eval", actor="eval-run-1")
        demoted = store.demote_learning("k", reason="contradicted")
        assert demoted.promoted_by == "eval-run-1"
        assert demoted.promotion_signal is PromotionSignal.eval

    def test_demote_requires_a_reason(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        with pytest.raises(InvalidRequestError):
            store.demote_learning("k", reason="")

    def test_demote_unknown_key_is_not_found(self, store: MemoryStore) -> None:
        with pytest.raises(NotFoundError):
            store.demote_learning("nope", reason="whatever")


class TestFrequencyCannotApprove:
    """The load-bearing rule of TAP-5539."""

    def test_reinforce_does_not_promote_a_candidate(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        for _ in range(20):
            store.reinforce("k", confidence_boost=0.05)
        entry = store.get("k")
        assert entry is not None
        assert entry.learning_status is LearningStatus.candidate

    def test_reinforce_preserves_an_approval(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        store.promote_learning("k", signal="eval", actor="eval-run-1")
        store.reinforce("k", confidence_boost=0.05)
        entry = store.get("k")
        assert entry is not None
        assert entry.learning_status is LearningStatus.approved
        assert entry.promoted_by == "eval-run-1"

    def test_record_access_does_not_promote(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        for _ in range(20):
            store.get("k")
        entry = store.get("k")
        assert entry is not None
        assert entry.learning_status is LearningStatus.candidate


class TestApprovalIsBoundToContent:
    def test_rewriting_the_value_re_opens_the_gate(self, store: MemoryStore) -> None:
        """Otherwise anyone who can save could launder new content through an old approval."""
        store.save(key="k", value="original")
        store.promote_learning("k", signal="eval", actor="eval-run-1")
        store.save(key="k", value="something entirely different")
        entry = store.get("k")
        assert entry is not None
        assert entry.learning_status is LearningStatus.candidate
        assert entry.promoted_by is None

    def test_metadata_only_save_keeps_the_approval(self, store: MemoryStore) -> None:
        store.save(key="k", value="v")
        store.promote_learning("k", signal="eval", actor="eval-run-1")
        store.save(key="k", value="v", tags=["critical"])
        entry = store.get("k")
        assert entry is not None
        assert entry.learning_status is LearningStatus.approved
