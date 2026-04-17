#!/usr/bin/env python3
"""CLI reproducer for LoCoMo / LongMemEval benchmarks (STORY-SC01 / TAP-557).

Usage:

    # Smoke run with deterministic stand-ins — no API keys required
    python scripts/run_benchmark.py locomo \
        --dataset data/locomo10.json \
        --limit 5 \
        --answer-model deterministic \
        --judge deterministic \
        --output benchmarks/runs/locomo-smoke.json

    # Full run with LLM answer + judge (requires ANTHROPIC_API_KEY or OPENAI_API_KEY)
    python scripts/run_benchmark.py longmemeval \
        --dataset data/longmemeval_s.json \
        --answer-model anthropic \
        --judge anthropic \
        --output benchmarks/runs/longmemeval-s.json

The script is intentionally thin. Real runs plug an LLM-backed AnswerModel
and AnswerJudge into the library adapters in ``tapps_brain.benchmarks``.
Scoring, seeding, retrieval, and aggregation all live in the library.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence  # noqa: TC003 — runtime-used in method signatures
from pathlib import Path

_MIN_TOKEN_LEN = 3

# Ensure package import works when running from a checked-out repo
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from tapps_brain.benchmarks import (  # noqa: E402
    AnswerJudge,
    AnswerModel,
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


def _make_answer_model(name: str) -> AnswerModel:
    if name == "deterministic":
        return DeterministicAnswerModel()
    if name == "anthropic":
        return _AnthropicAnswerModel()
    if name == "openai":
        return _OpenAIAnswerModel()
    raise SystemExit(f"unknown --answer-model: {name}")


def _make_judge(name: str) -> AnswerJudge:
    if name == "deterministic":
        return DeterministicAnswerJudge()
    if name == "anthropic":
        return _AnthropicJudge()
    if name == "openai":
        return _OpenAIJudge()
    raise SystemExit(f"unknown --judge: {name}")


class _BrainContext:
    """Wraps a fresh MemoryStore per item to isolate histories.

    Uses a throwaway SQLite DSN is not possible under ADR-007 (Postgres-only).
    For the smoke path we build a pure-Python in-process retrieval instead;
    real runs plug AgentBrain via the --live-store flag.
    """

    def __init__(self, k: int = 5) -> None:
        self.k = k
        self._history: list[tuple[str, str]] = []

    def seed_history(self, item: BenchmarkItem) -> None:
        self._history = [(key, value) for key, value, _ in item.history]

    def retrieve_context(self, item: BenchmarkItem) -> Sequence[str]:
        q_tokens = {t.lower() for t in item.question.split() if len(t) >= _MIN_TOKEN_LEN}
        scored: list[tuple[int, str]] = []
        for _key, value in self._history:
            tokens = {t.lower() for t in value.split() if len(t) >= _MIN_TOKEN_LEN}
            scored.append((len(q_tokens & tokens), value))
        scored.sort(key=lambda row: row[0], reverse=True)
        return [value for score, value in scored[: self.k] if score > 0][: self.k]


def _live_brain_context(
    project_prefix: str,
    k: int,
) -> tuple[object, object]:  # pragma: no cover — requires live Postgres
    """Build AgentBrain-backed seed/retrieve callbacks (requires live Postgres).

    Separate path so smoke tests don't depend on a running database. Opens
    one AgentBrain per item (isolated project_id = prefix + item_id) so
    histories don't bleed.
    """
    from tapps_brain.agent_brain import AgentBrain

    current: dict[str, object] = {}

    def seed(item: BenchmarkItem) -> None:
        project_id = f"{project_prefix}-{item.item_id}"
        brain = AgentBrain(agent_id="eval", project_dir=project_id)
        for _key, value, _ts in item.history:
            brain.remember(value, tier="context")
        current["brain"] = brain
        current["item"] = item

    def retrieve(item: BenchmarkItem) -> Sequence[str]:
        brain = current.get("brain")
        if brain is None:
            return []
        rows = brain.recall(item.question, max_results=k)  # type: ignore[attr-defined]
        return [str(r.get("value", "")) for r in rows]

    return seed, retrieve


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run LoCoMo or LongMemEval benchmark.")
    p.add_argument("benchmark", choices=["locomo", "longmemeval"])
    p.add_argument("--dataset", required=True, help="Path to dataset JSON")
    p.add_argument("--limit", type=int, default=None, help="Truncate items for smoke runs")
    p.add_argument(
        "--answer-model",
        default="deterministic",
        choices=["deterministic", "anthropic", "openai"],
    )
    p.add_argument(
        "--judge",
        default="deterministic",
        choices=["deterministic", "anthropic", "openai"],
    )
    p.add_argument("--k", type=int, default=5, help="Retrieved context size")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--store",
        default="in-memory",
        choices=["in-memory", "live"],
        help="in-memory = pure-python token-overlap; live = AgentBrain/Postgres",
    )
    p.add_argument("--output", type=Path, default=None, help="Write report JSON here")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise SystemExit(f"dataset not found: {dataset_path}")

    if args.store == "in-memory":
        ctx = _BrainContext(k=args.k)
        seed, retrieve = ctx.seed_history, ctx.retrieve_context
    else:
        seed, retrieve = _live_brain_context(f"bench-{args.benchmark}", args.k)

    answer_model = _make_answer_model(args.answer_model)
    judge = _make_judge(args.judge)

    if args.benchmark == "locomo":
        items = load_locomo(dataset_path)
        report: BenchmarkReport = run_locomo(
            items,
            seed_history=seed,
            retrieve_context=retrieve,
            answer_model=answer_model,
            judge=judge,
            limit=args.limit,
            seed=args.seed,
        )
    else:
        items_l = load_longmemeval(dataset_path)
        report = run_longmemeval(
            items_l,
            seed_history=seed,
            retrieve_context=retrieve,
            answer_model=answer_model,
            judge=judge,
            limit=args.limit,
            seed=args.seed,
        )
    report.metadata["answer_model"] = args.answer_model
    report.metadata["judge"] = args.judge
    report.metadata["store"] = args.store
    report.metadata["k"] = str(args.k)
    report.metadata["seed"] = str(args.seed)

    print(
        f"{args.benchmark}: accuracy={report.accuracy:.4f} "
        f"({report.num_correct}/{report.num_items}) "
        f"wall={report.wall_time_seconds:.1f}s"
    )
    for cat, stats in sorted(report.by_category.items()):
        print(f"  {cat:20s}  n={stats['n']:4d}  acc={stats['accuracy']:.4f}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


# ---------------------------------------------------------------------------
# Optional LLM-backed AnswerModel / AnswerJudge implementations
# ---------------------------------------------------------------------------


class _AnthropicAnswerModel:  # pragma: no cover — requires ANTHROPIC_API_KEY
    def __init__(self, model: str = "claude-haiku-4-5-20251001") -> None:
        import anthropic  # type: ignore[import-not-found]

        self._client = anthropic.Anthropic()
        self._model = model

    def answer(self, question: str, context: Sequence[str]) -> str:
        from tapps_brain.benchmarks._common import ANSWER_PROMPT

        prompt = ANSWER_PROMPT.format(context="\n".join(context), question=question)
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        block = msg.content[0] if msg.content else None
        return getattr(block, "text", "UNKNOWN").strip()


class _AnthropicJudge:  # pragma: no cover — requires ANTHROPIC_API_KEY
    def __init__(self, model: str = "claude-haiku-4-5-20251001") -> None:
        import anthropic  # type: ignore[import-not-found]

        self._client = anthropic.Anthropic()
        self._model = model

    def judge(self, question: str, reference: str, candidate: str) -> JudgeLabel:
        from tapps_brain.benchmarks._common import JUDGE_PROMPT

        prompt = JUDGE_PROMPT.format(question=question, reference=reference, candidate=candidate)
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=40,
            messages=[{"role": "user", "content": prompt}],
        )
        block = msg.content[0] if msg.content else None
        text = getattr(block, "text", "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(text[start : end + 1])
            return (
                JudgeLabel.CORRECT
                if str(parsed.get("label", "")).upper() == "CORRECT"
                else JudgeLabel.WRONG
            )
        return JudgeLabel.WRONG


class _OpenAIAnswerModel:  # pragma: no cover — requires OPENAI_API_KEY
    def __init__(self, model: str = "gpt-4o-mini") -> None:
        import openai  # type: ignore[import-not-found]

        self._client = openai.OpenAI()
        self._model = model

    def answer(self, question: str, context: Sequence[str]) -> str:
        from tapps_brain.benchmarks._common import ANSWER_PROMPT

        prompt = ANSWER_PROMPT.format(context="\n".join(context), question=question)
        resp = self._client.chat.completions.create(
            model=self._model,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        return (resp.choices[0].message.content or "UNKNOWN").strip()


class _OpenAIJudge:  # pragma: no cover — requires OPENAI_API_KEY
    def __init__(self, model: str = "gpt-4o-mini") -> None:
        import openai  # type: ignore[import-not-found]

        self._client = openai.OpenAI()
        self._model = model

    def judge(self, question: str, reference: str, candidate: str) -> JudgeLabel:
        from tapps_brain.benchmarks._common import JUDGE_PROMPT

        prompt = JUDGE_PROMPT.format(question=question, reference=reference, candidate=candidate)
        resp = self._client.chat.completions.create(
            model=self._model,
            max_tokens=40,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        text = (resp.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return JudgeLabel.WRONG
        return (
            JudgeLabel.CORRECT
            if str(parsed.get("label", "")).upper() == "CORRECT"
            else JudgeLabel.WRONG
        )


if __name__ == "__main__":
    raise SystemExit(main())
