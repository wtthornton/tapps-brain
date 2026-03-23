"""Unit tests for flywheel (EPIC-031)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

import tapps_brain.flywheel as flywheel_mod
from tapps_brain.feedback import FeedbackEvent
from tapps_brain.flywheel import (
    FeedbackProcessor,
    FlywheelConfig,
    GapTracker,
    KnowledgeGap,
    aggregate_hive_feedback,
    beta_mean,
    default_report_registry,
    generate_report,
    jaccard_similarity,
    knowledge_gap_summary_for_diagnostics,
    process_hive_feedback,
)
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


class TestConfidencePipeline:
    def test_beta_mean_jeffreys(self) -> None:
        assert abs(beta_mean(0.0, 0.0) - 0.5) < 1e-9
        assert beta_mean(1.0, 0.0) > 0.5

    def test_process_feedback_requires_memory_store(self) -> None:
        with pytest.raises(TypeError, match="MemoryStore"):
            FeedbackProcessor(FlywheelConfig()).process_feedback(MagicMock())  # type: ignore[arg-type]

    def test_process_feedback_implicit_and_issue_types(self, tmp_path: Path) -> None:
        root = tmp_path / "imp"
        root.mkdir()
        store = MemoryStore(root)
        try:
            store.save("ik1", "body one", tier="context", source="agent", confidence=0.6)
            store.save("ik2", "body two", tier="context", source="agent", confidence=0.6)
            store.save("ik3", "body three", tier="context", source="agent", confidence=0.6)
            store.record_feedback("implicit_positive", entry_key="ik1")
            store.record_feedback("implicit_negative", entry_key="ik2")
            store.record_feedback("implicit_correction", entry_key="ik3")
            store.record_feedback("issue_flagged", entry_key="ik3", details={"issue": "bad"})
            store.rate_recall("ik1", rating="partial")
            r = FeedbackProcessor(FlywheelConfig()).process_feedback(store)
            assert r["confidence_adjustments"] >= 4
        finally:
            store.close()

    def test_process_feedback_since_filters_updates(self, tmp_path: Path) -> None:
        root = tmp_path / "since"
        root.mkdir()
        store = MemoryStore(root)
        try:
            store.save("sk", "x", tier="context", source="agent", confidence=0.5)
            store.rate_recall("sk", rating="helpful")
            r = FeedbackProcessor(FlywheelConfig()).process_feedback(
                store, since="2099-01-01T00:00:00+00:00"
            )
            assert r["processed_events"] >= 1
            assert r["confidence_adjustments"] == 0
            e = store.get("sk")
            assert e is not None
            assert e.confidence == 0.5
        finally:
            store.close()

    def test_process_feedback_bad_cursor_json(self, tmp_path: Path) -> None:
        root = tmp_path / "pc"
        root.mkdir()
        store = MemoryStore(root)
        try:
            store._persistence.flywheel_meta_set("feedback_cursor", "not-json")
            out = FeedbackProcessor(FlywheelConfig()).process_feedback(store)
            assert "processed_events" in out
        finally:
            store.close()

    def test_process_feedback_helpful(self, tmp_path: Path) -> None:
        root = tmp_path / "p"
        root.mkdir()
        store = MemoryStore(root)
        try:
            store.save(
                "mem-a",
                "content about auth",
                tier="context",
                source="agent",
                confidence=0.5,
            )
            store.rate_recall("mem-a", rating="helpful")
            r = FeedbackProcessor(FlywheelConfig()).process_feedback(store)
            assert r["confidence_adjustments"] >= 1
            e2 = store.get("mem-a")
            assert e2 is not None
            assert e2.confidence > 0.5
            r2 = FeedbackProcessor(FlywheelConfig()).process_feedback(store)
            assert r2["processed_events"] == 0
        finally:
            store.close()

    def test_process_feedback_irrelevant(self, tmp_path: Path) -> None:
        root = tmp_path / "p2"
        root.mkdir()
        store = MemoryStore(root)
        try:
            store.save(
                "mem-b",
                "text",
                tier="context",
                source="agent",
                confidence=0.9,
            )
            store.rate_recall("mem-b", rating="irrelevant")
            FeedbackProcessor(FlywheelConfig()).process_feedback(store)
            e2 = store.get("mem-b")
            assert e2 is not None
            assert e2.confidence < 0.9
        finally:
            store.close()


class TestKnowledgeGaps:
    def test_analyze_gaps_semantic_fallback(self, tmp_path: Path) -> None:
        root = tmp_path / "sem"
        root.mkdir()
        store = MemoryStore(root)
        try:
            store.report_gap("alpha beta")
            out = GapTracker().analyze_gaps(store, use_semantic_clustering=True)
            assert isinstance(out, list)
        finally:
            store.close()

    def test_jaccard(self) -> None:
        assert jaccard_similarity("a b c", "b c d") == 0.5

    def test_jaccard_empty_edge_cases(self) -> None:
        assert jaccard_similarity("", "") == 1.0
        assert jaccard_similarity("only", "") == 0.0

    def test_analyze_gaps_gap_query_raises_no_signals(self, tmp_path: Path) -> None:
        root = tmp_path / "qe"
        root.mkdir()
        store = MemoryStore(root)
        try:
            store.query_feedback = MagicMock(side_effect=RuntimeError("db"))  # type: ignore[method-assign]
            assert GapTracker().analyze_gaps(store) == []
        finally:
            store.close()

    def test_gap_clustering(self, tmp_path: Path) -> None:
        root = tmp_path / "g"
        root.mkdir()
        store = MemoryStore(root)
        try:
            store.report_gap("how to deploy production")
            store.report_gap("deploy to production how")
            gaps = GapTracker().top_gaps(store, limit=5)
            assert gaps
            assert gaps[0].count >= 1.5
        finally:
            store.close()

    def test_zero_result_signal(self, tmp_path: Path) -> None:
        root = tmp_path / "z"
        root.mkdir()
        store = MemoryStore(root)
        try:
            store.recall("zzzznonexistentqueryunique", session_id="s1")
            sig = store.zero_result_gap_signals()
            assert sig
            gaps = GapTracker().top_gaps(store, limit=3)
            assert any("zzzznonexistentqueryunique" in g.query_pattern for g in gaps)
        finally:
            store.close()

    def test_gap_descriptions_and_trend_weights(self, tmp_path: Path) -> None:
        root = tmp_path / "tr"
        root.mkdir()
        store = MemoryStore(root)
        try:
            now = datetime.now(tz=UTC)
            fs = store._get_feedback_store()
            base_q = "trend weight query phrase"
            for days_ago in (5, 6, 7):
                fs.record(
                    FeedbackEvent(
                        event_type="gap_reported",
                        details={
                            "query": base_q,
                            "description": "seen-once",
                        },
                        timestamp=(now - timedelta(days=days_ago)).isoformat(),
                    )
                )
            for days_ago in (35, 36, 37, 38):
                fs.record(
                    FeedbackEvent(
                        event_type="gap_reported",
                        details={"query": base_q},
                        timestamp=(now - timedelta(days=days_ago)).isoformat(),
                    )
                )
            gaps = GapTracker(jaccard_threshold=0.9).top_gaps(store, limit=3)
            assert gaps
            assert "seen-once" in gaps[0].descriptions
            store.save(
                "arch",
                base_q + " extra",
                tier="architectural",
                source="agent",
            )
            gaps2 = GapTracker(jaccard_threshold=0.9).top_gaps(store, limit=3)
            assert gaps2[0].priority_score > gaps[0].priority_score
        finally:
            store.close()

    def test_gap_trend_prev_heavier(self, tmp_path: Path) -> None:
        root = tmp_path / "tr2"
        root.mkdir()
        store = MemoryStore(root)
        try:
            now = datetime.now(tz=UTC)
            fs = store._get_feedback_store()
            q = "prev heavier unique phrase"
            fs.record(
                FeedbackEvent(
                    event_type="gap_reported",
                    details={"query": q},
                    timestamp=(now - timedelta(days=5)).isoformat(),
                )
            )
            for days_ago in (35, 36, 37):
                fs.record(
                    FeedbackEvent(
                        event_type="gap_reported",
                        details={"query": q},
                        timestamp=(now - timedelta(days=days_ago)).isoformat(),
                    )
                )
            g = GapTracker(jaccard_threshold=0.99).top_gaps(store, limit=1)[0]
            assert g.priority_score > 0
        finally:
            store.close()


class TestSelfReport:
    def test_generate_report_markdown(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        root.mkdir()
        store = MemoryStore(root)
        try:
            qr = generate_report(store, period_days=1)
            assert "Quality report" in qr.rendered_text
            assert qr.structured_data.get("composite_score") is not None
        finally:
            store.close()

    def test_generate_report_self_memory_and_bad_section(self, tmp_path: Path) -> None:
        root = tmp_path / "rs"
        root.mkdir()
        store = MemoryStore(root)
        try:

            class BadSection:
                name = "bad_sec"
                priority = 5

                def should_include(self, data: object) -> bool:
                    return True

                def render(self, data: object) -> str:
                    raise RuntimeError("boom")

            qr = generate_report(
                store,
                period_days=1,
                extra_sections=[BadSection()],  # type: ignore[list-item]
                config=FlywheelConfig(store_self_report_memory=True),
            )
            assert qr.rendered_text
            assert any("self-report" in e.tags for e in store.list_all())
        finally:
            store.close()


class TestReportTemplates:
    def test_report_section_renders(self) -> None:
        g = KnowledgeGap(
            query_pattern="q",
            count=1.0,
            first_reported="2026-01-01T00:00:00+00:00",
            last_reported="2026-01-02T00:00:00+00:00",
            descriptions=[],
            priority_score=1.0,
        )
        dim = MagicMock()
        dim.score = 0.42
        data = flywheel_mod.ReportData(
            diagnostics_report={
                "composite_score": 0.55,
                "circuit_state": "degraded",
                "dimensions": {"dup": dim},
                "anomalies": ["slow"],
                "recommendations": ["compact"],
            },
            feedback_summary={"recall_rated": 2},
            knowledge_gaps=[g],
        )
        assert "0.420" in flywheel_mod._DimensionBreakdownSection().render(data)
        assert "slow" in flywheel_mod._AnomalyAlertsSection().render(data)
        assert "recall_rated" in flywheel_mod._FeedbackSummarySection().render(data)
        assert "q" in flywheel_mod._KnowledgeGapsSection().render(data)
        assert "compact" in flywheel_mod._RecommendationsSection().render(data)

    def test_feedback_summary_counts_query_raises(self, tmp_path: Path) -> None:
        root = tmp_path / "fsc"
        root.mkdir()
        store = MemoryStore(root)
        try:
            store.query_feedback = MagicMock(side_effect=RuntimeError("x"))  # type: ignore[method-assign]
            assert flywheel_mod._feedback_summary_counts(store) == {}
        finally:
            store.close()

    def test_parse_iso_and_tier_weight_helpers(self, tmp_path: Path) -> None:
        root = tmp_path / "ph"
        root.mkdir()
        store = MemoryStore(root)
        try:
            naive = flywheel_mod._parse_iso("2020-05-01T12:00:00")
            assert naive.tzinfo == UTC
            bad = flywheel_mod._parse_iso("not-an-iso-timestamp")
            assert bad.tzinfo == UTC
            store.save("t1", "python data science", tier="architectural", source="agent")

            def _boom(_q: str) -> list[object]:
                raise RuntimeError("search down")

            store.search = _boom  # type: ignore[method-assign]
            assert flywheel_mod._estimate_tier_weight(store, "python") == 1.0
        finally:
            store.close()

    def test_knowledge_gap_summary_and_gap_query_count_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "kgs"
        root.mkdir()
        store = MemoryStore(root)
        try:
            store.recall("only-zero-result-gap-signal-abc", session_id="s")
            calls: list[int] = []

            def _qf(**kwargs: object) -> list[FeedbackEvent]:
                calls.append(1)
                if len(calls) == 1:
                    return []
                raise RuntimeError("second")

            monkeypatch.setattr(store, "query_feedback", _qf)
            text = knowledge_gap_summary_for_diagnostics(store)
            assert text and "0 knowledge gaps reported" in text
        finally:
            store.close()

    def test_registry_register(self) -> None:
        reg = default_report_registry()
        before = len(reg.sections_sorted())

        class Extra:
            name = "extra"
            priority = 15

            def should_include(self, data: object) -> bool:
                return False

            def render(self, data: object) -> str:
                return ""

        reg.register(Extra())  # type: ignore[arg-type]
        assert len(reg.sections_sorted()) == before + 1
        reg.unregister("extra")
        assert len(reg.sections_sorted()) == before


class TestCrossProjectAggregation:
    def test_aggregate_hive_feedback_none_store(self) -> None:
        assert aggregate_hive_feedback(None) is None

    def test_process_hive_feedback_noop(self, tmp_path: Path) -> None:
        root = tmp_path / "h"
        root.mkdir()
        store = MemoryStore(root)
        try:
            out = process_hive_feedback(getattr(store, "_hive_store", None))
            assert out.get("skipped") is True
        finally:
            store.close()

    def test_aggregate_hive_gaps_and_issues(self) -> None:
        hs = MagicMock()
        hs.query_feedback_events.return_value = [
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "namespace": "n",
                "entry_key": None,
                "event_type": "gap_reported",
                "utility_score": None,
                "details": {"query": "missing docs"},
                "source_project": "/p/a",
            },
            {
                "timestamp": "2026-01-02T00:00:00+00:00",
                "namespace": "n",
                "entry_key": "ik",
                "event_type": "issue_flagged",
                "utility_score": None,
                "details": {},
                "source_project": "/p/a",
            },
            {
                "timestamp": "2026-01-03T00:00:00+00:00",
                "namespace": "n",
                "entry_key": "ik",
                "event_type": "issue_flagged",
                "utility_score": None,
                "details": {},
                "source_project": "/p/b",
            },
        ]
        rep = aggregate_hive_feedback(hs)
        assert rep is not None
        assert rep.issue_hotspots
        assert rep.cross_project_gaps

    def test_aggregate_hive_feedback_rows(self) -> None:
        hs = MagicMock()
        hs.query_feedback_events.return_value = [
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "namespace": "n",
                "entry_key": "k1",
                "event_type": "recall_rated",
                "utility_score": 0.0,
                "details": {"rating": "irrelevant"},
                "source_project": "/p/a",
            },
            {
                "timestamp": "2026-01-02T00:00:00+00:00",
                "namespace": "n",
                "entry_key": "k1",
                "event_type": "recall_rated",
                "utility_score": 0.0,
                "details": {"rating": "irrelevant"},
                "source_project": "/p/b",
            },
            {
                "timestamp": "2026-01-03T00:00:00+00:00",
                "namespace": "n",
                "entry_key": "k1",
                "event_type": "recall_rated",
                "utility_score": 0.0,
                "details": {"rating": "irrelevant"},
                "source_project": "/p/c",
            },
        ]
        rep = aggregate_hive_feedback(hs)
        assert rep is not None
        assert rep.entry_feedback["n:k1"]["negative_project_count"] == 3

    def test_process_hive_feedback_penalty(self) -> None:
        hs = MagicMock()
        hs.query_feedback_events.return_value = [
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "namespace": "n",
                "entry_key": "k1",
                "event_type": "recall_rated",
                "utility_score": 0.0,
                "details": {"rating": "irrelevant"},
                "source_project": "/p/a",
            },
            {
                "timestamp": "2026-01-02T00:00:00+00:00",
                "namespace": "n",
                "entry_key": "k1",
                "event_type": "recall_rated",
                "utility_score": 0.0,
                "details": {"rating": "irrelevant"},
                "source_project": "/p/b",
            },
            {
                "timestamp": "2026-01-03T00:00:00+00:00",
                "namespace": "n",
                "entry_key": "k1",
                "event_type": "recall_rated",
                "utility_score": 0.0,
                "details": {"rating": "irrelevant"},
                "source_project": "/p/c",
            },
        ]
        hs.get_confidence.return_value = 0.8
        hs.patch_confidence.return_value = True
        out = process_hive_feedback(hs, threshold=3)
        assert out["updated"] == 1
        hs.patch_confidence.assert_called_once()

    def test_process_hive_feedback_skips_missing_confidence(self) -> None:
        hs = MagicMock()
        hs.query_feedback_events.return_value = [
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "namespace": "n",
                "entry_key": "k1",
                "event_type": "recall_rated",
                "utility_score": 0.0,
                "details": {"rating": "irrelevant"},
                "source_project": "/p/a",
            },
            {
                "timestamp": "2026-01-02T00:00:00+00:00",
                "namespace": "n",
                "entry_key": "k1",
                "event_type": "recall_rated",
                "utility_score": 0.0,
                "details": {"rating": "irrelevant"},
                "source_project": "/p/b",
            },
            {
                "timestamp": "2026-01-03T00:00:00+00:00",
                "namespace": "n",
                "entry_key": "k1",
                "event_type": "recall_rated",
                "utility_score": 0.0,
                "details": {"rating": "irrelevant"},
                "source_project": "/p/c",
            },
        ]
        hs.get_confidence.return_value = None
        out = process_hive_feedback(hs, threshold=3)
        assert out["updated"] == 0
        hs.patch_confidence.assert_not_called()

    def test_process_hive_feedback_skips_malformed_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hs = MagicMock()
        hs.query_feedback_events.return_value = []
        rep = MagicMock()
        rep.entry_feedback = {"no-namespace-separator": {"negative_project_count": 99}}
        monkeypatch.setattr(flywheel_mod, "aggregate_hive_feedback", lambda _h: rep)
        out = process_hive_feedback(hs)
        assert out["updated"] == 0
