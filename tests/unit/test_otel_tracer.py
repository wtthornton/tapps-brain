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
