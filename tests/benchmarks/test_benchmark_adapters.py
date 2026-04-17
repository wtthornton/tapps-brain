"""Smoke tests for LoCoMo + LongMemEval benchmark adapters (STORY-SC01 / TAP-557).

These tests do not touch external datasets or LLM APIs — they use
hand-authored fixtures and the Deterministic* answer/judge stand-ins from
``tapps_brain.benchmarks._common``. Shape contracts only: dataset loaders
parse expected fields, the runner produces a well-formed report, and
per-category aggregation is correct.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from tapps_brain.benchmarks import (
    BenchmarkItem,
    BenchmarkReport,
    DeterministicAnswerJudge,
    DeterministicAnswerModel,
    JudgeLabel,
    load_locomo,
    load_longmemeval,
    run_locomo,
    run_longmemeval,
)

# ---------------------------------------------------------------------------
# Shape fixtures — minimal JSON that exercises the loader paths
# ---------------------------------------------------------------------------


LOCOMO_FIXTURE: list[dict[str, object]] = [
    {
        "sample_id": "convo-001",
        "conversation": {
            "speaker_a": "Alice",
            "speaker_b": "Bob",
            "session_1_date_time": "2024-01-01",
            "session_1": [
                {"dia_id": "D1:1", "speaker": "Alice", "text": "I adopted a golden retriever."},
                {
                    "dia_id": "D1:2",
                    "speaker": "Bob",
                    "text": "What did you name the golden retriever?",
                },
                {"dia_id": "D1:3", "speaker": "Alice", "text": "I named the dog Biscuit."},
            ],
            "session_2_date_time": "2024-01-08",
            "session_2": [
                {"dia_id": "D2:1", "speaker": "Alice", "text": "Biscuit loves the park."},
            ],
        },
        "qa": [
            {"question": "What did Alice name her dog?", "answer": "Biscuit", "category": 1},
            {"question": "What breed is the dog?", "answer": "golden retriever", "category": 2},
            {"question": "Trick adversarial question.", "answer": "n/a", "category": 5},
        ],
    }
]


LONGMEMEVAL_FIXTURE: list[dict[str, object]] = [
    {
        "question_id": "q-001",
        "question_type": "single-session-user",
        "question": "What coffee order did the user mention?",
        "answer": "flat white",
        "haystack_session_ids": ["s1", "s2"],
        "haystack_dates": ["2024-03-01", "2024-03-02"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I always order a flat white in the morning."},
                {"role": "assistant", "content": "Noted — flat white is your morning coffee."},
            ],
            [
                {"role": "user", "content": "The weather was nice today."},
                {"role": "assistant", "content": "Glad to hear it."},
            ],
        ],
    },
    {
        "question_id": "q-002",
        "question_type": "multi-session",
        "question": "What is the user's favorite coffee?",
        # Cleaned-dataset answer-as-array case
        "answer": ["flat white"],
        "haystack_session_ids": ["s1"],
        "haystack_dates": ["2024-03-01"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I always order a flat white."},
            ]
        ],
    },
]


@pytest.fixture
def locomo_path(tmp_path: Path) -> Path:
    path = tmp_path / "locomo10.json"
    path.write_text(json.dumps(LOCOMO_FIXTURE), encoding="utf-8")
    return path


@pytest.fixture
def longmemeval_path(tmp_path: Path) -> Path:
    path = tmp_path / "longmemeval_s.json"
    path.write_text(json.dumps(LONGMEMEVAL_FIXTURE), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Loader shape tests
# ---------------------------------------------------------------------------


def test_load_locomo_excludes_adversarial_by_default(locomo_path: Path) -> None:
    items = load_locomo(locomo_path)
    # category=5 (adversarial) is the default exclusion
    assert len(items) == 2
    assert all(item.category != 5 for item in items)
    assert items[0].conversation_id == "convo-001"
    assert items[0].history, "history should be flattened across sessions"
    assert any("Biscuit" in value for _k, value, _t in items[0].history)


def test_load_locomo_include_adversarial_when_requested(locomo_path: Path) -> None:
    items = load_locomo(locomo_path, exclude_categories=[])
    assert len(items) == 3
    assert any(item.category == 5 for item in items)


def test_load_locomo_history_ordered_by_session(locomo_path: Path) -> None:
    items = load_locomo(locomo_path)
    history = items[0].history
    sessions = [key.split(":", 1)[0] for key, _v, _t in history]
    # session_1 turns must precede session_2 turns
    assert sessions.index("session_1") < sessions.index("session_2")


def test_load_longmemeval_normalises_array_answers(longmemeval_path: Path) -> None:
    items = load_longmemeval(longmemeval_path)
    assert len(items) == 2
    q002 = next(i for i in items if i.question_id == "q-002")
    assert q002.reference_answer == "flat white"
    assert q002.history  # non-empty haystack


# ---------------------------------------------------------------------------
# Runner shape tests
# ---------------------------------------------------------------------------


class _EchoContext:
    """Simple in-memory token-overlap retriever for tests."""

    def __init__(self, k: int = 3) -> None:
        self.k = k
        self._history: list[tuple[str, str]] = []

    def seed(self, item: BenchmarkItem) -> None:
        self._history = [(key, value) for key, value, _t in item.history]

    def retrieve(self, item: BenchmarkItem) -> Sequence[str]:
        q_tokens = {t.lower() for t in item.question.split() if len(t) > 2}
        scored: list[tuple[int, str]] = []
        for _key, value in self._history:
            v_tokens = {t.lower() for t in value.split() if len(t) > 2}
            scored.append((len(q_tokens & v_tokens), value))
        scored.sort(key=lambda row: row[0], reverse=True)
        return [value for score, value in scored[: self.k] if score > 0][: self.k]


def test_run_locomo_produces_expected_report_shape(locomo_path: Path) -> None:
    items = load_locomo(locomo_path)
    ctx = _EchoContext()
    report: BenchmarkReport = run_locomo(
        items,
        seed_history=ctx.seed,
        retrieve_context=ctx.retrieve,
        answer_model=DeterministicAnswerModel(),
        judge=DeterministicAnswerJudge(),
    )
    assert report.benchmark == "locomo"
    assert report.num_items == len(items)
    assert report.num_correct <= report.num_items
    assert 0.0 <= report.accuracy <= 1.0
    # Category keys are string names, not integers
    for cat in report.by_category:
        assert cat in {"single_hop", "multi_hop", "temporal", "open_domain"}
    assert report.metadata["dataset"] == "locomo10"
    assert report.metadata["excluded_categories"] == "5"


def test_run_longmemeval_produces_expected_report_shape(longmemeval_path: Path) -> None:
    items = load_longmemeval(longmemeval_path)
    ctx = _EchoContext()
    report: BenchmarkReport = run_longmemeval(
        items,
        seed_history=ctx.seed,
        retrieve_context=ctx.retrieve,
        answer_model=DeterministicAnswerModel(),
        judge=DeterministicAnswerJudge(),
    )
    assert report.benchmark == "longmemeval"
    assert report.num_items == 2
    # With deterministic stand-ins the high-overlap cases must land CORRECT
    correct_ids = {r.item_id for r in report.results if r.label is JudgeLabel.CORRECT}
    assert "q-001" in correct_ids, "flat-white answer should be judged CORRECT"
    assert report.metadata["dataset"] == "longmemeval"


def test_run_locomo_honors_limit(locomo_path: Path) -> None:
    items = load_locomo(locomo_path)
    ctx = _EchoContext()
    report = run_locomo(
        items,
        seed_history=ctx.seed,
        retrieve_context=ctx.retrieve,
        answer_model=DeterministicAnswerModel(),
        judge=DeterministicAnswerJudge(),
        limit=1,
    )
    assert report.num_items == 1


def test_deterministic_judge_is_lenient_on_containment() -> None:
    judge = DeterministicAnswerJudge()
    assert judge.judge("Q?", "Biscuit", "The dog is Biscuit.") is JudgeLabel.CORRECT
    assert judge.judge("Q?", "Biscuit", "A completely unrelated string.") is JudgeLabel.WRONG


def test_report_serialises_to_json(locomo_path: Path) -> None:
    items = load_locomo(locomo_path)
    ctx = _EchoContext()
    report = run_locomo(
        items,
        seed_history=ctx.seed,
        retrieve_context=ctx.retrieve,
        answer_model=DeterministicAnswerModel(),
        judge=DeterministicAnswerJudge(),
    )
    payload = json.loads(report.model_dump_json())
    assert payload["benchmark"] == "locomo"
    assert "results" in payload
    assert "by_category" in payload
