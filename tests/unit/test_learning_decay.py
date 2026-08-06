"""TAP-5547: decay and demotion of unvalidated / contradicted learnings.

Without this a corpus only grows: a candidate nobody validated stays a
candidate forever, and an approved learning that turned out to be wrong keeps
its approval. These tests pin the three demotion rules and, just as importantly,
what must NOT be demoted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tapps_brain.decay import (
    DEMOTE_REASON_CONTRADICTED,
    DEMOTE_REASON_DECAYED,
    DEMOTE_REASON_UNVALIDATED,
    DecayConfig,
    identify_learning_demotions,
)
from tapps_brain.models import LearningStatus, MemoryEntry, MemoryTier, PromotionSignal
from tapps_brain.profile import LearningDecayConfig

_NOW = datetime(2026, 8, 6, tzinfo=UTC)


def _iso(days_ago: float) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


def _entry(
    key: str,
    *,
    learning_status: LearningStatus = LearningStatus.candidate,
    confidence: float = 0.9,
    days_ago: float = 1,
    contradicted: bool = False,
    contradiction_reason: str | None = None,
    tier: MemoryTier = MemoryTier.architectural,
) -> MemoryEntry:
    approved = learning_status is LearningStatus.approved
    return MemoryEntry(
        key=key,
        value=f"value for {key}",
        tier=tier,
        confidence=confidence,
        created_at=_iso(days_ago),
        updated_at=_iso(days_ago),
        learning_status=learning_status,
        contradicted=contradicted,
        contradiction_reason=contradiction_reason,
        promoted_by="eval-run-1" if approved else None,
        promoted_at=_iso(days_ago) if approved else None,
        promotion_signal=PromotionSignal.eval if approved else None,
    )


def _identify(
    entries: list[MemoryEntry],
    *,
    config: LearningDecayConfig | None = None,
    decay_config: DecayConfig | None = None,
) -> list[object]:
    return identify_learning_demotions(
        entries,
        decay_config or DecayConfig(),
        config or LearningDecayConfig(),
        now=_NOW,
    )


class TestUnvalidatedCandidates:
    def test_candidate_past_the_staleness_window_is_demoted(self) -> None:
        """A candidate unvalidated for a quarter is abandoned, not pending."""
        result = _identify([_entry("k1", days_ago=120)])
        assert len(result) == 1
        assert result[0].reason_code == DEMOTE_REASON_UNVALIDATED  # type: ignore[attr-defined]

    def test_fresh_candidate_is_left_alone(self) -> None:
        assert _identify([_entry("k1", days_ago=3)]) == []

    def test_boundary_day_demotes(self) -> None:
        """Exactly at the threshold counts as stale — the window is inclusive."""
        result = _identify([_entry("k1", days_ago=90)])
        assert len(result) == 1

    def test_threshold_is_configurable(self) -> None:
        cfg = LearningDecayConfig(candidate_stale_days=10)
        assert len(_identify([_entry("k1", days_ago=30)], config=cfg)) == 1
        assert _identify([_entry("k1", days_ago=3)], config=cfg) == []


class TestDecayedCandidates:
    def test_candidate_below_the_confidence_floor_is_demoted(self) -> None:
        result = _identify([_entry("k1", confidence=0.05, days_ago=1)])
        assert len(result) == 1
        assert result[0].reason_code == DEMOTE_REASON_DECAYED  # type: ignore[attr-defined]

    def test_floor_is_configurable(self) -> None:
        cfg = LearningDecayConfig(candidate_confidence_floor=0.0)
        assert _identify([_entry("k1", confidence=0.05, days_ago=1)], config=cfg) == []

    def test_decay_reason_wins_over_staleness_when_both_apply(self) -> None:
        """One entry yields at most one demotion, so the reason must be stable."""
        result = _identify([_entry("k1", confidence=0.05, days_ago=200)])
        assert len(result) == 1
        assert result[0].reason_code == DEMOTE_REASON_DECAYED  # type: ignore[attr-defined]


class TestContradictedApproved:
    def test_contradicted_approved_entry_is_demoted(self) -> None:
        """An approval that survives its own contradiction is worse than no gate."""
        result = _identify(
            [
                _entry(
                    "k1",
                    learning_status=LearningStatus.approved,
                    contradicted=True,
                    contradiction_reason="superseded by TAP-1",
                )
            ]
        )
        assert len(result) == 1
        assert result[0].reason_code == DEMOTE_REASON_CONTRADICTED  # type: ignore[attr-defined]
        assert "superseded by TAP-1" in result[0].reason  # type: ignore[attr-defined]

    def test_uncontradicted_approved_entry_survives_age(self) -> None:
        """Staleness demotes candidates, not approvals — approval is a decision."""
        assert (
            _identify([_entry("k1", learning_status=LearningStatus.approved, days_ago=999)]) == []
        )

    def test_contradicted_demotion_can_be_disabled(self) -> None:
        cfg = LearningDecayConfig(demote_contradicted_approved=False)
        entries = [
            _entry("k1", learning_status=LearningStatus.approved, contradicted=True),
        ]
        assert _identify(entries, config=cfg) == []

    def test_contradiction_with_no_recorded_reason_still_demotes(self) -> None:
        result = _identify(
            [_entry("k1", learning_status=LearningStatus.approved, contradicted=True)]
        )
        assert len(result) == 1
        assert "no reason recorded" in result[0].reason  # type: ignore[attr-defined]


class TestWhatMustNotBeDemoted:
    def test_already_demoted_entries_are_skipped(self) -> None:
        """Re-demoting would overwrite the original, more specific reason."""
        entries = [_entry("k1", learning_status=LearningStatus.demoted, days_ago=999)]
        assert _identify(entries) == []

    def test_disabled_config_demotes_nothing(self) -> None:
        cfg = LearningDecayConfig(enabled=False)
        entries = [_entry("k1", days_ago=999), _entry("k2", confidence=0.01)]
        assert _identify(entries, config=cfg) == []

    def test_entry_with_no_learning_status_is_skipped(self) -> None:
        """A row whose promotion axis is unreadable is not silently rewritten."""

        class _Legacy:
            key = "legacy"
            learning_status = None

        assert (
            identify_learning_demotions(
                [_Legacy()],  # type: ignore[list-item]
                DecayConfig(),
                LearningDecayConfig(),
                now=_NOW,
            )
            == []
        )


class TestDeterminism:
    def test_same_input_yields_the_same_decisions(self) -> None:
        entries = [
            _entry("a", days_ago=200),
            _entry("b", confidence=0.02),
            _entry("c", days_ago=1),
        ]
        first = _identify(entries)
        second = _identify(entries)
        assert [d.key for d in first] == [d.key for d in second]  # type: ignore[attr-defined]

    def test_only_offending_entries_are_returned(self) -> None:
        entries = [_entry("stale", days_ago=200), _entry("fine", days_ago=1)]
        result = _identify(entries)
        assert [d.key for d in result] == ["stale"]  # type: ignore[attr-defined]


class TestStoreSweep:
    """The apply path: dry-run reports, live run writes, both agree."""

    def _seed(self, store: object) -> None:
        from datetime import timedelta

        stale_stamp = (datetime.now(tz=UTC) - timedelta(days=400)).isoformat()
        store.save(key="stale-candidate", value="an idea nobody validated")  # type: ignore[attr-defined]
        store.save(key="fresh-candidate", value="written just now")  # type: ignore[attr-defined]
        entry = store._entries["stale-candidate"]  # type: ignore[attr-defined]
        store._entries["stale-candidate"] = entry.model_copy(  # type: ignore[attr-defined]
            update={"updated_at": stale_stamp, "created_at": stale_stamp}
        )

    def test_dry_run_reports_without_writing(self, tmp_path: object) -> None:
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path)  # type: ignore[arg-type]
        try:
            self._seed(store)
            result = store.decay_learnings(dry_run=True)
            assert result["dry_run"] is True
            assert "stale-candidate" in result["demoted_keys"]
            assert result["demoted_count"] == 0, "dry run must not write"
            after = store.get("stale-candidate")
            assert after is not None
            assert after.learning_status is LearningStatus.candidate
        finally:
            store.close()

    def test_live_run_demotes_and_records_the_reason(self, tmp_path: object) -> None:
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path)  # type: ignore[arg-type]
        try:
            self._seed(store)
            result = store.decay_learnings()
            assert result["demoted_count"] >= 1
            demoted = store.get("stale-candidate")
            assert demoted is not None
            assert demoted.learning_status is LearningStatus.demoted
            assert demoted.demotion_reason, "a demotion with no reason cannot be audited"
            untouched = store.get("fresh-candidate")
            assert untouched is not None
            assert untouched.learning_status is LearningStatus.candidate
        finally:
            store.close()

    def test_sweep_is_idempotent(self, tmp_path: object) -> None:
        """A second sweep must not rewrite the first sweep's reason."""
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path)  # type: ignore[arg-type]
        try:
            self._seed(store)
            store.decay_learnings()
            first_reason = store.get("stale-candidate").demotion_reason  # type: ignore[union-attr]
            second = store.decay_learnings()
            assert second["demoted_count"] == 0
            assert store.get("stale-candidate").demotion_reason == first_reason  # type: ignore[union-attr]
        finally:
            store.close()


class TestProfileConfig:
    def test_defaults_are_documented_values(self) -> None:
        cfg = LearningDecayConfig()
        assert cfg.enabled is True
        assert cfg.candidate_stale_days == 90
        assert cfg.candidate_confidence_floor == 0.25
        assert cfg.demote_contradicted_approved is True

    def test_config_rejects_unknown_keys(self) -> None:
        """extra="forbid" keeps a typo'd YAML knob from silently doing nothing."""
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            LearningDecayConfig(candidate_stale_dayz=10)  # type: ignore[call-arg]

    def test_profile_exposes_learning_decay_with_defaults(self) -> None:
        """A profile that says nothing about learning decay still gets the sweep."""
        from tapps_brain.profile import LayerDefinition, MemoryProfile

        profile = MemoryProfile(
            name="decay-test",
            layers=[LayerDefinition(name="pattern", half_life_days=60)],
        )
        assert isinstance(profile.learning_decay, LearningDecayConfig)
        assert profile.learning_decay.candidate_stale_days == 90

    def test_profile_can_override_thresholds(self) -> None:
        from tapps_brain.profile import LayerDefinition, MemoryProfile

        profile = MemoryProfile(
            name="decay-test",
            layers=[LayerDefinition(name="pattern", half_life_days=60)],
            learning_decay=LearningDecayConfig(candidate_stale_days=30, enabled=False),
        )
        assert profile.learning_decay.candidate_stale_days == 30
        assert profile.learning_decay.enabled is False
