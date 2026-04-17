"""LoCoMo benchmark adapter (arXiv:2402.17753).

Dataset: https://github.com/snap-research/locomo — single file
``data/locomo10.json`` with 10 conversations. Each conversation has
multi-session dialogues (session_1_date_time / session_1 / ...) and a
``qa`` list keyed by category: 1=single-hop, 2=multi-hop, 3=temporal,
4=open-domain, 5=adversarial (typically excluded from headline accuracy).

This adapter is dataset-shape-only. Runners wire it to a memory store via
``SeedHistoryFn`` / ``RetrieveContextFn`` callbacks (see run_benchmark).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable  # noqa: TC003 — used as default-arg type at runtime
from pathlib import Path

from pydantic import BaseModel, Field

from tapps_brain.benchmarks._common import (
    AnswerJudge,
    AnswerModel,
    BenchmarkItem,
    BenchmarkReport,
    run_benchmark,
)

LOCOMO_CATEGORY_NAMES = {
    1: "single_hop",
    2: "multi_hop",
    3: "temporal",
    4: "open_domain",
    5: "adversarial",
}

DEFAULT_EXCLUDED_CATEGORIES = frozenset({5})

_SESSION_KEY_RE = re.compile(r"^session_(\d+)$")
_SESSION_DATE_KEY_RE = re.compile(r"^session_(\d+)_date_time$")


class LoCoMoItem(BaseModel):
    """One LoCoMo QA row with flattened multi-session history."""

    conversation_id: str
    qa_index: int
    category: int
    question: str
    reference_answer: str
    history: list[tuple[str, str, str | None]] = Field(default_factory=list)

    def to_benchmark_item(self) -> BenchmarkItem:
        return BenchmarkItem(
            item_id=f"{self.conversation_id}:q{self.qa_index}",
            category=LOCOMO_CATEGORY_NAMES.get(self.category, f"cat_{self.category}"),
            question=self.question,
            reference_answer=self.reference_answer,
            history=self.history,
        )


def _flatten_conversation(convo: dict[str, object]) -> list[tuple[str, str, str | None]]:
    """Flatten session_N / session_N_date_time keys into ordered turns.

    Returns (memory_key, memory_value, timestamp_iso) triples. Sessions are
    emitted in numeric order so that the memory store sees chronological
    history even when the JSON dict ordering is implementation-defined.
    """
    raw = convo.get("conversation")
    if not isinstance(raw, dict):
        return []
    session_dates: dict[int, str | None] = {}
    session_turns: dict[int, list[dict[str, object]]] = {}
    for key, value in raw.items():
        if match := _SESSION_DATE_KEY_RE.match(key):
            session_dates[int(match.group(1))] = str(value) if value else None
        elif (match := _SESSION_KEY_RE.match(key)) and isinstance(value, list):
            session_turns[int(match.group(1))] = [t for t in value if isinstance(t, dict)]
    history: list[tuple[str, str, str | None]] = []
    for sess_num in sorted(session_turns):
        date = session_dates.get(sess_num)
        for turn in session_turns[sess_num]:
            dia_id = str(turn.get("dia_id", f"s{sess_num}"))
            speaker = str(turn.get("speaker", "unknown"))
            text = str(turn.get("text", ""))
            if not text:
                continue
            history.append((f"session_{sess_num}:{dia_id}", f"{speaker}: {text}", date))
    return history


def load_locomo(
    path: str | Path,
    *,
    exclude_categories: Iterable[int] = DEFAULT_EXCLUDED_CATEGORIES,
) -> list[LoCoMoItem]:
    """Load ``locomo10.json`` from disk and yield one LoCoMoItem per QA pair.

    ``exclude_categories`` defaults to the adversarial set (5) which the
    field conventionally reports separately. Pass an empty iterable to keep
    everything.
    """
    excluded = set(exclude_categories)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {path}, got {type(data).__name__}")
    items: list[LoCoMoItem] = []
    for convo in data:
        if not isinstance(convo, dict):
            continue
        convo_id = str(convo.get("sample_id", f"convo_{len(items)}"))
        history = _flatten_conversation(convo)
        qa_list = convo.get("qa")
        if not isinstance(qa_list, list):
            continue
        for idx, qa in enumerate(qa_list):
            if not isinstance(qa, dict):
                continue
            category = int(qa.get("category", 0))
            if category in excluded:
                continue
            question = str(qa.get("question", "")).strip()
            answer = str(qa.get("answer", "")).strip()
            if not question or not answer:
                continue
            items.append(
                LoCoMoItem(
                    conversation_id=convo_id,
                    qa_index=idx,
                    category=category,
                    question=question,
                    reference_answer=answer,
                    history=history,
                )
            )
    return items


def run_locomo(
    items: Iterable[LoCoMoItem],
    *,
    seed_history: object,
    retrieve_context: object,
    answer_model: AnswerModel,
    judge: AnswerJudge,
    limit: int | None = None,
    seed: int = 42,
) -> BenchmarkReport:
    """Run the LoCoMo benchmark. See ``_common.run_benchmark`` for callback shape."""
    bench_items = [item.to_benchmark_item() for item in items]
    report = run_benchmark(
        benchmark="locomo",
        items=bench_items,
        seed_history=seed_history,  # type: ignore[arg-type]
        retrieve_context=retrieve_context,  # type: ignore[arg-type]
        answer_model=answer_model,
        judge=judge,
        limit=limit,
        seed=seed,
    )
    report.metadata["dataset"] = "locomo10"
    report.metadata["excluded_categories"] = ",".join(
        str(c) for c in sorted(DEFAULT_EXCLUDED_CATEGORIES)
    )
    return report
