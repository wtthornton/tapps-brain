"""OpenTelemetry tracing for tapps-brain hot paths (STORY-061.1).

Provides lightweight span instrumentation for remember, recall, search,
and hive operations.  The ``opentelemetry-api`` package (core dep) is
always available as a no-op when no SDK is configured; actual export
requires ``pip install tapps-brain[otel]``.

Span names are aligned with ``docs/engineering/system-architecture.md``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

try:
    from opentelemetry import propagate as _otel_propagate
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, StatusCode

    _HAS_OTEL_API = True
    #: Export ``SpanKind.SERVER`` for HTTP adapters (STORY-061.3).
    SPAN_KIND_SERVER: Any = SpanKind.SERVER
except ImportError:  # pragma: no cover
    _HAS_OTEL_API = False
    SPAN_KIND_SERVER = None

if TYPE_CHECKING:
    from collections.abc import Iterator

# ---------------------------------------------------------------------------
# Canonical span names — aligned with system-architecture.md
# ---------------------------------------------------------------------------

#: Span name for the ``remember`` (save) operation.
SPAN_REMEMBER: str = "tapps_brain.remember"

#: Span name for the ``recall`` (search + inject) operation.
SPAN_RECALL: str = "tapps_brain.recall"

#: Span name for the low-level ``search`` operation.
SPAN_SEARCH: str = "tapps_brain.search"

#: Span name for Hive propagation on save.
SPAN_HIVE_PROPAGATE: str = "tapps_brain.hive.propagate"

#: Span name for Hive search during group-aware recall.
SPAN_HIVE_SEARCH: str = "tapps_brain.hive.search"

# ---------------------------------------------------------------------------
# MCP tool call spans — GenAI semantic conventions v1.35.0 (STORY-032.2)
# ---------------------------------------------------------------------------

#: MCP RPC method name for agent-to-server tool invocations (semconv v1.35.0).
MCP_METHOD_TOOLS_CALL: str = "tools/call"

#: ``gen_ai.operation.name`` value for MCP tool execution (semconv v1.35.0).
GEN_AI_OPERATION_EXECUTE_TOOL: str = "execute_tool"

#: ``gen_ai.system`` value identifying tapps-brain (semconv v1.35.0).
GEN_AI_SYSTEM: str = "tapps-brain"

# ---------------------------------------------------------------------------
# Instrumentation identity
# ---------------------------------------------------------------------------

_INSTRUMENTATION_NAME: str = "tapps_brain"


def _service_name() -> str:
    """Return ``service.name`` from env, defaulting to ``"tapps-brain"``."""
    return os.environ.get("OTEL_SERVICE_NAME", "tapps-brain")


def _service_version() -> str:
    """Return ``service.version`` from env, defaulting to ``""``."""
    return os.environ.get("OTEL_SERVICE_VERSION", "")


def get_tracer() -> Any:  # noqa: ANN401
    """Return the OTel Tracer for tapps-brain.

    When ``opentelemetry-api`` is not installed, returns ``None``.  When
    installed without the SDK, returns a no-op tracer (zero allocation on
    the hot path).

    The ``service.name`` / ``service.version`` resource attributes are set
    by the OTel SDK when configured via ``OTEL_SERVICE_NAME`` /
    ``OTEL_SERVICE_VERSION`` environment variables.
    """
    if not _HAS_OTEL_API:  # pragma: no cover
        return None
    return trace.get_tracer(_INSTRUMENTATION_NAME)


@contextmanager
def start_span(
    name: str,
    attributes: dict[str, str | int | float | bool] | None = None,
    *,
    record_exception: bool = True,
    kind: Any = None,  # noqa: ANN401 — SpanKind or None; defaults to INTERNAL
    context: Any = None,  # noqa: ANN401 — OTel Context for parent propagation
) -> Iterator[Any]:
    """Context manager that wraps an operation in an OTel span.

    No-op when ``opentelemetry-api`` is not available.  Exceptions are
    recorded on the span and re-raised; span status is set to ``ERROR`` on
    exception and ``OK`` on success.

    .. warning::
        **Never** pass raw memory content, entry keys, query strings, or
        user PII as attribute values.  See the telemetry policy doc.

    Args:
        name: Span name — use module-level ``SPAN_*`` constants.
        attributes: Safe span attributes (tier, scope, result counts, etc.).
            Must **not** contain memory content, entry keys, or query text.
        record_exception: When ``True``, caught exceptions are recorded on
            the span before being re-raised.
        kind: OTel ``SpanKind``.  Defaults to ``SpanKind.INTERNAL``.  Pass
            ``SpanKind.SERVER`` for HTTP request handlers.
        context: Optional OTel ``Context`` to use as the parent.  When
            supplied, the span is created as a child of the context — use
            this for W3C ``traceparent`` propagation (STORY-061.3).

    Yields:
        The OTel :class:`opentelemetry.trace.Span` instance when OTel is
        available, or ``None`` when it is not.
    """
    if not _HAS_OTEL_API:  # pragma: no cover
        yield None
        return

    tracer = get_tracer()
    if tracer is None:  # pragma: no cover
        yield None
        return

    _kind = kind if kind is not None else SpanKind.INTERNAL
    _span_kwargs: dict[str, Any] = {"kind": _kind}
    if context is not None:
        _span_kwargs["context"] = context

    with tracer.start_as_current_span(name, **_span_kwargs) as span:
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        try:
            yield span
            span.set_status(StatusCode.OK)
        except Exception as exc:
            if record_exception:
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR, str(exc))
            raise


@contextmanager
def start_mcp_tool_span(
    tool_name: str,
    method: str = MCP_METHOD_TOOLS_CALL,
    *,
    extra_attributes: dict[str, str | int | float | bool] | None = None,
    record_exception: bool = True,
) -> Iterator[Any]:
    """Context manager for GenAI semconv v1.35.0 MCP tool call spans.

    Span name: ``"{method} {tool_name}"`` (e.g. ``"tools/call brain_remember"``).
    SpanKind: ``SERVER`` — the MCP server receives the call from an LLM client.

    Semconv v1.35.0 attributes set on every span:

    .. code-block:: text

        gen_ai.system         = "tapps-brain"
        gen_ai.tool.name      = <tool_name>
        mcp.method.name       = <method>          (default "tools/call")
        gen_ai.operation.name = "execute_tool"

    .. warning::
        **Never** pass raw memory content, entry keys, query strings, or user
        PII as attribute values — including in *extra_attributes*.  See the
        telemetry policy doc (STORY-061.6).

    Args:
        tool_name: MCP tool name (e.g. ``"brain_remember"``).
        method: MCP RPC method name.  Defaults to ``"tools/call"`` per semconv.
        extra_attributes: Additional safe span attributes (beyond the standard
            semconv set above).  Must not contain PII or raw content.
        record_exception: When ``True``, caught exceptions are recorded on the
            span before being re-raised.

    Yields:
        The OTel :class:`opentelemetry.trace.Span` instance, or ``None`` when
        OTel is not available.
    """
    attributes: dict[str, str | int | float | bool] = {
        "gen_ai.system": GEN_AI_SYSTEM,
        "gen_ai.tool.name": tool_name,
        "mcp.method.name": method,
        "gen_ai.operation.name": GEN_AI_OPERATION_EXECUTE_TOOL,
    }
    if extra_attributes:
        attributes.update(extra_attributes)
    span_name = f"{method} {tool_name}"
    with start_span(
        span_name,
        attributes=attributes,
        kind=SPAN_KIND_SERVER,
        record_exception=record_exception,
    ) as span:
        yield span


def extract_trace_context(carrier: dict[str, str]) -> Any:  # noqa: ANN401
    """Extract an OTel ``Context`` from a W3C ``traceparent``/``tracestate`` carrier.

    Typically called with ``{"traceparent": request.headers.get("traceparent", "")}``
    before starting a SERVER span so that the new span is a child of the caller's
    trace (STORY-061.3).

    Returns ``None`` when ``opentelemetry-api`` is not available; returns an OTel
    ``Context`` (possibly empty / no-op) otherwise.  Never raises.

    .. note::
        **OTel SDK pattern note (STORY-061.3):** The Python OTel SDK uses a
        global ``TextMapPropagator`` registered at startup (typically W3C
        TraceContext + Baggage).  When only the API is installed (no SDK),
        ``propagate.extract()`` returns an empty context and spans are no-ops.
        Configure the SDK and propagators via env vars
        (``OTEL_PROPAGATORS=tracecontext``) for production use.  See
        https://opentelemetry-python.readthedocs.io/en/latest/api/propagate.html
    """
    if not _HAS_OTEL_API:  # pragma: no cover
        return None
    try:
        return _otel_propagate.extract(carrier)
    except Exception:
        return None
