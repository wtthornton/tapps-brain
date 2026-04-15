"""OpenTelemetry tracing for tapps-brain hot paths (STORY-061.1).

Provides lightweight span instrumentation for remember, recall, search,
and hive operations.  The ``opentelemetry-api`` package (core dep) is
always available as a no-op when no SDK is configured; actual export
requires ``pip install tapps-brain[otel]``.

Span names are aligned with ``docs/engineering/system-architecture.md``.

In-process retrieval counters (STORY-065.7) accumulate since process start
and are readable via :func:`get_retrieval_meter_snapshot`.  These are plain
Python ints/floats — not OTel exportable — so they remain available with
OTel API-only installs (no SDK required).  All values reset on restart.

STORY-070.12 adds per-request label enrichment:

- :func:`start_span` auto-injects ``tapps.project_id``, ``tapps.agent_id``,
  ``tapps.scope``, and ``tapps.tool`` from MCP request contextvars onto every
  memory-op span — no caller changes required.
- :func:`start_mcp_tool_span` additionally records success/error counts in
  the :func:`get_tool_call_counts_snapshot` registry for Prometheus export.
- :data:`ATTR_ROWS_RETURNED` / :data:`ATTR_LATENCY_MS` constants let callers
  set post-operation attributes without hardcoding strings.

Key public API: :func:`get_tracer`, :func:`start_mcp_tool_span`,
:func:`extract_trace_context_from_mcp_params`,
:func:`record_retrieval_document_events`, :func:`record_diagnostics_event`,
:func:`get_tool_call_counts_snapshot`.
"""

from __future__ import annotations

import contextlib
import os
import threading
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

#: Span name for the ``delete`` (forget) operation.
SPAN_DELETE: str = "tapps_brain.delete"

#: Span name for the ``reinforce`` operation.
SPAN_REINFORCE: str = "tapps_brain.reinforce"

#: Span name for the ``update_fields`` (update) operation.
SPAN_UPDATE: str = "tapps_brain.update"

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
# STORY-070.12: per-request span attribute names (bounded cardinality)
# ---------------------------------------------------------------------------

#: Span attribute: project identifier (tenant).  Low-cardinality — bounded by
#: registered projects.  Never set to raw user input.
ATTR_PROJECT_ID: str = "tapps.project_id"

#: Span attribute: agent identifier within the project.  Cardinality is capped
#: to 100 distinct values per project in the Prometheus export layer; overflow
#: is mapped to ``"other"``.
ATTR_AGENT_ID: str = "tapps.agent_id"

#: Span attribute: memory scope (``"project"`` | ``"branch"`` | ``"session"``).
ATTR_SCOPE: str = "tapps.scope"

#: Span attribute: memory operation name derived from span name
#: (``"remember"`` | ``"recall"`` | ``"search"`` | ``"hive_propagate"`` | …).
ATTR_TOOL: str = "tapps.tool"

#: Span attribute: number of memory entries returned by the operation.
ATTR_ROWS_RETURNED: str = "tapps.rows_returned"

#: Span attribute: wall-clock latency of the memory operation in milliseconds.
ATTR_LATENCY_MS: str = "tapps.latency_ms"

# ---------------------------------------------------------------------------
# Instrumentation identity
# ---------------------------------------------------------------------------

_INSTRUMENTATION_NAME: str = "tapps_brain"

# ---------------------------------------------------------------------------
# STORY-070.12: span-name → tapps.tool mapping
# ---------------------------------------------------------------------------

#: Maps canonical span names to safe ``tapps.tool`` label values.
_SPAN_NAME_TO_TOOL: dict[str, str] = {
    # Values defined at module scope so they reference the constants correctly
    # even though the dict is built before the constants are referenced below.
    "tapps_brain.remember": "remember",
    "tapps_brain.recall": "recall",
    "tapps_brain.search": "search",
    "tapps_brain.hive.propagate": "hive_propagate",
    "tapps_brain.hive.search": "hive_search",
    "tapps_brain.delete": "delete",
    "tapps_brain.reinforce": "reinforce",
    "tapps_brain.update": "update",
}

# ---------------------------------------------------------------------------
# STORY-070.12: per-tool call counter (for Prometheus export)
# ---------------------------------------------------------------------------

_TOOL_CALL_COUNTS: dict[tuple[str, str, str, str], int] = {}
_TOOL_CALL_LOCK: threading.Lock = threading.Lock()
_MAX_TOOL_AGENT_IDS: int = 100  # mirror of http_adapter._MAX_AGENT_ID_CARDINALITY


def _record_tool_call_metric(
    project_id: str,
    agent_id: str,
    tool: str,
    status: str,
) -> None:
    """Increment the per-(project_id, agent_id, tool, status) tool call counter.

    Agent-id cardinality is capped at :data:`_MAX_TOOL_AGENT_IDS` distinct values
    per project; excess values are collapsed to ``"other"``.  This keeps Prometheus
    cardinality bounded without losing aggregate signal.

    Args:
        project_id: Tenant project identifier.
        agent_id:   Agent identifier (bounded cardinality enforced internally).
        tool:       MCP tool name (``"brain_remember"``, ``"brain_recall"``, …).
        status:     ``"success"`` or ``"error"``.
    """
    with _TOOL_CALL_LOCK:
        distinct_agents = {k[1] for k in _TOOL_CALL_COUNTS if k[0] == project_id}
        if agent_id not in distinct_agents and len(distinct_agents) >= _MAX_TOOL_AGENT_IDS:
            agent_id = "other"
        key = (project_id, agent_id, tool, status)
        _TOOL_CALL_COUNTS[key] = _TOOL_CALL_COUNTS.get(key, 0) + 1


def get_tool_call_counts_snapshot() -> dict[tuple[str, str, str, str], int]:
    """Return a thread-safe snapshot of per-(project_id, agent_id, tool, status) counts.

    Keys are ``(project_id, agent_id, tool, status)`` 4-tuples.  Values are
    cumulative integer counts since process start.  Intended for Prometheus
    export from :mod:`tapps_brain.http_adapter`.

    Returns:
        Shallow copy of the internal counter dict; never raises.
    """
    with _TOOL_CALL_LOCK:
        return dict(_TOOL_CALL_COUNTS)


# ---------------------------------------------------------------------------
# STORY-070.12: context-attrs helper (reads mcp_server contextvars)
# ---------------------------------------------------------------------------


def _get_context_attrs(span_name: str | None = None) -> dict[str, str | int | float | bool]:
    """Build a safe attribute dict from the active MCP request contextvars.

    Reads ``REQUEST_PROJECT_ID``, ``REQUEST_AGENT_ID``, and ``REQUEST_SCOPE``
    from :mod:`tapps_brain.mcp_server` (imported lazily to avoid circular deps).
    When *span_name* is provided, also derives ``tapps.tool`` via
    :data:`_SPAN_NAME_TO_TOOL`.

    Returns an empty dict when no contextvars are set (e.g., unit tests that
    do not configure an MCP server context).  Never raises.

    Args:
        span_name: Canonical span name (e.g. ``"tapps_brain.recall"``).  When
            supplied, ``tapps.tool`` is added if the name is in the mapping.

    Returns:
        Dict of safe span attribute key/value pairs (never contains PII).
    """
    attrs: dict[str, str | int | float | bool] = {}
    try:
        from tapps_brain.mcp_server import (  # lazy — avoid circular import
            REQUEST_AGENT_ID,
            REQUEST_PROJECT_ID,
            REQUEST_SCOPE,
        )

        pid = REQUEST_PROJECT_ID.get()
        aid = REQUEST_AGENT_ID.get()
        scope = REQUEST_SCOPE.get()
        if pid:
            attrs[ATTR_PROJECT_ID] = str(pid)
        if aid:
            attrs[ATTR_AGENT_ID] = str(aid)
        if scope:
            attrs[ATTR_SCOPE] = str(scope)
    except Exception:  # mcp_server unavailable in some test contexts
        pass

    if span_name is not None:
        tool = _SPAN_NAME_TO_TOOL.get(span_name)
        if tool:
            attrs[ATTR_TOOL] = tool

    return attrs


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

    # STORY-070.12: merge context attrs (project_id, agent_id, scope, tool)
    # from mcp_server contextvars.  Caller-supplied attrs take precedence.
    ctx_attrs = _get_context_attrs(span_name=name)
    if ctx_attrs:
        merged: dict[str, str | int | float | bool] = {**ctx_attrs}
        if attributes:
            merged.update(attributes)
        attributes = merged

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
    project_id: str | None = None,
    agent_id: str | None = None,
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

    STORY-070.12 attributes (when available, bounded cardinality):

    .. code-block:: text

        tapps.project_id = <project_id>   (from contextvar or explicit param)
        tapps.agent_id   = <agent_id>     (from contextvar or explicit param;
                                           capped to first 100 distinct values
                                           at the Prometheus layer)

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
        project_id: Optional project_id override.  When ``None``, resolved from
            the ``tapps_brain_request_project_id`` contextvar (STORY-070.12).
        agent_id: Optional agent_id override.  When ``None``, resolved from the
            ``tapps_brain_request_agent_id`` contextvar (STORY-070.12).

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
    # STORY-070.12: inject project_id / agent_id from contextvars when not
    # supplied explicitly so both HTTP and MCP paths emit consistent labels.
    _pid = project_id
    _aid = agent_id
    if _pid is None or _aid is None:
        try:
            from tapps_brain.mcp_server import REQUEST_AGENT_ID, REQUEST_PROJECT_ID

            if _pid is None:
                _pid = REQUEST_PROJECT_ID.get()
            if _aid is None:
                _aid = REQUEST_AGENT_ID.get()
        except Exception:  # noqa: BLE001
            pass
    if _pid:
        attributes[ATTR_PROJECT_ID] = str(_pid)
    if _aid:
        attributes[ATTR_AGENT_ID] = str(_aid)
    if extra_attributes:
        attributes.update(extra_attributes)
    span_name = f"{method} {tool_name}"
    # STORY-070.12: record Prometheus tool call metric with success/error status.
    _pid_label = str(_pid) if _pid else ""
    _aid_label = str(_aid) if _aid else ""
    _success = True
    try:
        with start_span(
            span_name,
            attributes=attributes,
            kind=SPAN_KIND_SERVER,
            record_exception=record_exception,
        ) as span:
            try:
                yield span
            except Exception:
                _success = False
                raise
    finally:
        with contextlib.suppress(Exception):
            _record_tool_call_metric(
                _pid_label,
                _aid_label,
                tool_name,
                "success" if _success else "error",
            )


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


# ---------------------------------------------------------------------------
# MCP params._meta.traceparent extraction — STORY-032.3
# ---------------------------------------------------------------------------

#: MCP JSON-RPC params key that carries W3C trace context (MCP 2025-03-26 spec).
MCP_META_KEY: str = "_meta"

#: W3C TraceContext header name.
W3C_TRACEPARENT_KEY: str = "traceparent"

#: W3C TraceState header name.
W3C_TRACESTATE_KEY: str = "tracestate"


def extract_trace_context_from_mcp_params(params: dict[str, Any] | None) -> Any:  # noqa: ANN401
    """Extract an OTel ``Context`` from MCP ``params._meta`` trace context fields.

    The MCP 2025-03-26 specification allows clients to propagate W3C
    ``traceparent`` / ``tracestate`` headers via the ``_meta`` key of any
    JSON-RPC ``params`` object.  This helper extracts those fields and
    delegates to :func:`extract_trace_context`.

    Example MCP params structure::

        {
            "query": "what is the test",
            "_meta": {
                "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
                "tracestate": "rojo=00f067aa0ba902b7"
            }
        }

    Args:
        params: The MCP JSON-RPC ``params`` dict, or ``None``.  Any type
            error (non-dict ``_meta``, missing keys) is silently handled.

    Returns:
        An OTel ``Context`` (possibly empty) when ``opentelemetry-api`` is
        available and a ``traceparent`` was found, otherwise ``None``.
    """
    if not params:
        return None
    meta = params.get(MCP_META_KEY)
    if not isinstance(meta, dict):
        return None
    carrier: dict[str, str] = {}
    tp = meta.get(W3C_TRACEPARENT_KEY)
    if isinstance(tp, str) and tp:
        carrier[W3C_TRACEPARENT_KEY] = tp
    ts = meta.get(W3C_TRACESTATE_KEY)
    if isinstance(ts, str) and ts:
        carrier[W3C_TRACESTATE_KEY] = ts
    if not carrier:
        return None
    return extract_trace_context(carrier)


# ---------------------------------------------------------------------------
# Retrieval document events — STORY-032.3
# ---------------------------------------------------------------------------

#: OTel event name for a single retrieval document result.
EVENT_RETRIEVAL_DOCUMENT: str = "gen_ai.retrieval.document"

#: Span event attribute: SHA-256-hashed document identifier (avoids raw key PII).
ATTR_RETRIEVAL_DOC_ID: str = "gen_ai.retrieval.document.id"

#: Span event attribute: relevance score for the retrieval result (float 0-1).
ATTR_RETRIEVAL_DOC_SCORE: str = "gen_ai.retrieval.document.relevance_score"

#: Span event attribute: memory tier of the retrieval result.
ATTR_RETRIEVAL_DOC_TIER: str = "gen_ai.retrieval.document.tier"

_DOC_ID_HASH_LEN: int = 16  # hex chars from SHA-256 (64-bit prefix)


def _hash_doc_id(key: str) -> str:
    """Return a 16-hex-char SHA-256 prefix of *key* for use as a document id.

    Hashing avoids exposing raw entry keys (which may be user-controlled /
    contain PII) while preserving a stable, collision-resistant identifier
    suitable for log correlation.
    """
    import hashlib

    return hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:_DOC_ID_HASH_LEN]


def record_retrieval_document_events(
    span: Any,  # noqa: ANN401
    memories: list[dict[str, Any]],
) -> None:
    """Add one OTel span event per retrieved memory document.

    Each event uses the name :data:`EVENT_RETRIEVAL_DOCUMENT` and carries:

    - ``gen_ai.retrieval.document.id`` — a 16-hex SHA-256 prefix of the
      entry key (hashed to avoid PII in telemetry).
    - ``gen_ai.retrieval.document.relevance_score`` — the float relevance
      score from :class:`~tapps_brain.models.RecallResult` (0.0 if absent).
    - ``gen_ai.retrieval.document.tier`` — the memory tier string (safe
      low-cardinality enum).

    This function is a **no-op** when:

    - *span* is ``None`` (OTel unavailable or disabled).
    - *memories* is empty.
    - The span does not support ``add_event`` (defensive: wrong type).

    .. warning::
        **Never** pass raw memory values or query text in the event attributes.
        Only hashed keys, numeric scores, and tier enums are allowed.

    Args:
        span: The active OTel span returned by :func:`start_span`, or ``None``.
        memories: The ``RecallResult.memories`` list — each item is a dict
            with keys ``key``, ``score`` (float), ``tier`` (str), etc.
    """
    if span is None or not memories:
        return
    add_event = getattr(span, "add_event", None)
    if add_event is None:
        return
    for mem in memories:
        if not isinstance(mem, dict):
            continue
        raw_key = mem.get("key", "")
        doc_id = _hash_doc_id(str(raw_key)) if raw_key else ""
        score = mem.get("score", 0.0)
        tier = mem.get("tier", "")
        event_attrs: dict[str, str | float] = {}
        if doc_id:
            event_attrs[ATTR_RETRIEVAL_DOC_ID] = doc_id
        if isinstance(score, (int, float)):
            event_attrs[ATTR_RETRIEVAL_DOC_SCORE] = float(score)
        if tier and isinstance(tier, str):
            event_attrs[ATTR_RETRIEVAL_DOC_TIER] = tier
        try:
            add_event(EVENT_RETRIEVAL_DOCUMENT, event_attrs)
        except Exception:
            pass  # OTel SDK errors must never propagate to callers


# ---------------------------------------------------------------------------
# Feedback events — STORY-032.7
# ---------------------------------------------------------------------------

#: OTel span event name for a tapps-brain feedback event.
EVENT_FEEDBACK: str = "tapps_brain.feedback"

#: Span event attribute: the event_type of the feedback event (safe low-cardinality enum).
ATTR_FEEDBACK_EVENT_TYPE: str = "tapps_brain.feedback.event_type"

#: Span event attribute: numeric utility score (float in [-1.0, 1.0] or absent).
ATTR_FEEDBACK_UTILITY_SCORE: str = "tapps_brain.feedback.utility_score"


def record_feedback_event(
    span: Any,  # noqa: ANN401
    event: Any,  # noqa: ANN401  — FeedbackEvent or dict-like; intentionally untyped to avoid import
) -> None:
    """Add a ``tapps_brain.feedback`` OTel span event for a recorded feedback signal.

    This function is a **no-op** when:

    - *span* is ``None`` (OTel unavailable or disabled).
    - *event* is ``None`` or does not have an ``event_type`` attribute.
    - The span does not support ``add_event`` (defensive: wrong type).

    Only safe, low-cardinality attributes are emitted:

    - ``tapps_brain.feedback.event_type`` — the Object-Action snake_case event name
      (e.g. ``"recall_rated"``, ``"gap_reported"``).  **Not** raw user content.
    - ``tapps_brain.feedback.utility_score`` — numeric score in [-1.0, 1.0] when set.

    **Forbidden** (never included): ``entry_key``, ``session_id``, ``details`` —
    these may contain user-controlled PII.  See the telemetry policy doc (STORY-061.6).

    Args:
        span: The active OTel span returned by :func:`start_span`, or ``None``.
        event: A ``FeedbackEvent`` instance (or any object with an ``event_type``
            attribute).  Gracefully skipped if the module is absent.
    """
    if span is None or event is None:
        return
    add_event = getattr(span, "add_event", None)
    if add_event is None:
        return

    event_type = getattr(event, "event_type", None)
    if not event_type or not isinstance(event_type, str):
        return

    event_attrs: dict[str, str | float] = {
        ATTR_FEEDBACK_EVENT_TYPE: event_type,
    }
    utility_score = getattr(event, "utility_score", None)
    if utility_score is not None and isinstance(utility_score, (int, float)):
        event_attrs[ATTR_FEEDBACK_UTILITY_SCORE] = float(utility_score)

    try:
        add_event(EVENT_FEEDBACK, event_attrs)
    except Exception:
        pass  # OTel SDK errors must never propagate to callers


# ---------------------------------------------------------------------------
# Diagnostics events — STORY-032.8
# ---------------------------------------------------------------------------

#: OTel span event name for a tapps-brain diagnostics report.
EVENT_DIAGNOSTICS_REPORT: str = "tapps_brain.diagnostics.report"

#: Span event attribute: composite quality score (float 0-1).
ATTR_DIAGNOSTICS_COMPOSITE_SCORE: str = "tapps_brain.diagnostics.composite_score"

#: Span event attribute: circuit breaker state ("closed", "degraded", "open", "half_open").
ATTR_DIAGNOSTICS_CIRCUIT_STATE: str = "tapps_brain.diagnostics.circuit_state"

#: Span event attribute: number of memory gaps currently tracked.
ATTR_DIAGNOSTICS_GAP_COUNT: str = "tapps_brain.diagnostics.gap_count"

#: Span event attribute: number of active anomalies in the report.
ATTR_DIAGNOSTICS_ANOMALY_COUNT: str = "tapps_brain.diagnostics.anomaly_count"


def record_diagnostics_event(
    span: Any,  # noqa: ANN401
    report: Any,  # noqa: ANN401  — DiagnosticsReport or dict-like; intentionally untyped
) -> None:
    """Add a ``tapps_brain.diagnostics.report`` OTel span event.

    This function is a **no-op** when:

    - *span* is ``None`` (OTel unavailable or disabled).
    - *report* is ``None`` or lacks a ``composite_score`` attribute.
    - The span does not support ``add_event`` (defensive: wrong type).

    Only safe, bounded attributes are emitted:

    - ``tapps_brain.diagnostics.composite_score`` — float 0.0–1.0.
    - ``tapps_brain.diagnostics.circuit_state`` — bounded enum string.
    - ``tapps_brain.diagnostics.gap_count`` — integer ≥ 0.
    - ``tapps_brain.diagnostics.anomaly_count`` — length of anomalies list.

    **Never emitted**: raw memory content, dimension scores containing entry keys,
    or any user-controlled string.  See telemetry policy (STORY-061.6).

    Args:
        span: The active OTel span returned by :func:`start_span`, or ``None``.
        report: A ``DiagnosticsReport`` instance (or any object with a
            ``composite_score`` attribute).  Gracefully skipped if absent.
    """
    if span is None or report is None:
        return
    add_event = getattr(span, "add_event", None)
    if add_event is None:
        return

    composite_score = getattr(report, "composite_score", None)
    if composite_score is None:
        return

    event_attrs: dict[str, str | float | int] = {}
    if isinstance(composite_score, (int, float)):
        event_attrs[ATTR_DIAGNOSTICS_COMPOSITE_SCORE] = float(composite_score)

    circuit_state = getattr(report, "circuit_state", None)
    if circuit_state is not None and isinstance(circuit_state, str):
        event_attrs[ATTR_DIAGNOSTICS_CIRCUIT_STATE] = circuit_state

    gap_count = getattr(report, "gap_count", None)
    if gap_count is not None and isinstance(gap_count, int):
        event_attrs[ATTR_DIAGNOSTICS_GAP_COUNT] = gap_count

    anomalies = getattr(report, "anomalies", None)
    if isinstance(anomalies, (list, tuple)):
        event_attrs[ATTR_DIAGNOSTICS_ANOMALY_COUNT] = len(anomalies)

    try:
        add_event(EVENT_DIAGNOSTICS_REPORT, event_attrs)
    except Exception:
        pass  # OTel SDK errors must never propagate to callers


# ---------------------------------------------------------------------------
# In-process retrieval counters — STORY-065.7
# ---------------------------------------------------------------------------
# These are plain Python accumulators (not OTel exportable) that reset on
# process restart.  They are incremented from the retrieval hot path and
# read by ``visual_snapshot._collect_retrieval_metrics()``.
#
# Instrument names (mirrored from OTel metric intent):
#   tapps_brain.recall.total       — total store.recall()/store.search() calls
#   tapps_brain.bm25.candidates    — cumulative BM25 candidate count
#   tapps_brain.vector.candidates  — cumulative vector candidate count
#   tapps_brain.rrf.fusions        — times RRF fused both BM25+vector legs
#   tapps_brain.recall.latency_ms  — running mean recall latency (ms)

_rm_lock: threading.Lock = threading.Lock()
_rm_recall_total: int = 0
_rm_bm25_candidates: int = 0
_rm_vector_candidates: int = 0
_rm_rrf_fusions: int = 0
_rm_latency_sum_ms: float = 0.0
_rm_latency_count: int = 0


def rm_increment_recall_total() -> None:
    """Increment the in-process recall/search query counter by 1."""
    global _rm_recall_total  # noqa: PLW0603
    with _rm_lock:
        _rm_recall_total += 1


def rm_add_bm25_candidates(n: int) -> None:
    """Add *n* to the cumulative BM25 candidate counter."""
    global _rm_bm25_candidates  # noqa: PLW0603
    if n <= 0:
        return
    with _rm_lock:
        _rm_bm25_candidates += n


def rm_add_vector_candidates(n: int) -> None:
    """Add *n* to the cumulative vector candidate counter."""
    global _rm_vector_candidates  # noqa: PLW0603
    if n <= 0:
        return
    with _rm_lock:
        _rm_vector_candidates += n


def rm_increment_rrf_fusions() -> None:
    """Increment the RRF fusion counter by 1."""
    global _rm_rrf_fusions  # noqa: PLW0603
    with _rm_lock:
        _rm_rrf_fusions += 1


def rm_add_recall_latency_ms(ms: float) -> None:
    """Record one recall latency observation (milliseconds)."""
    global _rm_latency_sum_ms, _rm_latency_count  # noqa: PLW0603
    if ms < 0:
        return
    with _rm_lock:
        _rm_latency_sum_ms += ms
        _rm_latency_count += 1


def get_retrieval_meter_snapshot() -> dict[str, int | float]:
    """Return a snapshot of the in-process retrieval counters.

    Returns a dict with keys matching the OTel instrument intent:

    - ``total_queries`` — cumulative store.recall()/store.search() calls
    - ``bm25_hits`` — cumulative BM25 candidates across all queries
    - ``vector_hits`` — cumulative vector candidates (0 when BM25-only)
    - ``rrf_fusions`` — number of queries where both legs had candidates
    - ``mean_latency_ms`` — running mean latency in milliseconds (0.0 if none)

    All values are 0 / 0.0 until the first query since process start.
    This function never raises.
    """
    try:
        with _rm_lock:
            mean_lat = (
                _rm_latency_sum_ms / _rm_latency_count if _rm_latency_count > 0 else 0.0
            )
            return {
                "total_queries": _rm_recall_total,
                "bm25_hits": _rm_bm25_candidates,
                "vector_hits": _rm_vector_candidates,
                "rrf_fusions": _rm_rrf_fusions,
                "mean_latency_ms": mean_lat,
            }
    except Exception:  # pragma: no cover
        return {
            "total_queries": 0,
            "bm25_hits": 0,
            "vector_hits": 0,
            "rrf_fusions": 0,
            "mean_latency_ms": 0.0,
        }
