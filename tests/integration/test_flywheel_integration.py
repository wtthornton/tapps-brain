"""Integration tests for EPIC-031 flywheel (real SQLite)."""

from __future__ import annotations

from pathlib import Path

from tapps_brain.diagnostics import CircuitState
from tapps_brain.evaluation import EvalSuite, EvalThresholds, evaluate
from tapps_brain.flywheel import FeedbackProcessor, FlywheelConfig, generate_report
from tapps_brain.store import MemoryStore


def test_full_loop_feedback_process_report(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    store = MemoryStore(root)
    try:
        store.save("k1", "JWT authentication for APIs", tier="pattern", confidence=0.6)
        store.rate_recall("k1", rating="helpful")
        out = FeedbackProcessor(FlywheelConfig()).process_feedback(store)
        assert out["confidence_adjustments"] >= 1
        rep = store.diagnostics(record_history=False)
        assert rep.composite_score >= 0.0
        qr = generate_report(store, period_days=1)
        assert qr.rendered_text
    finally:
        store.close()


def test_eval_golden_dataset(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    root.mkdir()
    store = MemoryStore(root)
    try:
        suite_dir = Path(__file__).resolve().parents[1] / "eval"
        suite = EvalSuite.load_beir_dir(suite_dir)
        for doc_id, doc in suite.corpus.docs.items():
            text = f"{doc.title} {doc.text}".strip()
            store.save(doc_id, text, tier="pattern", source="agent")
        report = evaluate(
            store,
            suite,
            k=5,
            thresholds=EvalThresholds(min_mrr=0.0, min_ndcg_at_k=0.0, k=5),
        )
        assert len(report.per_query) == len(suite.qrels.qrels)
        assert report.mrr >= 0.0
    finally:
        store.close()


def test_recall_sets_quality_warning_when_circuit_not_closed(tmp_path: Path) -> None:
    root = tmp_path / "cb"
    root.mkdir()
    store = MemoryStore(root)
    try:
        store.save("rk", "python sqlite memory", tier="pattern")
        for st, needle in (
            (CircuitState.DEGRADED, "degraded"),
            (CircuitState.OPEN, "critical"),
            (CircuitState.HALF_OPEN, "recovering"),
        ):
            store._circuit_breaker.state = st
            res = store.recall("python")
            assert res.quality_warning and needle in res.quality_warning.lower()
        store._circuit_breaker.state = CircuitState.CLOSED
    finally:
        store.close()


def test_process_feedback_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "id"
    root.mkdir()
    store = MemoryStore(root)
    try:
        store.save("x", "v", tier="context", confidence=0.5)
        store.rate_recall("x", rating="partial")
        fp = FeedbackProcessor(FlywheelConfig())
        fp.process_feedback(store)
        second = fp.process_feedback(store)
        assert second["processed_events"] == 0
    finally:
        store.close()
