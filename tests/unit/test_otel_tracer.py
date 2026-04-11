"""Unit tests for OpenTelemetry tracer instrumentation (STORY-061.1).

Uses mock tracers/spans so that the opentelemetry-sdk is not required
(only the API package is a core dep). Tests verify that:
- Span names match the canonical constants aligned with system-architecture.md.
- Spans are created on remember, recall, search, and hive hot paths.
- Span attributes are safe (no raw memory content, no query text).
- Exceptions are recorded and re-raised.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, call, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_tracer() -> tuple[MagicMock, MagicMock]:
    """Return (mock_tracer, mock_span) with start_as_current_span wired up."""
    mock_span = MagicMock()
    mock_span.__enter__ = MagicMock(return_value=mock_span)
    mock_span.__exit__ = MagicMock(return_value=False)

    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value = mock_span
    return mock_tracer, mock_span


# ---------------------------------------------------------------------------
# Tests for span name constants
# ---------------------------------------------------------------------------


class TestSpanNameConstants:
    """Span names must match system-architecture.md naming."""

    def test_span_names_have_tapps_brain_prefix(self) -> None:
        from tapps_brain.otel_tracer import (
            SPAN_HIVE_PROPAGATE,
            SPAN_HIVE_SEARCH,
            SPAN_RECALL,
            SPAN_REMEMBER,
            SPAN_SEARCH,
        )

        for name in (
            SPAN_REMEMBER,
            SPAN_RECALL,
            SPAN_SEARCH,
            SPAN_HIVE_PROPAGATE,
            SPAN_HIVE_SEARCH,
        ):
            assert name.startswith("tapps_brain."), f"{name!r} must start with 'tapps_brain.'"

    def test_remember_span_name(self) -> None:
        from tapps_brain.otel_tracer import SPAN_REMEMBER

        assert SPAN_REMEMBER == "tapps_brain.remember"

    def test_recall_span_name(self) -> None:
        from tapps_brain.otel_tracer import SPAN_RECALL

        assert SPAN_RECALL == "tapps_brain.recall"

    def test_search_span_name(self) -> None:
        from tapps_brain.otel_tracer import SPAN_SEARCH

        assert SPAN_SEARCH == "tapps_brain.search"

    def test_hive_propagate_span_name(self) -> None:
        from tapps_brain.otel_tracer import SPAN_HIVE_PROPAGATE

        assert SPAN_HIVE_PROPAGATE == "tapps_brain.hive.propagate"

    def test_hive_search_span_name(self) -> None:
        from tapps_brain.otel_tracer import SPAN_HIVE_SEARCH

        assert SPAN_HIVE_SEARCH == "tapps_brain.hive.search"


# ---------------------------------------------------------------------------
# Tests for get_tracer()
# ---------------------------------------------------------------------------


class TestGetTracer:
    """get_tracer() delegates to the OTel API."""

    def test_returns_tracer_when_api_available(self) -> None:
        from tapps_brain.otel_tracer import get_tracer

        mock_tracer = MagicMock()
        with patch("tapps_brain.otel_tracer.trace") as mock_trace:
            mock_trace.get_tracer.return_value = mock_tracer
            result = get_tracer()
        assert result is mock_tracer

    def test_uses_tapps_brain_instrumentation_name(self) -> None:
        from tapps_brain.otel_tracer import _INSTRUMENTATION_NAME, get_tracer

        with patch("tapps_brain.otel_tracer.trace") as mock_trace:
            get_tracer()
        mock_trace.get_tracer.assert_called_once_with(_INSTRUMENTATION_NAME)


# ---------------------------------------------------------------------------
# Tests for _service_name() and _service_version()
# ---------------------------------------------------------------------------


class TestServiceResourceHelpers:
    """Service name and version are read from environment variables."""

    def test_service_name_default(self) -> None:
        from tapps_brain.otel_tracer import _service_name

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OTEL_SERVICE_NAME", None)
            assert _service_name() == "tapps-brain"

    def test_service_name_from_env(self) -> None:
        from tapps_brain.otel_tracer import _service_name

        with patch.dict(os.environ, {"OTEL_SERVICE_NAME": "my-brain"}):
            assert _service_name() == "my-brain"

    def test_service_version_default(self) -> None:
        from tapps_brain.otel_tracer import _service_version

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OTEL_SERVICE_VERSION", None)
            assert _service_version() == ""

    def test_service_version_from_env(self) -> None:
        from tapps_brain.otel_tracer import _service_version

        with patch.dict(os.environ, {"OTEL_SERVICE_VERSION": "3.0.0"}):
            assert _service_version() == "3.0.0"


# ---------------------------------------------------------------------------
# Tests for start_span()
# ---------------------------------------------------------------------------


class TestStartSpan:
    """start_span() creates OTel INTERNAL spans with safe attributes."""

    def test_creates_span_with_correct_name(self) -> None:
        from tapps_brain.otel_tracer import SPAN_REMEMBER, start_span

        mock_tracer, _mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_span(SPAN_REMEMBER),
        ):
            pass

        mock_tracer.start_as_current_span.assert_called_once()
        args, _ = mock_tracer.start_as_current_span.call_args
        assert args[0] == SPAN_REMEMBER

    def test_span_kind_is_internal(self) -> None:
        from opentelemetry.trace import SpanKind

        from tapps_brain.otel_tracer import SPAN_REMEMBER, start_span

        mock_tracer, _ = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_span(SPAN_REMEMBER),
        ):
            pass

        _, kwargs = mock_tracer.start_as_current_span.call_args
        assert kwargs.get("kind") == SpanKind.INTERNAL

    def test_yields_span_to_caller(self) -> None:
        from tapps_brain.otel_tracer import SPAN_SEARCH, start_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_span(SPAN_SEARCH) as span,
        ):
            assert span is mock_span

    def test_sets_initial_attributes_on_span(self) -> None:
        from tapps_brain.otel_tracer import SPAN_REMEMBER, start_span

        mock_tracer, mock_span = _make_mock_tracer()
        attrs = {"memory.tier": "pattern", "memory.scope": "project"}
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_span(SPAN_REMEMBER, attrs),
        ):
            pass

        expected_calls = [call("memory.tier", "pattern"), call("memory.scope", "project")]
        mock_span.set_attribute.assert_has_calls(expected_calls, any_order=True)

    def test_sets_ok_status_on_success(self) -> None:
        from opentelemetry.trace import StatusCode

        from tapps_brain.otel_tracer import SPAN_RECALL, start_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_span(SPAN_RECALL),
        ):
            pass

        mock_span.set_status.assert_called_once()
        status_arg = mock_span.set_status.call_args[0][0]
        assert status_arg == StatusCode.OK

    def test_records_exception_and_reraises(self) -> None:
        from opentelemetry.trace import StatusCode

        from tapps_brain.otel_tracer import SPAN_REMEMBER, start_span

        mock_tracer, mock_span = _make_mock_tracer()

        class _TestError(RuntimeError):
            pass

        with patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer):
            try:
                with start_span(SPAN_REMEMBER):
                    raise _TestError("boom")
            except _TestError:
                pass
            else:
                raise AssertionError("Exception should have been re-raised")

        mock_span.record_exception.assert_called_once()
        exc_arg = mock_span.record_exception.call_args[0][0]
        assert isinstance(exc_arg, _TestError)

        mock_span.set_status.assert_called_once()
        status_code = mock_span.set_status.call_args[0][0]
        assert status_code == StatusCode.ERROR

    def test_no_attributes_when_none_passed(self) -> None:
        from tapps_brain.otel_tracer import SPAN_SEARCH, start_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_span(SPAN_SEARCH, None),
        ):
            pass

        mock_span.set_attribute.assert_not_called()

    def test_no_exception_recording_when_disabled(self) -> None:
        from tapps_brain.otel_tracer import SPAN_REMEMBER, start_span

        mock_tracer, mock_span = _make_mock_tracer()

        with patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer):
            try:
                with start_span(SPAN_REMEMBER, record_exception=False):
                    raise ValueError("test")
            except ValueError:
                pass

        mock_span.record_exception.assert_not_called()


# ---------------------------------------------------------------------------
# Integration-style tests verifying store.py emits spans
# ---------------------------------------------------------------------------


class TestStoreSpans:
    """Verify store hot paths emit OTel spans with correct names."""

    def _make_store(self, tmp_path: Any) -> Any:
        from tapps_brain.store import MemoryStore

        return MemoryStore(
            tmp_path,
            embedding_provider=None,
        )

    def test_save_emits_remember_span(self, tmp_path: Any) -> None:
        from tapps_brain.otel_tracer import SPAN_REMEMBER

        mock_tracer, _ = _make_mock_tracer()
        store = self._make_store(tmp_path)

        with (
            patch(
                "tapps_brain.store.start_span",
                wraps=__import__("tapps_brain.otel_tracer", fromlist=["start_span"]).start_span,
            ) as mock_start_span,
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
        ):
            store.save("test-key", "test value", tier="pattern")

        # Verify start_span was called with SPAN_REMEMBER
        called_names = [c.args[0] for c in mock_start_span.call_args_list]
        assert SPAN_REMEMBER in called_names

    def test_recall_emits_recall_span(self, tmp_path: Any) -> None:
        from tapps_brain.otel_tracer import SPAN_RECALL

        store = self._make_store(tmp_path)

        with (
            patch(
                "tapps_brain.store.start_span",
                wraps=__import__("tapps_brain.otel_tracer", fromlist=["start_span"]).start_span,
            ) as mock_start_span,
            patch("tapps_brain.otel_tracer.get_tracer", return_value=_make_mock_tracer()[0]),
        ):
            store.recall("what is the test")

        called_names = [c.args[0] for c in mock_start_span.call_args_list]
        assert SPAN_RECALL in called_names

    def test_search_emits_search_span(self, tmp_path: Any) -> None:
        from tapps_brain.otel_tracer import SPAN_SEARCH

        store = self._make_store(tmp_path)

        with (
            patch(
                "tapps_brain.store.start_span",
                wraps=__import__("tapps_brain.otel_tracer", fromlist=["start_span"]).start_span,
            ) as mock_start_span,
            patch("tapps_brain.otel_tracer.get_tracer", return_value=_make_mock_tracer()[0]),
        ):
            store.search("test query")

        called_names = [c.args[0] for c in mock_start_span.call_args_list]
        assert SPAN_SEARCH in called_names

    def test_remember_span_has_safe_attributes_no_content(self, tmp_path: Any) -> None:
        """Verify save() span attributes do not include raw memory content."""
        from tapps_brain.otel_tracer import SPAN_REMEMBER

        captured_attrs: dict[str, Any] = {}

        @contextmanager
        def _capturing_start_span(name: str, attributes: dict | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
            if name == SPAN_REMEMBER and attributes:
                captured_attrs.update(attributes)
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            yield mock_span

        store = self._make_store(tmp_path)

        with patch("tapps_brain.store.start_span", _capturing_start_span):
            store.save("my-key", "sensitive memory value", tier="pattern", scope="project")

        # Attributes should contain tier/scope (safe) but NOT key or raw content
        assert "memory.tier" in captured_attrs
        assert "memory.scope" in captured_attrs
        # Raw content and key must NOT appear as attribute values
        raw_vals = list(captured_attrs.values())
        assert "sensitive memory value" not in raw_vals, "Raw memory content leaked into span"
        assert "my-key" not in raw_vals, "Memory key leaked into span"


# ---------------------------------------------------------------------------
# Tests for start_mcp_tool_span() — GenAI semconv v1.35.0 (STORY-032.2)
# ---------------------------------------------------------------------------


class TestStartMcpToolSpan:
    """start_mcp_tool_span() emits SERVER spans with GenAI semconv v1.35.0 attrs."""

    # --- Constants ----------------------------------------------------------

    def test_mcp_method_tools_call_constant(self) -> None:
        from tapps_brain.otel_tracer import MCP_METHOD_TOOLS_CALL

        assert MCP_METHOD_TOOLS_CALL == "tools/call"

    def test_gen_ai_operation_execute_tool_constant(self) -> None:
        from tapps_brain.otel_tracer import GEN_AI_OPERATION_EXECUTE_TOOL

        assert GEN_AI_OPERATION_EXECUTE_TOOL == "execute_tool"

    def test_gen_ai_system_constant(self) -> None:
        from tapps_brain.otel_tracer import GEN_AI_SYSTEM

        assert GEN_AI_SYSTEM == "tapps-brain"

    # --- Span name ----------------------------------------------------------

    def test_span_name_is_method_space_tool(self) -> None:
        """Span name must be '{method} {tool_name}' per GenAI semconv v1.35.0."""
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, _ = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_remember"),
        ):
            pass

        args, _ = mock_tracer.start_as_current_span.call_args
        assert args[0] == "tools/call brain_remember"

    def test_custom_method_in_span_name(self) -> None:
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, _ = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("memory_save", method="tools/execute"),
        ):
            pass

        args, _ = mock_tracer.start_as_current_span.call_args
        assert args[0] == "tools/execute memory_save"

    # --- SpanKind -----------------------------------------------------------

    def test_span_kind_is_server(self) -> None:
        """MCP tool spans must use SpanKind.SERVER per semconv v1.35.0."""
        from opentelemetry.trace import SpanKind

        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, _ = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_recall"),
        ):
            pass

        _, kwargs = mock_tracer.start_as_current_span.call_args
        assert kwargs.get("kind") == SpanKind.SERVER

    # --- Required semconv v1.35.0 attributes --------------------------------

    def test_gen_ai_system_attribute(self) -> None:
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_remember"),
        ):
            pass

        set_calls = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
        assert set_calls.get("gen_ai.system") == "tapps-brain"

    def test_gen_ai_tool_name_attribute(self) -> None:
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_recall"),
        ):
            pass

        set_calls = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
        assert set_calls.get("gen_ai.tool.name") == "brain_recall"

    def test_mcp_method_name_attribute(self) -> None:
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_forget"),
        ):
            pass

        set_calls = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
        assert set_calls.get("mcp.method.name") == "tools/call"

    def test_gen_ai_operation_name_attribute(self) -> None:
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_learn_success"),
        ):
            pass

        set_calls = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
        assert set_calls.get("gen_ai.operation.name") == "execute_tool"

    def test_all_required_semconv_attributes_present(self) -> None:
        """All four required semconv v1.35.0 attributes must be set."""
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_learn_failure"),
        ):
            pass

        set_keys = {c.args[0] for c in mock_span.set_attribute.call_args_list}
        assert "gen_ai.system" in set_keys
        assert "gen_ai.tool.name" in set_keys
        assert "mcp.method.name" in set_keys
        assert "gen_ai.operation.name" in set_keys

    # --- extra_attributes ---------------------------------------------------

    def test_extra_attributes_merged(self) -> None:
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_remember", extra_attributes={"memory.tier": "pattern"}),
        ):
            pass

        set_calls = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
        assert set_calls.get("memory.tier") == "pattern"
        # Standard semconv attrs still present
        assert "gen_ai.system" in set_calls

    def test_none_extra_attributes_safe(self) -> None:
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_forget", extra_attributes=None),
        ):
            pass

        # Only 4 standard semconv attributes (no crash)
        set_keys = {c.args[0] for c in mock_span.set_attribute.call_args_list}
        assert len(set_keys) == 4

    # --- Error handling -----------------------------------------------------

    def test_exception_recorded_and_reraised(self) -> None:
        from opentelemetry.trace import StatusCode

        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()

        class _ToolError(RuntimeError):
            pass

        with patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer):
            try:
                with start_mcp_tool_span("brain_remember"):
                    raise _ToolError("tool failed")
            except _ToolError:
                pass
            else:
                raise AssertionError("Exception should have been re-raised")

        mock_span.record_exception.assert_called_once()
        exc_arg = mock_span.record_exception.call_args[0][0]
        assert isinstance(exc_arg, _ToolError)
        status_code = mock_span.set_status.call_args[0][0]
        assert status_code == StatusCode.ERROR

    def test_no_exception_recording_when_disabled(self) -> None:
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()

        with patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer):
            try:
                with start_mcp_tool_span("brain_recall", record_exception=False):
                    raise ValueError("suppress me")
            except ValueError:
                pass

        mock_span.record_exception.assert_not_called()

    def test_yields_span_to_caller(self) -> None:
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_forget") as span,
        ):
            assert span is mock_span

    def test_ok_status_set_on_success(self) -> None:
        from opentelemetry.trace import StatusCode

        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_learn_success"),
        ):
            pass

        mock_span.set_status.assert_called_once()
        assert mock_span.set_status.call_args[0][0] == StatusCode.OK

    # --- Privacy: no raw content in attributes ------------------------------

    def test_no_raw_content_in_semconv_attrs(self) -> None:
        """Standard semconv attrs must never contain user-supplied content."""
        from tapps_brain.otel_tracer import start_mcp_tool_span

        mock_tracer, mock_span = _make_mock_tracer()
        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_mcp_tool_span("brain_remember"),
        ):
            pass

        attr_values = {c.args[1] for c in mock_span.set_attribute.call_args_list}
        # Only fixed enum-like values are allowed — no user-controlled strings
        expected_values = {"tapps-brain", "brain_remember", "tools/call", "execute_tool"}
        assert attr_values == expected_values


# ---------------------------------------------------------------------------
# Tests for extract_trace_context_from_mcp_params() — STORY-032.3
# ---------------------------------------------------------------------------


class TestExtractTraceContextFromMcpParams:
    """extract_trace_context_from_mcp_params() extracts W3C context from _meta."""

    def test_returns_none_when_params_none(self) -> None:
        from tapps_brain.otel_tracer import extract_trace_context_from_mcp_params

        result = extract_trace_context_from_mcp_params(None)
        assert result is None

    def test_returns_none_when_params_empty(self) -> None:
        from tapps_brain.otel_tracer import extract_trace_context_from_mcp_params

        result = extract_trace_context_from_mcp_params({})
        assert result is None

    def test_returns_none_when_no_meta_key(self) -> None:
        from tapps_brain.otel_tracer import extract_trace_context_from_mcp_params

        result = extract_trace_context_from_mcp_params({"query": "test"})
        assert result is None

    def test_returns_none_when_meta_not_dict(self) -> None:
        from tapps_brain.otel_tracer import extract_trace_context_from_mcp_params

        result = extract_trace_context_from_mcp_params({"_meta": "not-a-dict"})
        assert result is None

    def test_returns_none_when_meta_missing_traceparent(self) -> None:
        from tapps_brain.otel_tracer import extract_trace_context_from_mcp_params

        result = extract_trace_context_from_mcp_params({"_meta": {"other": "value"}})
        assert result is None

    def test_returns_none_when_traceparent_empty_string(self) -> None:
        from tapps_brain.otel_tracer import extract_trace_context_from_mcp_params

        result = extract_trace_context_from_mcp_params({"_meta": {"traceparent": ""}})
        assert result is None

    def test_calls_extract_trace_context_with_traceparent(self) -> None:
        from tapps_brain.otel_tracer import extract_trace_context_from_mcp_params

        tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        mock_ctx = MagicMock()
        with patch(
            "tapps_brain.otel_tracer.extract_trace_context", return_value=mock_ctx
        ) as mock_extract:
            result = extract_trace_context_from_mcp_params({"_meta": {"traceparent": tp}})

        assert result is mock_ctx
        mock_extract.assert_called_once_with({"traceparent": tp})

    def test_includes_tracestate_when_present(self) -> None:
        from tapps_brain.otel_tracer import extract_trace_context_from_mcp_params

        tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        ts = "rojo=00f067aa0ba902b7"
        mock_ctx = MagicMock()
        with patch(
            "tapps_brain.otel_tracer.extract_trace_context", return_value=mock_ctx
        ) as mock_extract:
            extract_trace_context_from_mcp_params(
                {"_meta": {"traceparent": tp, "tracestate": ts}}
            )

        mock_extract.assert_called_once_with({"traceparent": tp, "tracestate": ts})

    def test_omits_tracestate_when_empty_string(self) -> None:
        from tapps_brain.otel_tracer import extract_trace_context_from_mcp_params

        tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        mock_ctx = MagicMock()
        with patch(
            "tapps_brain.otel_tracer.extract_trace_context", return_value=mock_ctx
        ) as mock_extract:
            extract_trace_context_from_mcp_params(
                {"_meta": {"traceparent": tp, "tracestate": ""}}
            )

        # tracestate must NOT be in carrier when empty
        call_carrier = mock_extract.call_args[0][0]
        assert "tracestate" not in call_carrier

    def test_ignores_non_string_tracestate(self) -> None:
        from tapps_brain.otel_tracer import extract_trace_context_from_mcp_params

        tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        with patch(
            "tapps_brain.otel_tracer.extract_trace_context", return_value=MagicMock()
        ) as mock_extract:
            extract_trace_context_from_mcp_params(
                {"_meta": {"traceparent": tp, "tracestate": 12345}}
            )

        call_carrier = mock_extract.call_args[0][0]
        assert "tracestate" not in call_carrier

    def test_mcp_meta_key_constant(self) -> None:
        from tapps_brain.otel_tracer import MCP_META_KEY

        assert MCP_META_KEY == "_meta"

    def test_w3c_traceparent_key_constant(self) -> None:
        from tapps_brain.otel_tracer import W3C_TRACEPARENT_KEY

        assert W3C_TRACEPARENT_KEY == "traceparent"


# ---------------------------------------------------------------------------
# Tests for record_retrieval_document_events() — STORY-032.3
# ---------------------------------------------------------------------------


class TestRecordRetrievalDocumentEvents:
    """record_retrieval_document_events() adds safe OTel events per recall result."""

    def test_noop_when_span_is_none(self) -> None:
        from tapps_brain.otel_tracer import record_retrieval_document_events

        # Should not raise even when span is None
        record_retrieval_document_events(None, [{"key": "k", "score": 0.9, "tier": "pattern"}])

    def test_noop_when_memories_empty(self) -> None:
        from tapps_brain.otel_tracer import record_retrieval_document_events

        mock_span = MagicMock()
        record_retrieval_document_events(mock_span, [])
        mock_span.add_event.assert_not_called()

    def test_adds_one_event_per_memory(self) -> None:
        from tapps_brain.otel_tracer import record_retrieval_document_events

        mock_span = MagicMock()
        memories = [
            {"key": "key1", "score": 0.9, "tier": "pattern"},
            {"key": "key2", "score": 0.7, "tier": "context"},
        ]
        record_retrieval_document_events(mock_span, memories)
        assert mock_span.add_event.call_count == 2

    def test_event_name_is_gen_ai_retrieval_document(self) -> None:
        from tapps_brain.otel_tracer import EVENT_RETRIEVAL_DOCUMENT, record_retrieval_document_events

        mock_span = MagicMock()
        record_retrieval_document_events(mock_span, [{"key": "k", "score": 0.5, "tier": "context"}])
        event_name = mock_span.add_event.call_args[0][0]
        assert event_name == EVENT_RETRIEVAL_DOCUMENT

    def test_doc_id_is_hashed_not_raw_key(self) -> None:
        """The raw entry key must never appear as the doc id — must be SHA-256 hashed."""
        from tapps_brain.otel_tracer import ATTR_RETRIEVAL_DOC_ID, record_retrieval_document_events

        mock_span = MagicMock()
        raw_key = "secret-entry-key-abc123"
        record_retrieval_document_events(mock_span, [{"key": raw_key, "score": 0.5, "tier": "context"}])

        event_attrs = mock_span.add_event.call_args[0][1]
        doc_id = event_attrs.get(ATTR_RETRIEVAL_DOC_ID, "")
        # Must NOT be the raw key
        assert doc_id != raw_key
        # Must be hex string of _DOC_ID_HASH_LEN chars
        assert len(doc_id) == 16
        assert all(c in "0123456789abcdef" for c in doc_id)

    def test_doc_id_is_stable_for_same_key(self) -> None:
        """Same key must produce the same doc_id across calls."""
        from tapps_brain.otel_tracer import ATTR_RETRIEVAL_DOC_ID, record_retrieval_document_events

        mock_span1, mock_span2 = MagicMock(), MagicMock()
        mem = {"key": "stable-key", "score": 0.5, "tier": "context"}
        record_retrieval_document_events(mock_span1, [mem])
        record_retrieval_document_events(mock_span2, [mem])
        id1 = mock_span1.add_event.call_args[0][1].get(ATTR_RETRIEVAL_DOC_ID)
        id2 = mock_span2.add_event.call_args[0][1].get(ATTR_RETRIEVAL_DOC_ID)
        assert id1 == id2

    def test_score_attribute_is_float(self) -> None:
        from tapps_brain.otel_tracer import ATTR_RETRIEVAL_DOC_SCORE, record_retrieval_document_events

        mock_span = MagicMock()
        record_retrieval_document_events(mock_span, [{"key": "k", "score": 0.85, "tier": "pattern"}])
        attrs = mock_span.add_event.call_args[0][1]
        assert isinstance(attrs.get(ATTR_RETRIEVAL_DOC_SCORE), float)
        assert abs(attrs[ATTR_RETRIEVAL_DOC_SCORE] - 0.85) < 1e-9

    def test_tier_attribute_is_present(self) -> None:
        from tapps_brain.otel_tracer import ATTR_RETRIEVAL_DOC_TIER, record_retrieval_document_events

        mock_span = MagicMock()
        record_retrieval_document_events(mock_span, [{"key": "k", "score": 0.5, "tier": "architectural"}])
        attrs = mock_span.add_event.call_args[0][1]
        assert attrs.get(ATTR_RETRIEVAL_DOC_TIER) == "architectural"

    def test_missing_key_produces_no_doc_id(self) -> None:
        from tapps_brain.otel_tracer import ATTR_RETRIEVAL_DOC_ID, record_retrieval_document_events

        mock_span = MagicMock()
        record_retrieval_document_events(mock_span, [{"score": 0.5, "tier": "context"}])
        attrs = mock_span.add_event.call_args[0][1]
        assert ATTR_RETRIEVAL_DOC_ID not in attrs

    def test_missing_score_defaults_to_float(self) -> None:
        from tapps_brain.otel_tracer import ATTR_RETRIEVAL_DOC_SCORE, record_retrieval_document_events

        mock_span = MagicMock()
        record_retrieval_document_events(mock_span, [{"key": "k", "tier": "context"}])
        attrs = mock_span.add_event.call_args[0][1]
        assert isinstance(attrs.get(ATTR_RETRIEVAL_DOC_SCORE), float)

    def test_non_dict_memory_entries_skipped(self) -> None:
        from tapps_brain.otel_tracer import record_retrieval_document_events

        mock_span = MagicMock()
        # Mix of valid dicts and invalid types
        memories: list[Any] = [
            "not-a-dict",
            {"key": "k", "score": 0.5, "tier": "context"},
            None,
        ]
        record_retrieval_document_events(mock_span, memories)
        # Only the dict entry should produce an event
        assert mock_span.add_event.call_count == 1

    def test_noop_when_span_has_no_add_event(self) -> None:
        from tapps_brain.otel_tracer import record_retrieval_document_events

        # Object with no add_event method — should not raise
        class _FakeSpan:
            pass

        record_retrieval_document_events(_FakeSpan(), [{"key": "k", "score": 0.5, "tier": "context"}])

    def test_event_constants_have_correct_values(self) -> None:
        from tapps_brain.otel_tracer import (
            ATTR_RETRIEVAL_DOC_ID,
            ATTR_RETRIEVAL_DOC_SCORE,
            ATTR_RETRIEVAL_DOC_TIER,
            EVENT_RETRIEVAL_DOCUMENT,
        )

        assert EVENT_RETRIEVAL_DOCUMENT == "gen_ai.retrieval.document"
        assert ATTR_RETRIEVAL_DOC_ID == "gen_ai.retrieval.document.id"
        assert ATTR_RETRIEVAL_DOC_SCORE == "gen_ai.retrieval.document.relevance_score"
        assert ATTR_RETRIEVAL_DOC_TIER == "gen_ai.retrieval.document.tier"

    def test_sdk_error_silently_suppressed(self) -> None:
        """If span.add_event raises, the error must not propagate."""
        from tapps_brain.otel_tracer import record_retrieval_document_events

        mock_span = MagicMock()
        mock_span.add_event.side_effect = RuntimeError("SDK crash")

        # Must not raise
        record_retrieval_document_events(mock_span, [{"key": "k", "score": 0.5, "tier": "context"}])


# ---------------------------------------------------------------------------
# Tests for store.py emitting retrieval events on recall — STORY-032.3
# ---------------------------------------------------------------------------


class TestStoreRetrievalDocumentEvents:
    """store.recall() wires retrieval document events onto the recall span."""

    def _make_store(self, tmp_path: Any) -> Any:
        from tapps_brain.store import MemoryStore

        return MemoryStore(tmp_path, embedding_provider=None)

    def test_recall_calls_record_retrieval_document_events(self, tmp_path: Any) -> None:
        """record_retrieval_document_events must be called during store.recall()."""
        from tapps_brain.otel_tracer import record_retrieval_document_events

        store = self._make_store(tmp_path)
        store.save("recall-test-key", "test value for recall", tier="pattern")

        with patch(
            "tapps_brain.store.record_retrieval_document_events",
            wraps=record_retrieval_document_events,
        ) as mock_rde:
            store.recall("test value")

        assert mock_rde.call_count >= 1


# ---------------------------------------------------------------------------
# Tests for non-retrieval spans — STORY-032.4
# ---------------------------------------------------------------------------


class TestNonRetrievalSpanConstants:
    """Span name constants for delete, reinforce, and update ops (STORY-032.4)."""

    def test_span_delete_constant(self) -> None:
        from tapps_brain.otel_tracer import SPAN_DELETE

        assert SPAN_DELETE == "tapps_brain.delete"
        assert SPAN_DELETE.startswith("tapps_brain.")

    def test_span_reinforce_constant(self) -> None:
        from tapps_brain.otel_tracer import SPAN_REINFORCE

        assert SPAN_REINFORCE == "tapps_brain.reinforce"
        assert SPAN_REINFORCE.startswith("tapps_brain.")

    def test_span_update_constant(self) -> None:
        from tapps_brain.otel_tracer import SPAN_UPDATE

        assert SPAN_UPDATE == "tapps_brain.update"
        assert SPAN_UPDATE.startswith("tapps_brain.")

    def test_all_non_retrieval_spans_have_tapps_brain_prefix(self) -> None:
        from tapps_brain.otel_tracer import SPAN_DELETE, SPAN_REINFORCE, SPAN_UPDATE

        for name in (SPAN_DELETE, SPAN_REINFORCE, SPAN_UPDATE):
            assert name.startswith("tapps_brain."), f"{name!r} must start with 'tapps_brain.'"


class TestNonRetrievalSpanStoreIntegration:
    """store.delete() and store.reinforce() emit spans with gen_ai.operation.name."""

    def _make_store(self, tmp_path: Any) -> Any:
        from tapps_brain.store import MemoryStore

        return MemoryStore(tmp_path, embedding_provider=None)

    def test_delete_emits_delete_span(self, tmp_path: Any) -> None:
        from tapps_brain.otel_tracer import SPAN_DELETE

        store = self._make_store(tmp_path)
        store.save("to-delete", "value", tier="context")

        with patch(
            "tapps_brain.store.start_span",
            wraps=__import__("tapps_brain.otel_tracer", fromlist=["start_span"]).start_span,
        ) as mock_start_span:
            store.delete("to-delete")

        called_names = [c.args[0] for c in mock_start_span.call_args_list]
        assert SPAN_DELETE in called_names

    def test_delete_span_has_gen_ai_operation_name(self, tmp_path: Any) -> None:
        from tapps_brain.otel_tracer import GEN_AI_OPERATION_EXECUTE_TOOL, SPAN_DELETE

        store = self._make_store(tmp_path)
        store.save("key-for-delete", "some value", tier="context")

        captured_attrs: dict[str, Any] = {}

        @contextmanager
        def _capturing_start_span(name: str, attributes: dict | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
            if name == SPAN_DELETE and attributes:
                captured_attrs.update(attributes)
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            yield mock_span

        with patch("tapps_brain.store.start_span", _capturing_start_span):
            store.delete("key-for-delete")

        assert captured_attrs.get("gen_ai.operation.name") == GEN_AI_OPERATION_EXECUTE_TOOL

    def test_reinforce_emits_reinforce_span(self, tmp_path: Any) -> None:
        from tapps_brain.otel_tracer import SPAN_REINFORCE

        store = self._make_store(tmp_path)
        store.save("to-reinforce", "value", tier="pattern")

        with patch(
            "tapps_brain.store.start_span",
            wraps=__import__("tapps_brain.otel_tracer", fromlist=["start_span"]).start_span,
        ) as mock_start_span:
            store.reinforce("to-reinforce")

        called_names = [c.args[0] for c in mock_start_span.call_args_list]
        assert SPAN_REINFORCE in called_names

    def test_reinforce_span_has_gen_ai_operation_name(self, tmp_path: Any) -> None:
        from tapps_brain.otel_tracer import GEN_AI_OPERATION_EXECUTE_TOOL, SPAN_REINFORCE

        store = self._make_store(tmp_path)
        store.save("key-for-reinforce", "some value", tier="pattern")

        captured_attrs: dict[str, Any] = {}

        @contextmanager
        def _capturing_start_span(name: str, attributes: dict | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
            if name == SPAN_REINFORCE and attributes:
                captured_attrs.update(attributes)
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            yield mock_span

        with patch("tapps_brain.store.start_span", _capturing_start_span):
            store.reinforce("key-for-reinforce")

        assert captured_attrs.get("gen_ai.operation.name") == GEN_AI_OPERATION_EXECUTE_TOOL

    def test_save_span_has_gen_ai_operation_name(self, tmp_path: Any) -> None:
        """remember (save) span must carry gen_ai.operation.name = 'execute_tool'."""
        from tapps_brain.otel_tracer import GEN_AI_OPERATION_EXECUTE_TOOL, SPAN_REMEMBER

        store = self._make_store(tmp_path)

        captured_attrs: dict[str, Any] = {}

        @contextmanager
        def _capturing_start_span(name: str, attributes: dict | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
            if name == SPAN_REMEMBER and attributes:
                captured_attrs.update(attributes)
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            yield mock_span

        with patch("tapps_brain.store.start_span", _capturing_start_span):
            store.save("save-key", "save value", tier="pattern")

        assert captured_attrs.get("gen_ai.operation.name") == GEN_AI_OPERATION_EXECUTE_TOOL

    def test_delete_span_does_not_include_raw_key(self, tmp_path: Any) -> None:
        """delete() span attributes must not contain the raw entry key."""
        from tapps_brain.otel_tracer import SPAN_DELETE

        store = self._make_store(tmp_path)
        raw_key = "secret-entry-key-to-delete"
        store.save(raw_key, "some value", tier="context")

        captured_attrs: dict[str, Any] = {}

        @contextmanager
        def _capturing_start_span(name: str, attributes: dict | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
            if name == SPAN_DELETE and attributes:
                captured_attrs.update(attributes)
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            yield mock_span

        with patch("tapps_brain.store.start_span", _capturing_start_span):
            store.delete(raw_key)

        raw_vals = list(captured_attrs.values())
        assert raw_key not in raw_vals, "Raw entry key must not appear in delete span attributes"

    def test_reinforce_span_does_not_include_raw_key(self, tmp_path: Any) -> None:
        """reinforce() span attributes must not contain the raw entry key."""
        from tapps_brain.otel_tracer import SPAN_REINFORCE

        store = self._make_store(tmp_path)
        raw_key = "secret-reinforce-key"
        store.save(raw_key, "some value", tier="pattern")

        captured_attrs: dict[str, Any] = {}

        @contextmanager
        def _capturing_start_span(name: str, attributes: dict | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
            if name == SPAN_REINFORCE and attributes:
                captured_attrs.update(attributes)
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            yield mock_span

        with patch("tapps_brain.store.start_span", _capturing_start_span):
            store.reinforce(raw_key)

        raw_vals = list(captured_attrs.values())
        assert raw_key not in raw_vals, "Raw entry key must not appear in reinforce span attributes"
