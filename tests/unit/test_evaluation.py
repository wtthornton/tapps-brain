"""Unit tests for offline evaluation (EPIC-031)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tapps_brain.evaluation import (
    AnthropicJudge,
    EvalCorpus,
    EvalDoc,
    EvalQrels,
    EvalQueries,
    EvalQuery,
    EvalSuite,
    EvalThresholds,
    JudgeResult,
    OpenAIJudge,
    dcg_at_k,
    evaluate,
    evaluate_with_judge,
    ideal_dcg_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from tapps_brain.store import MemoryStore


def test_precision_recall_mrr_ndcg_known() -> None:
    qrels = {"d1": 2, "d2": 0, "d3": 1}
    ranked = ["d2", "d3", "d1"]
    assert precision_at_k(ranked, qrels, 2) == 0.5
    assert recall_at_k(ranked, qrels, 3) == 1.0
    assert reciprocal_rank(ranked, qrels) == 1.0 / 2.0
    k = 3
    dcg = dcg_at_k(ranked, qrels, k)
    idcg = ideal_dcg_at_k(qrels, k)
    assert idcg > 0
    assert abs(ndcg_at_k(ranked, qrels, k) - dcg / idcg) < 1e-9


def test_eval_loaders_blank_lines_and_qrels_variants(tmp_path: Path) -> None:
    c = tmp_path / "corpus.jsonl"
    c.write_text('\n\n{"_id":"a","text":"x"}\n', encoding="utf-8")
    q = tmp_path / "queries.jsonl"
    q.write_text('\n{"_id":"q1","text":"hi"}\n', encoding="utf-8")
    t = tmp_path / "qrels.tsv"
    t.write_text("# c\nq1\ta\t2\nshort\nq1 b 1\n", encoding="utf-8")
    corpus = EvalCorpus.load_jsonl(c)
    queries = EvalQueries.load_jsonl(q)
    qrels = EvalQrels.load_tsv(t)
    assert "a" in corpus.docs
    assert "q1" in queries.queries
    assert qrels.qrels["q1"]["b"] == 1


def test_load_beir_dir_missing() -> None:
    with pytest.raises(FileNotFoundError):
        EvalSuite.load_beir_dir(Path("/nonexistent/beir-suite-xyz"))


def test_eval_loaders(tmp_path: Path) -> None:
    cdir = tmp_path / "beir"
    cdir.mkdir()
    (cdir / "corpus.jsonl").write_text(
        '{"_id":"a","title":"t","text":"hello world"}\n', encoding="utf-8"
    )
    (cdir / "queries.jsonl").write_text('{"_id":"q1","text":"hello"}\n', encoding="utf-8")
    (cdir / "qrels.tsv").write_text("q1\ta\t2\n", encoding="utf-8")
    suite = EvalSuite.load_beir_dir(cdir, name="t")
    assert suite.name == "t"
    assert "a" in suite.corpus.docs
    assert suite.qrels.qrels["q1"]["a"] == 2


def test_eval_suite_yaml_roundtrip(tmp_path: Path) -> None:
    suite = EvalSuite(
        name="x",
        corpus=EvalCorpus(docs={"k": EvalDoc(id="k", text="z")}),
        queries=EvalQueries(queries={"q": EvalQuery(id="q", text="z")}),
        qrels=EvalQrels(qrels={"q": {"k": 1}}),
    )
    p = tmp_path / "s.yaml"
    suite.save_yaml(p)
    s2 = EvalSuite.load_yaml(p)
    assert s2.name == "x"
    assert "k" in s2.corpus.docs


def test_evaluate_on_store(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    store = MemoryStore(root)
    try:
        store.save(
            "doc-auth",
            "JWT tokens for API authentication",
            tier="pattern",
            source="agent",
        )
        suite_dir = Path(__file__).resolve().parents[1] / "eval"
        suite = EvalSuite.load_beir_dir(suite_dir, name="sample")
        rep = evaluate(
            store,
            suite,
            k=5,
            thresholds=EvalThresholds(min_mrr=0.0, min_ndcg_at_k=0.0, k=5),
        )
        assert rep.per_query
        assert rep.mrr >= 0.0
    finally:
        store.close()


def test_feature_not_available_judge() -> None:
    from tapps_brain import evaluation as ev
    from tapps_brain._feature_flags import feature_flags

    feature_flags.reset()
    feature_flags._cache["anthropic_sdk"] = False
    try:
        with pytest.raises(ev.FeatureNotAvailable):
            ev.AnthropicJudge()
    finally:
        feature_flags.reset()


def test_evaluate_with_judge_inline(tmp_path: Path) -> None:
    root = tmp_path / "ej"
    root.mkdir()
    store = MemoryStore(root)
    try:
        store.save("doc-a", "alpha beta gamma", tier="pattern")
        judge = MagicMock()
        judge.judge_relevance.return_value = JudgeResult(score=1.0, reasoning="ok", confident=True)
        rep = evaluate_with_judge(
            store,
            [("q1", "alpha")],
            judge,
            k=3,
            thresholds=EvalThresholds(min_mrr=0.0, min_ndcg_at_k=0.0, k=3),
        )
        assert rep.per_query
    finally:
        store.close()


def test_eval_suite_yaml_invalid(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("- not a dict\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        EvalSuite.load_yaml(p)


def test_openai_judge_not_installed() -> None:
    from tapps_brain import evaluation as ev
    from tapps_brain._feature_flags import feature_flags

    feature_flags.reset()
    feature_flags._cache["openai_sdk"] = False
    try:
        with pytest.raises(ev.FeatureNotAvailable):
            OpenAIJudge()
    finally:
        feature_flags.reset()


def test_anthropic_judge_mock() -> None:
    from tapps_brain._feature_flags import feature_flags

    inst = MagicMock()
    block = MagicMock()
    block.text = '{"reasoning":"ok","score":1,"confident":true}'
    inst.messages.create.return_value = MagicMock(content=[block])
    anthropic_mod = MagicMock()
    anthropic_mod.Anthropic.return_value = inst
    feature_flags.reset()
    feature_flags._cache["anthropic_sdk"] = True
    old = sys.modules.get("anthropic")
    try:
        sys.modules["anthropic"] = anthropic_mod
        j = AnthropicJudge()
        r = j.judge_relevance("q", "mem")
        assert r.score == 1.0
    finally:
        if old is not None:
            sys.modules["anthropic"] = old
        else:
            sys.modules.pop("anthropic", None)
        feature_flags.reset()


def test_openai_judge_mock() -> None:
    from tapps_brain._feature_flags import feature_flags

    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"reasoning":"x","score":0,"confident":true}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    openai_mod = MagicMock()
    openai_mod.OpenAI.return_value = client
    feature_flags.reset()
    feature_flags._cache["openai_sdk"] = True
    old = sys.modules.get("openai")
    try:
        sys.modules["openai"] = openai_mod
        j = OpenAIJudge()
        r = j.judge_relevance("q", "mem")
        assert r.score == 0.0
    finally:
        if old is not None:
            sys.modules["openai"] = old
        else:
            sys.modules.pop("openai", None)
        feature_flags.reset()


def test_cascaded_judge_escalation() -> None:
    from tapps_brain.evaluation import CascadedJudge, JudgeResult, LLMJudge

    class Low(LLMJudge):
        def judge_relevance(self, q: str, v: str) -> JudgeResult:
            return JudgeResult(score=0.0, reasoning="x", confident=False)

    class High(LLMJudge):
        def judge_relevance(self, q: str, v: str) -> JudgeResult:
            return JudgeResult(score=1.0, reasoning="y", confident=True)

    cj = CascadedJudge(cheap=Low(), expensive=High())
    r = cj.judge_relevance("q", "v")
    assert r.score == 1.0
    assert cj.escalations == 1
    assert cj.escalation_rate == 1.0


def test_evaluate_with_judge_skips(tmp_path: Path) -> None:
    root = tmp_path / "sk"
    root.mkdir()
    store = MemoryStore(root)
    try:
        judge = MagicMock()
        judge.judge_relevance.return_value = JudgeResult(score=1.0, confident=True)
        empty = evaluate_with_judge(
            store,
            [("a", "  "), ("b", "nohitsuniquexyz")],
            judge,
            k=3,
            thresholds=EvalThresholds(min_mrr=0.0, min_ndcg_at_k=0.0, k=3),
        )
        assert isinstance(empty.per_query, list)
    finally:
        store.close()
