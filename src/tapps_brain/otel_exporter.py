"""Optional OpenTelemetry exporter for tapps-brain metrics (STORY-007.5, STORY-061.2).

Converts ``MetricsSnapshot`` counters, histograms, and gauges into OpenTelemetry
metrics. The ``opentelemetry-api`` package is a core dependency (no-op
when no SDK is configured). Install ``pip install tapps-brain[otel]``
to get the SDK for actual metric export.

When the OpenTelemetry SDK is not installed, :func:`create_exporter`
returns ``None`` and no metrics are exported.

:class:`GenAIMetricsRecorder` (STORY-032.5) provides direct recording of the
standard GenAI semantic convention v1.35.0 metric instruments:

- ``gen_ai.client.operation.duration`` (seconds, histogram)
- ``mcp.server.operation.duration`` (seconds, histogram)
- ``gen_ai.client.token.usage`` (tokens, histogram)

---------------------------------------------------------------------------
Allowed metric dimensions (label / attribute set)
---------------------------------------------------------------------------

Only attributes from the following bounded list are safe to attach to OTel
metric instruments.  Using raw user content as an attribute value creates
unbounded cardinality which can degrade your metrics backend.

**Allowed** (fixed, low-cardinality enums):

.. code-block:: text

    operation.type       — "remember" | "recall" | "search" | "hive_propagate" | "hive_search"
    memory.tier          — "architectural" | "pattern" | "procedural" | "context"
    memory.scope         — "project" | "branch" | "session"
    error.type           — "content_blocked" | "invalid_scope" | "invalid_group"
                           | "write_rules_violation" | "db_error"
    hive.group_scoped    — "true" | "false"   (NOT the group name — that is user-controlled)
    gen_ai.system        — "tapps-brain"   (fixed constant)
    gen_ai.operation.name — "remember" | "recall" | "execute_tool" | etc.
    gen_ai.token.type    — "input" | "output" | "total"
    mcp.method.name      — "tools/call" | "resources/read" | etc.
    gen_ai.tool.name     — registered MCP tool name (bounded, not user content)

**Forbidden** (unbounded / PII risk):

.. code-block:: text

    memory.key       — user-controlled string
    memory.value     — raw memory content
    query.text       — raw search / recall query
    session_id       — user PII
    agent_id         — potentially user-controlled

This policy is enforced by code review and the :class:`MemoryBodyRedactionFilter`
log handler (STORY-061.7).  See the telemetry policy doc for the rationale
(STORY-061.6).

---------------------------------------------------------------------------
Log redaction (STORY-061.7)
---------------------------------------------------------------------------

:class:`MemoryBodyRedactionFilter` is a Python ``logging.Filter`` that strips
or SHA-256-hashes memory body content from log records before they reach any
handler.  Attach it to a logger or handler directly, or call
:func:`install_memory_redaction_filter` for the ``tapps_brain`` logger tree.

:func:`create_allowed_attribute_views` returns OTel SDK ``View`` objects that
filter metric instruments to the allowed dimension set, dropping any forbidden
high-cardinality attribute key before the instrument records its value.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib.util
import logging
import os
import re
from typing import TYPE_CHECKING, Any

try:
    from opentelemetry.metrics import get_meter
except ImportError:  # pragma: no cover — opentelemetry-api is a core dependency
    get_meter = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from tapps_brain.metrics import MetricsSnapshot

# ---------------------------------------------------------------------------
# HAS_OTEL — public feature flag (STORY-032.1)
# ---------------------------------------------------------------------------

#: ``True`` when the ``opentelemetry-api`` package is importable at runtime.
#:
#: When ``False`` every OTel code path is a zero-overhead no-op — no spans,
#: no metrics, no allocations on the hot path.
#:
#: Note: even when ``HAS_OTEL`` is ``True``, :class:`OTelConfig` can disable
#: instrumentation via ``OTelConfig.enabled = False``.
HAS_OTEL: bool = get_meter is not None

# ---------------------------------------------------------------------------
# OTelConfig — bootstrap configuration (STORY-032.1)
# ---------------------------------------------------------------------------


def _parse_bool_env(value: str, default: bool) -> bool:
    """Parse a boolean environment variable string.

    Returns *default* when *value* is empty/whitespace.
    Truthy: ``"1"``, ``"true"``, ``"yes"`` (case-insensitive).
    Falsy: ``"0"``, ``"false"``, ``"no"`` (case-insensitive).
    """
    v = value.strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes"}


@dataclasses.dataclass
class OTelConfig:
    """Bootstrap configuration for tapps-brain OpenTelemetry instrumentation.

    Controls whether OTel is active (``enabled``), which service identity to
    report (``service_name``), and whether to emit memory/query content as span
    attributes (``capture_content``).

    When ``enabled`` is ``False`` the tracer returned by :func:`bootstrap_tracer`
    is ``None`` — a null-object that causes :func:`tapps_brain.otel_tracer.start_span`
    to yield ``None`` immediately with **zero allocation** on the hot path.

    **Privacy — content capture (STORY-032.9)**

    ``capture_content`` controls whether memory content or query strings may be
    attached to OTel spans.  The **default is ``False``** (opt-in, not opt-out)
    to protect user data.

    When ``False`` (default), content attributes are **omitted entirely** —
    never replaced with a placeholder like ``"[REDACTED]"``.  A placeholder
    would still leak the fact that content was present and could confuse
    consumers.  Omitting the attribute is the only correct privacy posture.

    This mirrors the standard
    ``OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`` env var defined by
    the OpenTelemetry GenAI semantic conventions.

    Usage::

        from tapps_brain.otel_exporter import OTelConfig, bootstrap_tracer

        # From environment (recommended)
        tracer = bootstrap_tracer(OTelConfig.from_env())

        # Explicit disable
        tracer = bootstrap_tracer(OTelConfig(enabled=False))

        # Opt-in to content capture (non-prod / debug only)
        cfg = OTelConfig(capture_content=True)
    """

    enabled: bool = True
    """Soft on/off switch, independent of :data:`HAS_OTEL` library availability."""

    service_name: str = "tapps-brain"
    """Value for the ``service.name`` OTel resource attribute."""

    capture_content: bool = False
    """Whether memory content / query strings may be emitted as span attributes.

    Default: ``False`` (opt-in).  When ``False``, content attributes are
    **omitted entirely** — not redacted or replaced with placeholders.

    Set to ``True`` only in controlled, non-production environments where
    you need to correlate memory bodies with traces.  Never enable in prod.

    Controlled by (in priority order):

    1. ``TAPPS_BRAIN_OTEL_CAPTURE_CONTENT`` env var (``"1"``/``"true"``/``"yes"``
       or ``"0"``/``"false"``/``"no"``).
    2. ``OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`` env var (same values)
       — standard GenAI semconv variable.
    3. Default ``False`` when neither is set.
    """

    @classmethod
    def from_env(cls) -> OTelConfig:
        """Construct :class:`OTelConfig` from environment variables.

        Environment variables read:

        .. code-block:: text

            TAPPS_BRAIN_OTEL_ENABLED    — "1"/"true"/"yes" enables (default).
                                           "0"/"false"/"no"  disables.
            OTEL_SERVICE_NAME            — service name (default: ``"tapps-brain"``).
            TAPPS_BRAIN_OTEL_CAPTURE_CONTENT
                                         — opt-in content capture (default: ``"0"``).
                                           Takes priority over the semconv var below.
            OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT
                                         — standard GenAI semconv env var; used when
                                           TAPPS_BRAIN_OTEL_CAPTURE_CONTENT is unset.

        Returns:
            A new :class:`OTelConfig` instance populated from the environment.
        """
        raw_enabled = os.environ.get("TAPPS_BRAIN_OTEL_ENABLED", "1").strip().lower()
        enabled = raw_enabled not in {"0", "false", "no"}
        service_name = os.environ.get("OTEL_SERVICE_NAME", "tapps-brain") or "tapps-brain"

        # Privacy: content capture — tapps-brain var takes priority over semconv var.
        tapps_capture_raw = os.environ.get("TAPPS_BRAIN_OTEL_CAPTURE_CONTENT", "")
        if tapps_capture_raw.strip():
            capture_content = _parse_bool_env(tapps_capture_raw, default=False)
        else:
            semconv_raw = os.environ.get(
                "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", ""
            )
            capture_content = _parse_bool_env(semconv_raw, default=False)

        return cls(enabled=enabled, service_name=service_name, capture_content=capture_content)


def should_capture_content(config: OTelConfig | None = None) -> bool:
    """Return ``True`` if memory / query content may be emitted as span attributes.

    This is the **single canonical check** that all tapps-brain instrumentation
    code must call before attaching any content attribute to a span or metric.
    When it returns ``False``, the attribute must be **omitted entirely** — not
    set to a placeholder like ``"[REDACTED]"``.

    A placeholder still leaks that content was present and may confuse trace
    consumers.  Omitting the attribute is the only correct privacy posture.

    When *config* is ``None`` (most call sites), the function reads environment
    variables via :meth:`OTelConfig.from_env` — the result is **not cached**,
    so env changes during a process lifetime are respected.

    Args:
        config: Explicit :class:`OTelConfig`, or ``None`` to read from env.

    Returns:
        ``True`` if content capture is explicitly opted-in; ``False`` otherwise.

    Example::

        from tapps_brain.otel_exporter import should_capture_content
        from tapps_brain.otel_tracer import start_span

        with start_span("tapps_brain.remember") as span:
            if span and should_capture_content():
                span.set_attribute("gen_ai.prompt", query_text)
            # … rest of operation …
    """
    cfg = config if config is not None else OTelConfig.from_env()
    return cfg.capture_content


def bootstrap_tracer(config: OTelConfig | None = None) -> Any:  # noqa: ANN401
    """Return an OTel tracer for *config*, or ``None`` when OTel is disabled.

    This is the **single canonical place** that creates a tracer.  All
    downstream stories (032.2, 032.3, …) call this function.

    Returns ``None`` (null-object / no-op) when **either**:

    - :data:`HAS_OTEL` is ``False`` (``opentelemetry-api`` not installed), **or**
    - ``config.enabled`` is ``False``

    When ``None`` is returned, :func:`tapps_brain.otel_tracer.start_span` is
    already wired to yield ``None`` without creating any span objects —
    **zero allocation** on the hot path.

    Args:
        config: OTel bootstrap configuration.  When ``None`` a default
            :class:`OTelConfig` (all defaults, enabled) is used.

    Returns:
        An OTel ``Tracer`` instance, or ``None`` when OTel is disabled.
    """
    cfg = config if config is not None else OTelConfig()
    if not HAS_OTEL or not cfg.enabled:
        return None
    try:
        from opentelemetry import trace  # lazy — API is a core dep but guard anyway

        return trace.get_tracer(cfg.service_name)
    except Exception:  # OTel import / init errors must not crash callers
        return None  # pragma: no cover


# ---------------------------------------------------------------------------
# Documented allowed metric dimensions — see module docstring.
# ---------------------------------------------------------------------------

ALLOWED_METRIC_DIMENSIONS: frozenset[str] = frozenset(
    {
        "operation.type",
        "memory.tier",
        "memory.scope",
        "error.type",
        "hive.group_scoped",
        # GenAI semantic convention v1.35.0 dimensions (STORY-032.5)
        "gen_ai.system",
        "gen_ai.operation.name",
        "gen_ai.token.type",
        "gen_ai.tool.name",
        "mcp.method.name",
    }
)
"""Bounded set of safe metric attribute (dimension) names.

Any attribute **not** in this set must be reviewed before use.  Attributes
that contain raw user content (memory text, query strings, entry keys) are
**permanently forbidden** — see the module docstring.

The GenAI semconv v1.35.0 attributes (``gen_ai.*``, ``mcp.method.name``) are
low-cardinality enums and therefore safe to use as metric dimensions.
"""

FORBIDDEN_METRIC_DIMENSIONS: frozenset[str] = frozenset(
    {
        "memory.key",
        "memory.value",
        "query.text",
        "session_id",
        "agent_id",
    }
)
"""Permanently forbidden metric attribute names (unbounded cardinality / PII)."""

# ---------------------------------------------------------------------------
# Forbidden log field names — STORY-061.7
# ---------------------------------------------------------------------------

#: LogRecord extra-field names that must never appear as raw content in logs.
#: The redaction filter strips / hashes the value of any field in this set.
FORBIDDEN_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "content",
        "body",
        "memory_value",
        "memory_body",
        "query_text",
        "search_query",
        # dotted variants that may appear as kwargs in structured loggers
        "memory.value",
        "memory.body",
        "query.text",
    }
)
"""Log-record field names whose values contain raw memory / query content.

Any field in this set is replaced with ``[REDACTED:<hash>]`` by
:class:`MemoryBodyRedactionFilter` before the record reaches a handler.
"""

# Regex that matches inline ``key=<value>`` / ``key: <value>`` patterns in
# the *formatted* log message where ``key`` is a forbidden field name.
_INLINE_FIELD_RE: re.Pattern[str] = re.compile(
    r"(?<![.\w])("
    + "|".join(re.escape(k) for k in sorted(FORBIDDEN_LOG_FIELDS, key=len, reverse=True))
    + r")\s*[=:]\s*(?P<val>\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Log redaction filter — STORY-061.7
# ---------------------------------------------------------------------------


class MemoryBodyRedactionFilter(logging.Filter):
    """Python :class:`logging.Filter` that strips memory content from log records.

    Attaches to a :class:`logging.Logger` or :class:`logging.Handler` to ensure
    raw memory bodies, query strings, and other forbidden content **never** appear
    in structured log output, even at ``DEBUG`` level.

    Forbidden field values on the :class:`~logging.LogRecord` (set via
    ``extra={...}``) are replaced with ``[REDACTED:<sha256-prefix>]`` where the
    8-hex-char SHA-256 prefix enables correlated record matching without exposing
    the raw value.

    Inline occurrences of ``key=<value>`` / ``key: <value>`` patterns in the
    formatted message string are also redacted.

    The filter **always returns** ``True`` — it redacts in-place but never
    suppresses records.

    Usage::

        # Attach to a handler
        handler = logging.StreamHandler()
        handler.addFilter(MemoryBodyRedactionFilter())

        # Or attach to the tapps_brain logger tree (recommended)
        install_memory_redaction_filter()
    """

    _REDACTED_FMT: str = "[REDACTED:{hash}]"
    _HASH_LEN: int = 8

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact forbidden fields from *record* in-place; always allow the record."""
        self._redact_extra_fields(record)
        self._redact_message(record)
        return True

    def _redact_extra_fields(self, record: logging.LogRecord) -> None:
        """Replace values of forbidden extra fields with hashed placeholders."""
        for field in FORBIDDEN_LOG_FIELDS:
            if hasattr(record, field):
                raw = getattr(record, field)
                if isinstance(raw, str) and raw:
                    placeholder = self._REDACTED_FMT.format(hash=self._hash(raw))
                else:
                    placeholder = "[REDACTED]"
                with contextlib.suppress(AttributeError):
                    # some attrs are read-only on certain platforms
                    setattr(record, field, placeholder)

    def _redact_message(self, record: logging.LogRecord) -> None:
        """Redact inline forbidden-key patterns in the formatted message."""
        try:
            msg = record.getMessage()
        except Exception:
            return

        def _replace(m: re.Match[str]) -> str:
            raw_val = m.group("val").strip("\"'")
            hashed = self._hash(raw_val) if raw_val else ""
            replacement = f"[REDACTED:{hashed}]" if hashed else "[REDACTED]"
            return m.group(0)[: m.start("val") - m.start(0)] + replacement

        redacted = _INLINE_FIELD_RE.sub(_replace, msg)
        if redacted != msg:
            record.msg = redacted
            record.args = ()

    @staticmethod
    def _hash(value: str) -> str:
        """Return the first 8 hex chars of SHA-256 of *value*."""
        return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[
            : MemoryBodyRedactionFilter._HASH_LEN
        ]


def install_memory_redaction_filter(
    logger_name: str = "tapps_brain",
) -> MemoryBodyRedactionFilter:
    """Install a :class:`MemoryBodyRedactionFilter` on the named Python logger.

    Safe to call multiple times — a second call on the same logger is a no-op
    that returns the existing filter instance.

    Args:
        logger_name: The logger name to attach to.  Defaults to ``"tapps_brain"``
            which covers all child loggers in the tapps-brain package.

    Returns:
        The :class:`MemoryBodyRedactionFilter` instance attached to the logger.
    """
    logger = logging.getLogger(logger_name)
    for f in logger.filters:
        if isinstance(f, MemoryBodyRedactionFilter):
            return f
    filt = MemoryBodyRedactionFilter()
    logger.addFilter(filt)
    return filt


# ---------------------------------------------------------------------------
# OTel metric Views — STORY-061.7
# ---------------------------------------------------------------------------


def create_allowed_attribute_views() -> list[Any]:
    """Return OTel SDK ``View`` objects that enforce the allowed metric dimension set.

    Each returned :class:`opentelemetry.sdk.metrics.view.View` applies to all
    instruments (wildcard name ``"*"``) and restricts recorded attributes to
    :data:`ALLOWED_METRIC_DIMENSIONS`.  Any forbidden attribute key set by calling
    code is silently dropped before the metric value is recorded.

    Pass the returned list to ``MeterProvider(views=[...])`` at startup::

        from opentelemetry.sdk.metrics import MeterProvider
        from tapps_brain.otel_exporter import create_allowed_attribute_views

        provider = MeterProvider(views=create_allowed_attribute_views())

    Returns an **empty list** when the OTel SDK is not installed so that callers
    do not need to guard the call.
    """
    if not _has_otel_sdk():
        return []
    try:
        from opentelemetry.sdk.metrics.view import View

        return [
            View(
                instrument_name="*",
                attribute_keys=set(ALLOWED_METRIC_DIMENSIONS),
            )
        ]
    except Exception:  # OTel SDK internal changes should not crash callers
        return []


class OTelExporter:
    """Exports :class:`MetricsSnapshot` data to OpenTelemetry.

    Uses the OTel Metrics API to create counters, histograms, and
    up-down counters that mirror the in-memory collector's state.

    .. note::
        Each :meth:`export` call sends only the *delta* since the last
        export, not the cumulative total.  This matches the OTel
        ``Counter`` contract where ``add()`` accepts a non-negative
        increment, not an absolute value.

        Gauges (from ``MetricsSnapshot.gauges``) are exported as
        absolute point-in-time readings via OTel **up-down counters**
        — the delta between the previous and current reading is applied
        on each call.
    """

    def __init__(self, meter: Any = None) -> None:  # noqa: ANN401
        """Initialise with an optional OTel ``Meter`` instance.

        If *meter* is ``None``, a default meter named ``tapps_brain``
        is created from the global meter provider.
        """
        self._meter = meter if meter is not None else get_meter("tapps_brain")

        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self._up_down_counters: dict[str, Any] = {}
        # Track last-exported counter values so we only send deltas.
        self._last_counter_values: dict[str, int] = {}
        # Track last-exported gauge values so we only send deltas.
        self._last_gauge_values: dict[str, float] = {}

    def _get_counter(self, name: str) -> Any:  # noqa: ANN401
        """Lazily create or return an OTel counter."""
        if name not in self._counters:
            self._counters[name] = self._meter.create_counter(
                name=name,
                description=f"tapps-brain counter: {name}",
            )
        return self._counters[name]

    def _get_histogram(self, name: str, unit: str = "ms") -> Any:  # noqa: ANN401
        """Lazily create or return an OTel histogram.

        *unit* defaults to ``"ms"`` (milliseconds) which is correct for the
        latency histograms emitted by :class:`~tapps_brain.metrics.MetricsTimer`.
        Histograms tracking other quantities (sizes, counts) should pass an
        appropriate unit string.
        """
        if name not in self._histograms:
            self._histograms[name] = self._meter.create_histogram(
                name=name,
                description=f"tapps-brain histogram: {name}",
                unit=unit,
            )
        return self._histograms[name]

    def _get_up_down_counter(self, name: str) -> Any:  # noqa: ANN401
        """Lazily create or return an OTel up-down counter for gauges.

        Up-down counters are used for values that can increase **and** decrease
        (e.g. pool connections in use, entry count).  The delta between the
        previous export and the current reading is applied on each :meth:`export`
        call.
        """
        if name not in self._up_down_counters:
            self._up_down_counters[name] = self._meter.create_up_down_counter(
                name=name,
                description=f"tapps-brain gauge: {name}",
            )
        return self._up_down_counters[name]

    def export(self, snapshot: MetricsSnapshot) -> None:
        """Export a snapshot to OpenTelemetry.

        Only the *delta* since the last call is sent for counters and gauges
        so that repeated exports do not double-count.  Histogram stats are
        recorded as a single observation of ``stats.mean`` so the OTel SDK
        can aggregate them.

        Errors from the OTel SDK are caught and silently suppressed so that
        an unavailable collector never crashes the caller.
        """
        try:
            for name, value in snapshot.counters.items():
                counter = self._get_counter(name)
                last = self._last_counter_values.get(name, 0)
                delta = value - last
                if delta > 0:
                    counter.add(delta)
                self._last_counter_values[name] = value

            for name, stats in snapshot.histograms.items():
                histogram = self._get_histogram(name)
                # Record representative values so the OTel SDK can compute aggregates
                if stats.count > 0:
                    histogram.record(stats.mean)

            for name, current in snapshot.gauges.items():
                udc = self._get_up_down_counter(name)
                last_g = self._last_gauge_values.get(name, 0.0)
                delta_g = current - last_g
                if delta_g != 0.0:
                    udc.add(delta_g)
                self._last_gauge_values[name] = current

        except Exception:  # OTel SDK failures must not propagate to callers
            pass


# ---------------------------------------------------------------------------
# GenAI semantic convention v1.35.0 metric instrument names (STORY-032.5)
# ---------------------------------------------------------------------------

#: Standard GenAI client operation duration histogram name (semconv v1.35.0).
GEN_AI_OPERATION_DURATION_METRIC: str = "gen_ai.client.operation.duration"

#: Standard MCP server operation duration histogram name (semconv v1.35.0).
MCP_SERVER_OPERATION_DURATION_METRIC: str = "mcp.server.operation.duration"

#: Standard GenAI client token usage histogram name (semconv v1.35.0).
GEN_AI_TOKEN_USAGE_METRIC: str = "gen_ai.client.token.usage"

#: Fixed ``gen_ai.system`` attribute value for tapps-brain.
_GEN_AI_SYSTEM_VALUE: str = "tapps-brain"

# ---------------------------------------------------------------------------
# Custom tapps_brain.* metric instrument names (STORY-032.6)
# ---------------------------------------------------------------------------

#: Gauge tracking the total number of memory entries in the private store.
#:
#: .. warning::
#:     **Cardinality rule** — never attach ``entry_key``, ``query``,
#:     ``session_id``, or any user-controlled string as an attribute on these
#:     instruments.  The only safe labels are the bounded enum keys in
#:     :data:`ALLOWED_METRIC_DIMENSIONS`.
TAPPS_BRAIN_ENTRIES_COUNT_METRIC: str = "tapps_brain.entries.count"

#: Gauge tracking the number of candidate memory entries identified for consolidation
#: at the last health-check or GC scan.  Staleness: updated when ``MemoryStore.health()``
#: or ``MemoryStore.gc()`` runs, not on every ``get_metrics()`` call.
TAPPS_BRAIN_CONSOLIDATION_CANDIDATES_METRIC: str = "tapps_brain.consolidation.candidates"

#: Gauge tracking the number of candidate memory entries identified for garbage collection
#: at the last GC scan.  Staleness: updated when ``MemoryStore.gc()`` runs.
TAPPS_BRAIN_GC_CANDIDATES_METRIC: str = "tapps_brain.gc.candidates"


class GenAIMetricsRecorder:
    """Records GenAI semantic convention v1.35.0 metrics via the OTel Metrics API.

    Creates three standard histogram instruments on init:

    - :data:`GEN_AI_OPERATION_DURATION_METRIC` (``gen_ai.client.operation.duration``,
      unit ``s``) — duration of high-level AgentBrain / MemoryStore operations.
    - :data:`MCP_SERVER_OPERATION_DURATION_METRIC` (``mcp.server.operation.duration``,
      unit ``s``) — duration of individual MCP server tool calls.
    - :data:`GEN_AI_TOKEN_USAGE_METRIC` (``gen_ai.client.token.usage``,
      unit ``{token}``) — token counts for any LLM-aware path (optional / future).

    All recording methods are **no-ops** when the meter is ``None`` or when the
    OTel API raises an exception.  OTel SDK failures **never** propagate to callers.

    Usage::

        from tapps_brain.otel_exporter import GenAIMetricsRecorder
        from opentelemetry.metrics import get_meter

        recorder = GenAIMetricsRecorder(meter=get_meter("my-service"))

        import time
        t0 = time.perf_counter()
        # … do some work …
        recorder.record_gen_ai_operation(
            time.perf_counter() - t0,
            operation="remember",
        )

    When no *meter* is supplied, a default meter named ``"tapps_brain"`` is
    created from the global meter provider (which is a no-op when the OTel SDK
    is not configured).
    """

    def __init__(self, meter: Any = None) -> None:  # noqa: ANN401
        """Initialise with an optional OTel ``Meter`` instance.

        If *meter* is ``None`` and ``opentelemetry-api`` is installed, a default
        meter named ``"tapps_brain"`` is used.  All instruments are created lazily
        on the first recording call if construction fails.
        """
        if meter is None and get_meter is not None:
            self._meter: Any = get_meter("tapps_brain")
        else:
            self._meter = meter

        self._gen_ai_duration: Any = None
        self._mcp_duration: Any = None
        self._token_usage: Any = None
        self._init_instruments()

    def _init_instruments(self) -> None:
        """Create the three standard histogram instruments; silently skip on error."""
        if self._meter is None:
            return
        try:
            self._gen_ai_duration = self._meter.create_histogram(
                name=GEN_AI_OPERATION_DURATION_METRIC,
                description="Duration of GenAI client operations (AgentBrain / MemoryStore)",
                unit="s",
            )
            self._mcp_duration = self._meter.create_histogram(
                name=MCP_SERVER_OPERATION_DURATION_METRIC,
                description="Duration of MCP server tool call operations",
                unit="s",
            )
            self._token_usage = self._meter.create_histogram(
                name=GEN_AI_TOKEN_USAGE_METRIC,
                description="Number of tokens processed in GenAI operations",
                unit="{token}",
            )
        except Exception:
            # OTel SDK internal errors must not propagate
            pass

    def record_gen_ai_operation(
        self,
        duration_s: float,
        *,
        operation: str = "",
        system: str = _GEN_AI_SYSTEM_VALUE,
        error_type: str | None = None,
    ) -> None:
        """Record a :data:`GEN_AI_OPERATION_DURATION_METRIC` observation.

        Args:
            duration_s: Elapsed time in **seconds**.
            operation: ``gen_ai.operation.name`` attribute value
                (e.g. ``"remember"``, ``"recall"``, ``"execute_tool"``).
                Omitted from attributes when empty.
            system: ``gen_ai.system`` attribute (default: ``"tapps-brain"``).
            error_type: Optional ``error.type`` attribute
                (e.g. ``"content_blocked"``).  Omitted when ``None``.
        """
        if self._gen_ai_duration is None:
            return
        attrs: dict[str, str] = {}
        if operation:
            attrs["gen_ai.operation.name"] = operation
        if system:
            attrs["gen_ai.system"] = system
        if error_type:
            attrs["error.type"] = error_type
        try:
            self._gen_ai_duration.record(duration_s, attrs)
        except Exception:
            pass

    def record_mcp_operation(
        self,
        duration_s: float,
        *,
        method: str = "",
        tool_name: str = "",
        error_type: str | None = None,
    ) -> None:
        """Record a :data:`MCP_SERVER_OPERATION_DURATION_METRIC` observation.

        Args:
            duration_s: Elapsed time in **seconds**.
            method: ``mcp.method.name`` attribute (e.g. ``"tools/call"``).
                Omitted from attributes when empty.
            tool_name: ``gen_ai.tool.name`` attribute (e.g. ``"brain_remember"``).
                Must be a registered MCP tool name — **not** raw user content.
                Omitted when empty.
            error_type: Optional ``error.type`` attribute.  Omitted when ``None``.
        """
        if self._mcp_duration is None:
            return
        attrs: dict[str, str] = {}
        if method:
            attrs["mcp.method.name"] = method
        if tool_name:
            attrs["gen_ai.tool.name"] = tool_name
        if error_type:
            attrs["error.type"] = error_type
        try:
            self._mcp_duration.record(duration_s, attrs)
        except Exception:
            pass

    def record_token_usage(
        self,
        token_count: int,
        *,
        token_type: str = "total",
        operation: str = "",
        system: str = _GEN_AI_SYSTEM_VALUE,
    ) -> None:
        """Record a :data:`GEN_AI_TOKEN_USAGE_METRIC` observation.

        Args:
            token_count: Number of tokens (non-negative integer).
            token_type: ``gen_ai.token.type`` attribute — one of
                ``"input"``, ``"output"``, or ``"total"`` (default).
            operation: ``gen_ai.operation.name`` attribute.  Omitted when empty.
            system: ``gen_ai.system`` attribute (default: ``"tapps-brain"``).
        """
        if self._token_usage is None:
            return
        attrs: dict[str, str] = {"gen_ai.token.type": token_type}
        if operation:
            attrs["gen_ai.operation.name"] = operation
        if system:
            attrs["gen_ai.system"] = system
        try:
            self._token_usage.record(token_count, attrs)
        except Exception:
            pass


def _has_otel_sdk() -> bool:
    """Return True when ``opentelemetry.sdk`` is importable."""
    try:
        return importlib.util.find_spec("opentelemetry.sdk") is not None
    except (ModuleNotFoundError, ValueError):
        return False


def create_exporter(meter: Any = None) -> OTelExporter | None:  # noqa: ANN401
    """Create an exporter if the OpenTelemetry SDK is available.

    Returns ``None`` when ``opentelemetry-sdk`` is not installed.
    The API package (always available) is a no-op without the SDK.
    """
    if not _has_otel_sdk():
        return None
    return OTelExporter(meter=meter)
