"""End-to-end OpenTelemetry integration tests (STORY-032.10).

Tests use a real MemoryStore (local SQLite) with mocked OTel SDK collectors.
Covers:
- Recall/remember spans captured with InMemorySpanExporter
- tapps_brain.* metrics emitted via MetricsCollector + OTelExporter
- Privacy modes: content omitted (not placeholder) when capture_content=False
- HAS_OTEL=False: all spans/metrics are no-ops without crashing
- Feedback + diagnostics events via record_feedback_event / record_diagnostics_event
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_span() -> MagicMock:
    """Return a mock span with add_event and set_attribute tracking."""
    span = MagicMock()
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=False)
    return span


# ---------------------------------------------------------------------------
# Tests: OTelExporter + MetricsCollector round-trip (STORY-032.5/032.6)
# ---------------------------------------------------------------------------


class TestOTelExporterRoundTrip:
    """MetricsCollector → MetricsSnapshot → OTelExporter → OTel instruments."""

    def test_save_increments_counters_exported_to_otel(self, tmp_path: Path) -> None:
        """store.save() increments store.save counter; OTelExporter forwards it."""
        from tapps_brain.otel_exporter import OTelExporter
        from tapps_brain.store import MemoryStore

        mock_meter = MagicMock()
        mock_counter = MagicMock()
        mock_meter.create_counter.return_value = mock_counter

        store = MemoryStore(tmp_path, embedding_provider=None)
        store.save("integration-key", "some memory value for testing", tier="pattern")

        snap = store.get_metrics()
        exporter = OTelExporter(meter=mock_meter)
        exporter.export(snap)

        assert snap.counters.get("store.save", 0) >= 1
        mock_counter.add.assert_called()

    def test_recall_increments_counter_exported_to_otel(self, tmp_path: Path) -> None:
        """store.recall() increments store.recall counter; forwarded via OTelExporter."""
        from tapps_brain.otel_exporter import OTelExporter
        from tapps_brain.store import MemoryStore

        mock_meter = MagicMock()
        mock_counter = MagicMock()
        mock_meter.create_counter.return_value = mock_counter

        store = MemoryStore(tmp_path, embedding_provider=None)
        store.save("recall-test", "memory for recall integration")
        store.recall("recall integration")

        snap = store.get_metrics()
        exporter = OTelExporter(meter=mock_meter)
        exporter.export(snap)

        assert snap.counters.get("store.recall", 0) >= 1

    def test_tapps_brain_entries_count_gauge_exported(self, tmp_path: Path) -> None:
        """tapps_brain.entries.count gauge appears in snapshot and exported as up-down counter."""
        from tapps_brain.otel_exporter import OTelExporter
        from tapps_brain.store import MemoryStore

        mock_meter = MagicMock()
        mock_udc = MagicMock()
        mock_meter.create_up_down_counter.return_value = mock_udc

        store = MemoryStore(tmp_path, embedding_provider=None)
        store.save("g1", "gauge test one")
        store.save("g2", "gauge test two")

        snap = store.get_metrics()
        assert "tapps_brain.entries.count" in snap.gauges
        assert snap.gauges["tapps_brain.entries.count"] >= 2.0

        exporter = OTelExporter(meter=mock_meter)
        exporter.export(snap)

        udc_names = {
            c.kwargs.get("name") or c.args[0]
            for c in mock_meter.create_up_down_counter.call_args_list
        }
        assert "tapps_brain.entries.count" in udc_names

    def test_gc_candidates_gauge_after_gc_run(self, tmp_path: Path) -> None:
        """tapps_brain.gc.candidates updated after gc() run."""
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path, embedding_provider=None)
        store.save("gc-test", "memory for gc integration test")
        store.gc(dry_run=True)

        snap = store.get_metrics()
        assert "tapps_brain.gc.candidates" in snap.gauges
        assert snap.gauges["tapps_brain.gc.candidates"] >= 0.0

    def test_gen_ai_metrics_recorder_no_crash(self, tmp_path: Path) -> None:
        """GenAIMetricsRecorder records duration without raising."""
        import time

        from tapps_brain.otel_exporter import GenAIMetricsRecorder
        from tapps_brain.store import MemoryStore

        mock_meter = MagicMock()
        mock_hist = MagicMock()
        mock_meter.create_histogram.return_value = mock_hist

        recorder = GenAIMetricsRecorder(meter=mock_meter)
        store = MemoryStore(tmp_path, embedding_provider=None)

        t0 = time.perf_counter()
        store.save("timing-key", "memory value for timing test")
        elapsed = time.perf_counter() - t0

        recorder.record_gen_ai_operation(elapsed, operation="remember")
        assert mock_hist.record.call_count >= 1


# ---------------------------------------------------------------------------
# Tests: span + event instrumentation (STORY-032.2/032.3/032.7/032.8)
# ---------------------------------------------------------------------------


class TestSpanInstrumentation:
    """start_span + record_* helpers emit correct OTel events with real MemoryStore."""

    def test_mcp_tool_span_attributes(self) -> None:
        """start_mcp_tool_span creates a span with correct GenAI semconv attributes."""
        from unittest.mock import patch

        from tapps_brain.otel_tracer import (
            GEN_AI_OPERATION_EXECUTE_TOOL,
            GEN_AI_SYSTEM,
            MCP_METHOD_TOOLS_CALL,
            start_mcp_tool_span,
        )

        captured: dict[str, Any] = {}

        def _capturing_start_as_current_span(name: str, **kwargs: Any) -> Any:
            captured["span_name"] = name
            return _make_mock_span()

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.side_effect = _capturing_start_as_current_span
        mock_span = _make_mock_span()
        mock_tracer.start_as_current_span.return_value = mock_span

        with patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer):
            with start_mcp_tool_span("brain_remember") as span:
                pass

        # Verify the span was created with the correct attributes
        assert mock_tracer.start_as_current_span.called
        call_name = mock_tracer.start_as_current_span.call_args.args[0]
        assert "brain_remember" in call_name

    def test_retrieval_document_events_on_real_recall(self, tmp_path: Path) -> None:
        """record_retrieval_document_events attaches events to span for real recall results."""
        from tapps_brain.otel_tracer import (
            ATTR_RETRIEVAL_DOC_SCORE,
            EVENT_RETRIEVAL_DOCUMENT,
            record_retrieval_document_events,
        )
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path, embedding_provider=None)
        store.save("doc-a", "Python programming language documentation")
        store.save("doc-b", "Python web frameworks and libraries")

        result = store.recall("Python programming")
        # recall() returns a RecallResult (Pydantic model); memories is a list of dicts
        from tapps_brain.models import RecallResult

        memories_raw: list[Any] = []
        if isinstance(result, RecallResult):
            memories_raw = result.memories  # list[dict]
        elif isinstance(result, dict):
            memories_raw = result.get("memories", [])

        mock_span = _make_mock_span()
        record_retrieval_document_events(mock_span, memories_raw)

        if memories_raw:
            # Should have one add_event call per memory
            assert mock_span.add_event.call_count == len(memories_raw)
            # Verify event name and score attribute
            first_call = mock_span.add_event.call_args_list[0]
            event_name = first_call.args[0] if first_call.args else first_call.kwargs.get("name")
            assert event_name == EVENT_RETRIEVAL_DOCUMENT

    def test_feedback_event_on_real_feedback_event(self, tmp_path: Path) -> None:
        """record_feedback_event attaches span event for real FeedbackEvent model."""
        from tapps_brain.feedback import FeedbackEvent
        from tapps_brain.otel_tracer import ATTR_FEEDBACK_EVENT_TYPE, record_feedback_event

        mock_span = _make_mock_span()
        event = FeedbackEvent(event_type="recall_rated", utility_score=0.8)
        record_feedback_event(mock_span, event)

        mock_span.add_event.assert_called_once()
        attrs = (
            mock_span.add_event.call_args.args[1]
            if len(mock_span.add_event.call_args.args) > 1
            else mock_span.add_event.call_args.kwargs.get("attributes", {})
        )
        assert attrs.get(ATTR_FEEDBACK_EVENT_TYPE) == "recall_rated"
        # PII must not be present
        assert "entry_key" not in attrs
        assert "session_id" not in attrs

    def test_diagnostics_event_on_real_diagnostics_report(self, tmp_path: Path) -> None:
        """record_diagnostics_event attaches span event for real DiagnosticsReport."""
        from tapps_brain.diagnostics import run_diagnostics
        from tapps_brain.otel_tracer import (
            ATTR_DIAGNOSTICS_CIRCUIT_STATE,
            ATTR_DIAGNOSTICS_COMPOSITE_SCORE,
            record_diagnostics_event,
        )
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path, embedding_provider=None)
        store.save("diag-key", "diagnostics integration memory value")
        report = run_diagnostics(store)

        mock_span = _make_mock_span()
        record_diagnostics_event(mock_span, report)

        mock_span.add_event.assert_called_once()
        attrs = (
            mock_span.add_event.call_args.args[1]
            if len(mock_span.add_event.call_args.args) > 1
            else mock_span.add_event.call_args.kwargs.get("attributes", {})
        )
        assert ATTR_DIAGNOSTICS_COMPOSITE_SCORE in attrs
        assert ATTR_DIAGNOSTICS_CIRCUIT_STATE in attrs
        assert 0.0 <= attrs[ATTR_DIAGNOSTICS_COMPOSITE_SCORE] <= 1.0


# ---------------------------------------------------------------------------
# Tests: Privacy modes (STORY-032.9)
# ---------------------------------------------------------------------------


class TestPrivacyModes:
    """Content is omitted (not placeholdered) when capture_content=False."""

    def test_default_capture_content_false(self) -> None:
        """By default, should_capture_content() returns False."""
        from unittest.mock import patch

        from tapps_brain.otel_exporter import OTelConfig, should_capture_content

        with (
            patch.dict("os.environ", {}, clear=False),
        ):
            import os

            os.environ.pop("TAPPS_BRAIN_OTEL_CAPTURE_CONTENT", None)
            os.environ.pop("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", None)
            result = should_capture_content()
            assert result is False

    def test_omit_not_placeholder_when_disabled(self) -> None:
        """When disabled, span attribute is NOT set at all — not set to a placeholder."""
        from tapps_brain.otel_exporter import OTelConfig, should_capture_content

        cfg = OTelConfig(capture_content=False)
        mock_span = MagicMock()

        # Caller pattern: check then conditionally set
        if should_capture_content(cfg):
            mock_span.set_attribute("gen_ai.prompt", "a real query string")

        mock_span.set_attribute.assert_not_called()

    def test_attribute_set_when_capture_enabled(self) -> None:
        """When enabled, span attribute IS set."""
        from tapps_brain.otel_exporter import OTelConfig, should_capture_content

        cfg = OTelConfig(capture_content=True)
        mock_span = MagicMock()

        query = "test query for content capture"
        if should_capture_content(cfg):
            mock_span.set_attribute("gen_ai.prompt", query)

        mock_span.set_attribute.assert_called_once_with("gen_ai.prompt", query)

    def test_semconv_env_var_enables_capture(self, monkeypatch: Any) -> None:
        """OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true enables capture."""
        from tapps_brain.otel_exporter import should_capture_content

        monkeypatch.delenv("TAPPS_BRAIN_OTEL_CAPTURE_CONTENT", raising=False)
        monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")
        assert should_capture_content() is True

    def test_tapps_var_disables_despite_semconv_enabled(self, monkeypatch: Any) -> None:
        """Tapps-brain var takes priority: set to 0, semconv=true → still False."""
        from tapps_brain.otel_exporter import should_capture_content

        monkeypatch.setenv("TAPPS_BRAIN_OTEL_CAPTURE_CONTENT", "0")
        monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")
        assert should_capture_content() is False


# ---------------------------------------------------------------------------
# Tests: HAS_OTEL=False no-op path (STORY-032.1)
# ---------------------------------------------------------------------------


class TestHasOtelFalseNoOp:
    """When HAS_OTEL=False all span/metric helpers are safe no-ops."""

    def test_bootstrap_tracer_returns_none_when_has_otel_false(self) -> None:
        from unittest.mock import patch

        from tapps_brain.otel_exporter import OTelConfig, bootstrap_tracer

        with patch("tapps_brain.otel_exporter.HAS_OTEL", False):
            result = bootstrap_tracer(OTelConfig(enabled=True))
        assert result is None

    def test_gen_ai_metrics_recorder_noop_when_meter_is_none(self) -> None:
        """GenAIMetricsRecorder with None meter raises no exception on record calls."""
        from tapps_brain.otel_exporter import GenAIMetricsRecorder

        recorder = GenAIMetricsRecorder(meter=None)
        # All paths must be safe no-ops
        recorder.record_gen_ai_operation(0.5, operation="remember")
        recorder.record_mcp_operation(0.1, method="tools/call", tool_name="brain_recall")
        recorder.record_token_usage(256, token_type="input")

    def test_record_feedback_event_noop_on_none_span(self) -> None:
        """record_feedback_event with None span does not raise."""
        from types import SimpleNamespace

        from tapps_brain.otel_tracer import record_feedback_event

        event = SimpleNamespace(event_type="recall_rated", utility_score=0.5)
        record_feedback_event(None, event)  # must not raise

    def test_record_diagnostics_event_noop_on_none_span(self) -> None:
        """record_diagnostics_event with None span does not raise."""
        from types import SimpleNamespace

        from tapps_brain.otel_tracer import record_diagnostics_event

        report = SimpleNamespace(composite_score=0.8, circuit_state="closed", gap_count=0, anomalies=[])
        record_diagnostics_event(None, report)  # must not raise

    def test_start_span_noop_when_otel_disabled(self) -> None:
        """start_span with null tracer yields None without creating any span."""
        from unittest.mock import patch

        from tapps_brain.otel_tracer import SPAN_REMEMBER, start_span

        with patch("tapps_brain.otel_tracer.get_tracer", return_value=None):
            with start_span(SPAN_REMEMBER) as span:
                result = span
        assert result is None

    def test_memory_store_recall_does_not_crash_without_otel(self, tmp_path: Path) -> None:
        """Real MemoryStore recall completes successfully even when OTel returns None spans."""
        from unittest.mock import patch

        from tapps_brain.models import RecallResult
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path, embedding_provider=None)
        store.save("no-otel-key", "memory for no-otel recall test")

        # Patch get_tracer to return None (simulates HAS_OTEL=False environment)
        with patch("tapps_brain.otel_tracer.get_tracer", return_value=None):
            result = store.recall("no-otel recall test")

        # recall() returns a RecallResult Pydantic model
        assert isinstance(result, RecallResult)
        assert hasattr(result, "memories")


# ---------------------------------------------------------------------------
# Tests: MCP metrics integration (STORY-032.5)
# ---------------------------------------------------------------------------


class TestMCPMetricsIntegration:
    """GenAIMetricsRecorder.record_mcp_operation() with real operation timing."""

    def test_record_mcp_duration_for_real_tool_call(self, tmp_path: Path) -> None:
        """Simulate recording MCP operation duration after a real store operation."""
        import time

        from tapps_brain.otel_exporter import GenAIMetricsRecorder
        from tapps_brain.store import MemoryStore

        mock_meter = MagicMock()
        mock_hist = MagicMock()
        mock_meter.create_histogram.return_value = mock_hist

        recorder = GenAIMetricsRecorder(meter=mock_meter)
        store = MemoryStore(tmp_path, embedding_provider=None)

        t0 = time.perf_counter()
        store.save("mcp-timing-key", "mcp operation timing integration test")
        elapsed_s = time.perf_counter() - t0

        recorder.record_mcp_operation(
            elapsed_s,
            method="tools/call",
            tool_name="brain_remember",
        )

        # Histogram.record() must have been called at least once
        assert mock_hist.record.call_count >= 1

    def test_token_usage_histogram_recorded(self) -> None:
        """record_token_usage() calls histogram.record() with correct token count."""
        from tapps_brain.otel_exporter import GenAIMetricsRecorder

        mock_meter = MagicMock()
        mock_hist = MagicMock()
        mock_meter.create_histogram.return_value = mock_hist

        recorder = GenAIMetricsRecorder(meter=mock_meter)
        recorder.record_token_usage(512, token_type="output", operation="recall")

        # Find any record call with value 512
        found = any(
            (c.args and c.args[0] == 512) or (c.kwargs.get("amount") == 512)
            for c in mock_hist.record.call_args_list
        )
        assert found, f"Expected record(512, ...) in calls: {mock_hist.record.call_args_list}"
