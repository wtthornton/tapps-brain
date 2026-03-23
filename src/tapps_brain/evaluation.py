"""Offline retrieval evaluation (EPIC-031 STORY-031.3–031.4).

BEIR-compatible JSONL/TSV datasets, pure-Python IR metrics, and optional
LLM-as-judge evaluation behind optional SDK dependencies.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog
import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FeatureNotAvailable(RuntimeError):
    """Raised when an optional evaluator dependency is not installed."""


# ---------------------------------------------------------------------------
# BEIR-compatible loaders
# ---------------------------------------------------------------------------


class EvalDoc(BaseModel):
    """Single corpus document (BEIR JSONL row)."""

    id: str = Field(alias="_id")
    title: str = ""
    text: str = ""

    model_config = {"populate_by_name": True}


class EvalCorpus(BaseModel):
    """In-memory corpus keyed by document id."""

    docs: dict[str, EvalDoc] = Field(default_factory=dict)

    @classmethod
    def load_jsonl(cls, path: Path) -> EvalCorpus:
        docs: dict[str, EvalDoc] = {}
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                d = EvalDoc.model_validate(row)
                docs[d.id] = d
        return cls(docs=docs)


class EvalQuery(BaseModel):
    """Single query (BEIR JSONL row)."""

    id: str = Field(alias="_id")
    text: str = ""

    model_config = {"populate_by_name": True}


class EvalQueries(BaseModel):
    """Queries keyed by query id."""

    queries: dict[str, EvalQuery] = Field(default_factory=dict)

    @classmethod
    def load_jsonl(cls, path: Path) -> EvalQueries:
        queries: dict[str, EvalQuery] = {}
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                q = EvalQuery.model_validate(row)
                queries[q.id] = q
        return cls(queries=queries)


class EvalQrels(BaseModel):
    """Query relevance judgments: query_id -> {doc_id -> grade 0..3}."""

    qrels: dict[str, dict[str, int]] = Field(default_factory=dict)

    @classmethod
    def load_tsv(cls, path: Path) -> EvalQrels:
        qrels: dict[str, dict[str, int]] = {}
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    parts = line.split()
                if len(parts) < 3:
                    continue
                qid, did, score_s = parts[0], parts[1], parts[2]
                grade = int(float(score_s))
                grade = max(0, min(3, grade))
                qrels.setdefault(qid, {})[did] = grade
        return cls(qrels=qrels)


class EvalSuite(BaseModel):
    """Corpus + queries + qrels with metadata."""

    name: str = "unnamed"
    corpus: EvalCorpus = Field(default_factory=EvalCorpus)
    queries: EvalQueries = Field(default_factory=EvalQueries)
    qrels: EvalQrels = Field(default_factory=EvalQrels)

    def model_dump_for_yaml(self) -> dict[str, Any]:
        """Serialize to a YAML-friendly dict (nested plain structures)."""
        return {
            "name": self.name,
            "corpus": {k: v.model_dump(by_alias=True) for k, v in self.corpus.docs.items()},
            "queries": {k: v.model_dump(by_alias=True) for k, v in self.queries.queries.items()},
            "qrels": dict(self.qrels.qrels),
        }

    @classmethod
    def from_yaml_dict(cls, data: dict[str, Any]) -> EvalSuite:
        name = str(data.get("name", "unnamed"))
        corpus_data = data.get("corpus") or {}
        docs = {k: EvalDoc.model_validate(v) for k, v in corpus_data.items()}
        queries_data = data.get("queries") or {}
        queries = {k: EvalQuery.model_validate(v) for k, v in queries_data.items()}
        qrels_raw = data.get("qrels") or {}
        qrels: dict[str, dict[str, int]] = {}
        for qid, m in qrels_raw.items():
            if isinstance(m, dict):
                qrels[str(qid)] = {str(d): int(g) for d, g in m.items()}
        return cls(name=name, corpus=EvalCorpus(docs=docs), queries=EvalQueries(queries=queries), qrels=EvalQrels(qrels=qrels))

    @classmethod
    def load_yaml(cls, path: Path) -> EvalSuite:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise ValueError("YAML suite root must be a mapping")
        return cls.from_yaml_dict(data)

    def save_yaml(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(self.model_dump_for_yaml(), fh, sort_keys=False, allow_unicode=True)

    @classmethod
    def load_beir_dir(cls, directory: Path, *, name: str | None = None) -> EvalSuite:
        """Load corpus.jsonl, queries.jsonl, qrels.tsv from a directory."""
        d = directory.resolve()
        corpus_path = d / "corpus.jsonl"
        queries_path = d / "queries.jsonl"
        qrels_path = d / "qrels.tsv"
        if not corpus_path.is_file() or not queries_path.is_file() or not qrels_path.is_file():
            raise FileNotFoundError(
                f"BEIR directory {d} must contain corpus.jsonl, queries.jsonl, qrels.tsv"
            )
        suite = cls(
            name=name or d.name,
            corpus=EvalCorpus.load_jsonl(corpus_path),
            queries=EvalQueries.load_jsonl(queries_path),
            qrels=EvalQrels.load_tsv(qrels_path),
        )
        return suite


# ---------------------------------------------------------------------------
# Metrics (pure Python)
# ---------------------------------------------------------------------------


def _is_relevant(grade: int) -> bool:
    return grade > 0


def precision_at_k(ranked_doc_ids: list[str], qrels: dict[str, int], k: int) -> float:
    """Fraction of top-k retrieved docs that are relevant (grade > 0)."""
    if k <= 0:
        return 0.0
    top = ranked_doc_ids[:k]
    if not top:
        return 0.0
    rel = sum(1 for d in top if _is_relevant(qrels.get(d, 0)))
    return rel / float(k)


def recall_at_k(ranked_doc_ids: list[str], qrels: dict[str, int], k: int) -> float:
    """Fraction of relevant judged docs that appear in top-k."""
    relevant = {d for d, g in qrels.items() if _is_relevant(g)}
    if not relevant:
        return 0.0
    top = set(ranked_doc_ids[:k])
    hit = len(relevant & top)
    return hit / float(len(relevant))


def reciprocal_rank(ranked_doc_ids: list[str], qrels: dict[str, int]) -> float:
    """Reciprocal rank of the first relevant document (0 if none)."""
    for i, d in enumerate(ranked_doc_ids):
        if _is_relevant(qrels.get(d, 0)):
            return 1.0 / float(i + 1)
    return 0.0


def dcg_at_k(ranked_doc_ids: list[str], qrels: dict[str, int], k: int) -> float:
    """DCG@k using graded relevance as gain, log2(rank+1) discount."""
    total = 0.0
    for i, d in enumerate(ranked_doc_ids[:k]):
        gain = float(qrels.get(d, 0))
        total += gain / math.log2(i + 2)
    return total


def ideal_dcg_at_k(qrels: dict[str, int], k: int) -> float:
    """IDCG@k from all judged gains for the query."""
    gains = sorted((float(g) for g in qrels.values()), reverse=True)[:k]
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(ranked_doc_ids: list[str], qrels: dict[str, int], k: int) -> float:
    """NDCG@k = DCG / IDCG."""
    idcg = ideal_dcg_at_k(qrels, k)
    if idcg <= 0.0:
        return 0.0
    return dcg_at_k(ranked_doc_ids, qrels, k) / idcg


# ---------------------------------------------------------------------------
# EvalReport
# ---------------------------------------------------------------------------


class EvalPerQueryMetrics(BaseModel):
    """Aggregate metrics for one query."""

    query_id: str
    precision_at_k: float
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
    ranked_doc_ids: list[str] = Field(default_factory=list)


class EvalThresholds(BaseModel):
    """Pass/fail gates for an evaluation run."""

    min_mrr: float = Field(default=0.5, ge=0.0, le=1.0)
    min_ndcg_at_k: float = Field(default=0.5, ge=0.0, le=1.0)
    k: int = Field(default=5, ge=1)


class EvalReport(BaseModel):
    """Full evaluation result."""

    suite_name: str
    timestamp: str
    k: int
    per_query: list[EvalPerQueryMetrics] = Field(default_factory=list)
    mean_precision_at_k: float = 0.0
    mean_recall_at_k: float = 0.0
    mrr: float = 0.0
    mean_ndcg_at_k: float = 0.0
    passed: bool = False
    thresholds: EvalThresholds = Field(default_factory=EvalThresholds)


def evaluate(
    store: MemoryStore,
    suite: EvalSuite,
    *,
    k: int = 5,
    thresholds: EvalThresholds | None = None,
) -> EvalReport:
    """Run each query through ranked retrieval and compute IR metrics."""
    from tapps_brain.retrieval import MemoryRetriever

    k_eff = max(1, min(k, 50))
    thr = (thresholds or EvalThresholds()).model_copy(update={"k": k_eff})
    retriever = MemoryRetriever()
    now = datetime.now(tz=UTC).isoformat()
    per_query: list[EvalPerQueryMetrics] = []
    mrr_acc: list[float] = []
    p_acc: list[float] = []
    r_acc: list[float] = []
    n_acc: list[float] = []

    for qid, q in sorted(suite.queries.queries.items(), key=lambda x: x[0]):
        qrels = suite.qrels.qrels.get(qid, {})
        if not qrels:
            logger.debug("evaluation.skip_query_no_qrels", query_id=qid)
            continue
        text = (q.text or "").strip()
        if not text:
            continue
        scored = retriever.search(text, store, limit=k_eff, min_confidence=0.0)
        ranked = [s.entry.key for s in scored]

        p = precision_at_k(ranked, qrels, k_eff)
        r = recall_at_k(ranked, qrels, k_eff)
        rr = reciprocal_rank(ranked, qrels)
        n = ndcg_at_k(ranked, qrels, k_eff)
        per_query.append(
            EvalPerQueryMetrics(
                query_id=qid,
                precision_at_k=round(p, 6),
                recall_at_k=round(r, 6),
                reciprocal_rank=round(rr, 6),
                ndcg_at_k=round(n, 6),
                ranked_doc_ids=list(ranked),
            )
        )
        mrr_acc.append(rr)
        p_acc.append(p)
        r_acc.append(r)
        n_acc.append(n)

    nq = len(per_query)
    mean_p = sum(p_acc) / nq if nq else 0.0
    mean_r = sum(r_acc) / nq if nq else 0.0
    mrr = sum(mrr_acc) / nq if nq else 0.0
    mean_n = sum(n_acc) / nq if nq else 0.0

    passed = mrr >= thr.min_mrr and mean_n >= thr.min_ndcg_at_k
    return EvalReport(
        suite_name=suite.name,
        timestamp=now,
        k=k_eff,
        per_query=per_query,
        mean_precision_at_k=round(mean_p, 6),
        mean_recall_at_k=round(mean_r, 6),
        mrr=round(mrr, 6),
        mean_ndcg_at_k=round(mean_n, 6),
        passed=passed,
        thresholds=thr,
    )


# ---------------------------------------------------------------------------
# LLM-as-judge (optional)
# ---------------------------------------------------------------------------


class JudgeResult(BaseModel):
    """Pointwise relevance judgment."""

    score: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    confident: bool = True


@runtime_checkable
class LLMJudge(Protocol):
    """Protocol for LLM relevance judges."""

    def judge_relevance(self, query: str, memory_value: str) -> JudgeResult: ...


def _parse_judge_json(text: str) -> JudgeResult:
    """Parse structured JSON from model output."""
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    score = float(data.get("score", 0))
    score = max(0.0, min(1.0, score))
    reasoning = str(data.get("reasoning", ""))
    confident = bool(data.get("confident", True))
    return JudgeResult(score=score, reasoning=reasoning, confident=confident)


class AnthropicJudge:
    """Claude-based binary relevance judge (optional ``anthropic`` SDK)."""

    def __init__(self, model: str = "claude-3-5-haiku-20241022") -> None:
        from tapps_brain._feature_flags import feature_flags

        if not feature_flags.anthropic_sdk:
            raise FeatureNotAvailable(
                "anthropic package is not installed; install optional extra or `pip install anthropic`"
            )
        import anthropic  # type: ignore[import-not-found]

        self._client = anthropic.Anthropic()  # noqa: TC002
        self._model = model

    def judge_relevance(self, query: str, memory_value: str) -> JudgeResult:
        prompt = (
            "You evaluate whether a memory snippet is relevant to a user query.\n"
            "Answer with JSON only, no markdown fences:\n"
            '{"reasoning": "<brief chain of thought>", "score": 0 or 1, '
            '"confident": true or false}\n'
            f"Query: {query}\nMemory: {memory_value}\n"
        )
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        block = msg.content[0]
        text = getattr(block, "text", str(block))
        return _parse_judge_json(text)


class OpenAIJudge:
    """OpenAI chat judge (optional ``openai`` SDK)."""

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        from tapps_brain._feature_flags import feature_flags

        if not feature_flags.openai_sdk:
            raise FeatureNotAvailable(
                "openai package is not installed; install optional extra or `pip install openai`"
            )
        from openai import OpenAI

        self._client = OpenAI()
        self._model = model

    def judge_relevance(self, query: str, memory_value: str) -> JudgeResult:
        prompt = (
            "You evaluate whether a memory snippet is relevant to a user query.\n"
            "Answer with JSON only:\n"
            '{"reasoning": "<brief>", "score": 0 or 1, "confident": true or false}\n'
            f"Query: {query}\nMemory: {memory_value}\n"
        )
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
        )
        text = resp.choices[0].message.content or ""
        return _parse_judge_json(text)


@dataclass
class CascadedJudge:
    """Try a cheap judge first; escalate when not confident."""

    cheap: LLMJudge
    expensive: LLMJudge
    escalations: int = field(default=0, init=False)

    def judge_relevance(self, query: str, memory_value: str) -> JudgeResult:
        first = self.cheap.judge_relevance(query, memory_value)
        if first.confident:
            return first
        self.escalations += 1
        return self.expensive.judge_relevance(query, memory_value)

    @property
    def escalation_rate(self) -> float:
        """Escalations per call (informal; reset escalations for a fresh rate)."""
        return float(self.escalations)


def evaluate_with_judge(
    store: MemoryStore,
    queries: list[tuple[str, str]],
    judge: LLMJudge,
    *,
    k: int = 5,
    thresholds: EvalThresholds | None = None,
) -> EvalReport:
    """Auto-build qrels via pointwise judging, then standard metrics.

    Each query item is ``(query_id, query_text)``. Retrieved memories are
    judged against the query; grade 1 if score >= 0.5 else 0.
    """
    from tapps_brain.retrieval import MemoryRetriever

    k_eff = max(1, min(k, 50))
    thr = (thresholds or EvalThresholds()).model_copy(update={"k": k_eff})
    retriever = MemoryRetriever()
    now = datetime.now(tz=UTC).isoformat()
    per_query: list[EvalPerQueryMetrics] = []
    mrr_acc: list[float] = []
    p_acc: list[float] = []
    r_acc: list[float] = []
    n_acc: list[float] = []

    for qid, qtext in queries:
        text = (qtext or "").strip()
        if not text:
            continue
        scored = retriever.search(text, store, limit=k_eff, min_confidence=0.0)
        qrels: dict[str, int] = {}
        ranked: list[str] = []
        for sm in scored:
            key = sm.entry.key
            ranked.append(key)
            jr = judge.judge_relevance(text, sm.entry.value)
            qrels[key] = 1 if jr.score >= 0.5 else 0

        if not ranked:
            continue

        p = precision_at_k(ranked, qrels, k_eff)
        r = recall_at_k(ranked, qrels, k_eff)
        rr = reciprocal_rank(ranked, qrels)
        n = ndcg_at_k(ranked, qrels, k_eff)
        per_query.append(
            EvalPerQueryMetrics(
                query_id=qid,
                precision_at_k=round(p, 6),
                recall_at_k=round(r, 6),
                reciprocal_rank=round(rr, 6),
                ndcg_at_k=round(n, 6),
                ranked_doc_ids=list(ranked),
            )
        )
        mrr_acc.append(rr)
        p_acc.append(p)
        r_acc.append(r)
        n_acc.append(n)

    nq = len(per_query)
    mean_p = sum(p_acc) / nq if nq else 0.0
    mean_r = sum(r_acc) / nq if nq else 0.0
    mrr = sum(mrr_acc) / nq if nq else 0.0
    mean_n = sum(n_acc) / nq if nq else 0.0
    passed = mrr >= thr.min_mrr and mean_n >= thr.min_ndcg_at_k
    return EvalReport(
        suite_name="llm_judge",
        timestamp=now,
        k=k_eff,
        per_query=per_query,
        mean_precision_at_k=round(mean_p, 6),
        mean_recall_at_k=round(mean_r, 6),
        mrr=round(mrr, 6),
        mean_ndcg_at_k=round(mean_n, 6),
        passed=passed,
        thresholds=thr,
    )
