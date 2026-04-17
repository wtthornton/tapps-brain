"""Shared protocols, prompts, and aggregation for QA benchmarks.

Design notes:
- AnswerModel and AnswerJudge are Protocols, not concrete implementations.
  Tests use Deterministic* doubles; real runs plug in Anthropic/OpenAI.
- Reports are Pydantic models so they serialise to JSON for publication.
- The judge prompt is adapted from mem0's ``metrics/llm_judge.py``
  (CORRECT/WRONG binary). Category-aware prompts are a per-adapter concern.
"""

from __future__ import annotations

import random
import time
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


_MIN_TOKEN_LEN = 3  # Tokens shorter than this are dropped from overlap scoring


class JudgeLabel(StrEnum):
    """Binary correctness label — matches mem0's ACCURACY_PROMPT output."""

    CORRECT = "CORRECT"
    WRONG = "WRONG"


ANSWER_PROMPT = (
    "You are answering a question based only on the memory context below. "
    "Produce a concise answer in no more than 6 words. If the answer cannot "
    "be determined from the context, reply: UNKNOWN.\n\n"
    "Memory context:\n{context}\n\n"
    "Question: {question}\n"
    "Answer (≤6 words):"
)


JUDGE_PROMPT = (
    "Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. "
    "Do not be overly strict — if the candidate captures the same meaning as "
    "the reference answer, label it CORRECT even if the wording differs.\n\n"
    "Question: {question}\n"
    "Reference answer: {reference}\n"
    "Candidate answer: {candidate}\n"
    'Respond with JSON: {{"label": "CORRECT" | "WRONG"}}'
)


@runtime_checkable
class AnswerModel(Protocol):
    """LLM that produces a short answer given retrieved memory context."""

    def answer(self, question: str, context: Sequence[str]) -> str: ...


@runtime_checkable
class AnswerJudge(Protocol):
    """LLM-as-judge that grades a candidate answer against a reference."""

    def judge(self, question: str, reference: str, candidate: str) -> JudgeLabel: ...


class BenchmarkItem(BaseModel):
    """A single evaluable QA item.

    ``history`` is a list of (memory_key, memory_value, timestamp_iso) triples
    that the runner will load into the store before asking the question. The
    timestamp is a hint to adapters; stores that support ``created_at``
    overrides use it.
    """

    item_id: str
    category: str = "uncategorised"
    question: str
    reference_answer: str
    history: list[tuple[str, str, str | None]] = Field(default_factory=list)


class BenchmarkResult(BaseModel):
    """Per-item grading result."""

    item_id: str
    category: str
    question: str
    reference_answer: str
    candidate_answer: str
    label: JudgeLabel
    retrieved_context: list[str] = Field(default_factory=list)


class BenchmarkReport(BaseModel):
    """Aggregate report across all items."""

    benchmark: str
    num_items: int
    num_correct: int
    accuracy: float
    by_category: dict[str, dict[str, float | int]] = Field(default_factory=dict)
    wall_time_seconds: float = 0.0
    results: list[BenchmarkResult] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class DeterministicAnswerModel:
    """Offline AnswerModel that echoes the highest-overlap context line.

    Token-overlap scoring — reproducible, no dependencies, no API calls. Used
    by smoke tests and any environment without LLM credentials. Truncates to
    six words to mirror mem0's short-answer constraint.
    """

    def answer(self, question: str, context: Sequence[str]) -> str:
        if not context:
            return "UNKNOWN"
        q_tokens = {t.lower() for t in question.split() if len(t) >= _MIN_TOKEN_LEN}
        best_line = ""
        best_score = -1
        for line in context:
            line_tokens = {t.lower() for t in line.split() if len(t) >= _MIN_TOKEN_LEN}
            score = len(q_tokens & line_tokens)
            if score > best_score:
                best_score = score
                best_line = line
        return " ".join(best_line.split()[:6]) if best_line else "UNKNOWN"


class DeterministicAnswerJudge:
    """Offline AnswerJudge: lowercase token-set Jaccard over ≥0.5 → CORRECT.

    Deliberately lenient so that reference-present-in-candidate heuristics
    count as correct — matches mem0's "capture the same meaning" rubric.
    """

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold

    def judge(self, question: str, reference: str, candidate: str) -> JudgeLabel:
        ref_tokens = {t.lower().strip(".,!?:;") for t in reference.split()}
        cand_tokens = {t.lower().strip(".,!?:;") for t in candidate.split()}
        if not ref_tokens:
            return JudgeLabel.WRONG
        overlap = len(ref_tokens & cand_tokens)
        jaccard = overlap / max(1, len(ref_tokens | cand_tokens))
        containment = overlap / len(ref_tokens)
        score = max(jaccard, containment)
        return JudgeLabel.CORRECT if score >= self.threshold else JudgeLabel.WRONG


def run_benchmark(
    benchmark: str,
    items: Iterable[BenchmarkItem],
    *,
    seed_history: SeedHistoryFn,
    retrieve_context: RetrieveContextFn,
    answer_model: AnswerModel,
    judge: AnswerJudge,
    limit: int | None = None,
    seed: int = 42,
) -> BenchmarkReport:
    """Drive a benchmark end-to-end and aggregate per-category accuracy.

    ``seed_history`` and ``retrieve_context`` are adapter-provided callables
    so the runner stays store-agnostic: tests can pass a pure in-memory fake,
    real runs pass AgentBrain-backed wrappers. Seeded RNG is currently unused
    but reserved for future tie-breaking in retrieval.
    """
    random.Random(seed)
    t_start = time.perf_counter()
    results: list[BenchmarkResult] = []
    items_list = list(items)
    if limit is not None:
        items_list = items_list[:limit]

    for item in items_list:
        seed_history(item)
        context = retrieve_context(item)
        candidate = answer_model.answer(item.question, context)
        label = judge.judge(item.question, item.reference_answer, candidate)
        results.append(
            BenchmarkResult(
                item_id=item.item_id,
                category=item.category,
                question=item.question,
                reference_answer=item.reference_answer,
                candidate_answer=candidate,
                label=label,
                retrieved_context=list(context),
            )
        )

    num_correct = sum(1 for r in results if r.label is JudgeLabel.CORRECT)
    accuracy = num_correct / len(results) if results else 0.0
    by_category: dict[str, dict[str, float | int]] = {}
    for r in results:
        cat = by_category.setdefault(r.category, {"n": 0, "correct": 0})
        cat["n"] = int(cat["n"]) + 1
        if r.label is JudgeLabel.CORRECT:
            cat["correct"] = int(cat["correct"]) + 1
    for cat_stats in by_category.values():
        n = int(cat_stats["n"])
        correct = int(cat_stats["correct"])
        cat_stats["accuracy"] = round(correct / n, 6) if n else 0.0

    return BenchmarkReport(
        benchmark=benchmark,
        num_items=len(results),
        num_correct=num_correct,
        accuracy=round(accuracy, 6),
        by_category=by_category,
        wall_time_seconds=round(time.perf_counter() - t_start, 3),
        results=results,
    )


class SeedHistoryFn(Protocol):
    """Adapter callback that loads an item's history into a memory store."""

    def __call__(self, item: BenchmarkItem) -> None: ...


class RetrieveContextFn(Protocol):
    """Adapter callback that retrieves memory context for the question."""

    def __call__(self, item: BenchmarkItem) -> Sequence[str]: ...
