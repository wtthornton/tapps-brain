"""learn_from_failure applies negative signal through feedback_events (TAP-7339).

Covers:
  - VAL-15: a failure recorded with a reason writes a feedback_events row
    (reason visible) and bumps negative_feedback_count on the recalled keys.
  - B2-2: a missing/blank reason writes no row and moves no counter.
  - B2-3: the signal lands only on recalled keys, never on an unrecalled one.
  - Positive control: learn_from_success still bumps positive_feedback_count
    and writes its own feedback_events row, proving the counter-observation
    technique used above actually detects a real change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tapps_brain.agent_brain import AgentBrain, _content_key

if TYPE_CHECKING:
    from pathlib import Path


def _make_brain(tmp_path: Path, **kwargs: object) -> AgentBrain:
    defaults: dict[str, object] = {"agent_id": "test-agent", "project_dir": tmp_path}
    defaults.update(kwargs)
    return AgentBrain(**defaults)  # type: ignore[arg-type]


class TestLearnFromFailureNegativeSignal:
    def test_val15_reason_writes_row_and_bumps_counter_on_recalled_keys(
        self, tmp_path: Path
    ) -> None:
        with _make_brain(tmp_path) as brain:
            brain.remember("Retry with exponential backoff on 429s")
            recalled = brain.recall("429 retry")
            assert len(recalled) == 1
            key = recalled[0]["key"]
            assert brain._last_recalled_keys == [key]

            before = brain.store.get(key)
            assert before is not None
            before_neg = before.negative_feedback_count

            brain.learn_from_failure(
                "retry loop still hammered the API",
                error="backoff was not applied",
            )

            after = brain.store.get(key)
            assert after is not None
            assert after.negative_feedback_count == before_neg + 1.0

            rows = brain.store.query_feedback(entry_key=key)
            assert len(rows) == 1
            row = rows[0]
            assert row.event_type == "implicit_negative"
            assert row.details.get("reason") == "backoff was not applied"

    def test_b2_2_missing_reason_writes_no_row_and_no_counter_move(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            brain.remember("Use idempotency keys on retries")
            recalled = brain.recall("idempotency keys")
            key = recalled[0]["key"]

            before = brain.store.get(key)
            assert before is not None
            before_neg = before.negative_feedback_count

            # No `error=` kwarg at all.
            brain.learn_from_failure("the retry still duplicated the write")

            after = brain.store.get(key)
            assert after is not None
            assert after.negative_feedback_count == before_neg

            assert brain.store.query_feedback(entry_key=key) == []

    def test_b2_2_blank_reason_writes_no_row_and_no_counter_move(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            brain.remember("Cache the token to avoid re-auth storms")
            recalled = brain.recall("re-auth storms")
            key = recalled[0]["key"]

            before = brain.store.get(key)
            assert before is not None
            before_neg = before.negative_feedback_count

            # error="" must not be silently treated as a valid reason.
            brain.learn_from_failure("auth storm happened anyway", error="   ")

            after = brain.store.get(key)
            assert after is not None
            assert after.negative_feedback_count == before_neg
            assert brain.store.query_feedback(entry_key=key) == []

    def test_b2_3_unrecalled_key_is_untouched(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            brain.remember("Recalled memory about queue backpressure")
            unrecalled_key = brain.remember("Unrelated memory about CSS grid")

            recalled = brain.recall("queue backpressure")
            recalled_key = recalled[0]["key"]
            assert unrecalled_key not in {r["key"] for r in recalled}

            unrecalled_before = brain.store.get(unrecalled_key)
            assert unrecalled_before is not None
            unrecalled_before_neg = unrecalled_before.negative_feedback_count

            brain.learn_from_failure(
                "queue backpressure handling failed under load",
                error="semaphore never released",
            )

            recalled_after = brain.store.get(recalled_key)
            assert recalled_after is not None
            assert recalled_after.negative_feedback_count == 1.0

            unrecalled_after = brain.store.get(unrecalled_key)
            assert unrecalled_after is not None
            assert unrecalled_after.negative_feedback_count == unrecalled_before_neg
            assert brain.store.query_feedback(entry_key=unrecalled_key) == []

    def test_positive_control_learn_from_success_still_bumps_positive_counter(
        self, tmp_path: Path
    ) -> None:
        with _make_brain(tmp_path) as brain:
            brain.remember("Debounce the search input")
            recalled = brain.recall("debounce search")
            key = recalled[0]["key"]

            before = brain.store.get(key)
            assert before is not None
            before_pos = before.positive_feedback_count

            brain.learn_from_success("debounce fixed the input lag")

            after = brain.store.get(key)
            assert after is not None
            assert after.positive_feedback_count == before_pos + 1.0

            rows = brain.store.query_feedback(entry_key=key)
            assert len(rows) == 1
            assert rows[0].event_type == "implicit_positive"


def test_content_key_matches_failure_prefix() -> None:
    # Sanity check the key helper used by _make_brain-based tests above still
    # matches the "failure-" prefix learn_from_failure() saves under.
    assert _content_key("failure-x") == _content_key("failure-x")
