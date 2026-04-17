"""End-to-end QA benchmark adapters (STORY-SC01 / TAP-557).

Unlike ``tapps_brain.evaluation`` (BEIR-shaped retrieval IR metrics), the
benchmarks here are answer-based: seed a memory store with a conversation
history, ask a question, produce an answer, grade the answer with an
LLM-as-judge. Covers LoCoMo (arXiv:2402.17753) and LongMemEval
(arXiv:2410.10813).
"""

from __future__ import annotations

from tapps_brain.benchmarks._common import (
    AnswerJudge,
    AnswerModel,
    BenchmarkItem,
    BenchmarkReport,
    BenchmarkResult,
    DeterministicAnswerJudge,
    DeterministicAnswerModel,
    JudgeLabel,
    run_benchmark,
)
from tapps_brain.benchmarks.locomo import (
    LoCoMoItem,
    load_locomo,
    run_locomo,
)
from tapps_brain.benchmarks.longmemeval import (
    LongMemEvalItem,
    load_longmemeval,
    run_longmemeval,
)

__all__ = [
    "AnswerJudge",
    "AnswerModel",
    "BenchmarkItem",
    "BenchmarkReport",
    "BenchmarkResult",
    "DeterministicAnswerJudge",
    "DeterministicAnswerModel",
    "JudgeLabel",
    "LoCoMoItem",
    "LongMemEvalItem",
    "load_locomo",
    "load_longmemeval",
    "run_benchmark",
    "run_locomo",
    "run_longmemeval",
]
