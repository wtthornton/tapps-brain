"""LongMemEval benchmark adapter (arXiv:2410.10813).

Dataset: https://github.com/xiaowu0162/LongMemEval or the HF cleaned mirror
``xiaowu0162/longmemeval-cleaned``. Each JSON record has ``question``,
``answer``, ``question_type`` plus a ``haystack_sessions`` list of
chat-session turn arrays. We seed each session's turns as memories and let
the retriever pick what to surface.
"""

from __future__ import annotations

import json
from collections.abc import Iterable  # noqa: TC003 — used at runtime
from pathlib import Path

from pydantic import BaseModel, Field

from tapps_brain.benchmarks._common import (
    AnswerJudge,
    AnswerModel,
    BenchmarkItem,
    BenchmarkReport,
    run_benchmark,
)


class LongMemEvalItem(BaseModel):
    """One LongMemEval record with flattened haystack history."""

    question_id: str
    question_type: str
    question: str
    reference_answer: str
    history: list[tuple[str, str, str | None]] = Field(default_factory=list)

    def to_benchmark_item(self) -> BenchmarkItem:
        return BenchmarkItem(
            item_id=self.question_id,
            category=self.question_type,
            question=self.question,
            reference_answer=self.reference_answer,
            history=self.history,
        )


def _flatten_haystack(item: dict[str, object]) -> list[tuple[str, str, str | None]]:
    session_ids = item.get("haystack_session_ids")
    sessions = item.get("haystack_sessions")
    dates = item.get("haystack_dates")
    if not isinstance(sessions, list):
        return []
    ids_list = session_ids if isinstance(session_ids, list) else []
    dates_list = dates if isinstance(dates, list) else []
    history: list[tuple[str, str, str | None]] = []
    for sess_idx, session in enumerate(sessions):
        sess_id = str(ids_list[sess_idx]) if sess_idx < len(ids_list) else f"s{sess_idx}"
        sess_date = str(dates_list[sess_idx]) if sess_idx < len(dates_list) else None
        if not isinstance(session, list):
            continue
        for turn_idx, turn in enumerate(session):
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role", "unknown"))
            content = str(turn.get("content", "")).strip()
            if not content:
                continue
            history.append((f"{sess_id}:{turn_idx}", f"{role}: {content}", sess_date))
    return history


def load_longmemeval(path: str | Path) -> list[LongMemEvalItem]:
    """Load a LongMemEval JSON file (S / M / oracle split)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {path}, got {type(data).__name__}")
    items: list[LongMemEvalItem] = []
    for idx, raw in enumerate(data):
        if not isinstance(raw, dict):
            continue
        qid = str(raw.get("question_id", f"q{idx}"))
        qtype = str(raw.get("question_type", "uncategorised"))
        question = str(raw.get("question", "")).strip()
        answer_raw = raw.get("answer")
        answer = _normalise_answer(answer_raw)
        if not question or not answer:
            continue
        items.append(
            LongMemEvalItem(
                question_id=qid,
                question_type=qtype,
                question=question,
                reference_answer=answer,
                history=_flatten_haystack(raw),
            )
        )
    return items


def _normalise_answer(raw: object) -> str:
    """LongMemEval-cleaned answers are sometimes strings, sometimes arrays."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw if str(x).strip()]
        return " | ".join(parts)
    if raw is None:
        return ""
    return str(raw).strip()


def run_longmemeval(
    items: Iterable[LongMemEvalItem],
    *,
    seed_history: object,
    retrieve_context: object,
    answer_model: AnswerModel,
    judge: AnswerJudge,
    limit: int | None = None,
    seed: int = 42,
) -> BenchmarkReport:
    """Run LongMemEval. See ``_common.run_benchmark`` for callback shape."""
    bench_items = [item.to_benchmark_item() for item in items]
    report = run_benchmark(
        benchmark="longmemeval",
        items=bench_items,
        seed_history=seed_history,  # type: ignore[arg-type]
        retrieve_context=retrieve_context,  # type: ignore[arg-type]
        answer_model=answer_model,
        judge=judge,
        limit=limit,
        seed=seed,
    )
    report.metadata["dataset"] = "longmemeval"
    return report
