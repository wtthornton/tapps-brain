"""Optional OpenTelemetry exporter for tapps-brain metrics (STORY-007.5, STORY-061.2).

Converts ``MetricsSnapshot`` counters, histograms, and gauges into OpenTelemetry
metrics. The ``opentelemetry-api`` package is a core dependency (no-op
when no SDK is configured). Install ``pip install tapps-brain[otel]``
to get the SDK for actual metric export.

When the OpenTelemetry SDK is not installed, :func:`create_exporter`
returns ``None`` and no metrics are exported.

---------------------------------------------------------------------------
Allowed metric dimensions (label / attribute set)
---------------------------------------------------------------------------

Only attributes from the following bounded list are safe to attach to OTel
metric instruments.  Using raw user content as an attribute value creates
unbounded cardinality which can degrade your metrics backend.

**Allowed** (fixed, low-cardinality enums):

.. code-block:: text

    operation.type   — "remember" | "recall" | "search" | "hive_propagate" | "hive_search"
    memory.tier      — "architectural" | "pattern" | "procedural" | "context"
    memory.scope     — "project" | "branch" | "session"
    error.type       — "content_blocked" | "invalid_scope" | "invalid_group"
                       | "write_rules_violation" | "db_error"
    hive.group_scoped — "true" | "false"   (NOT the group name — that is user-controlled)

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
import hashlib
import importlib.util
import logging
import re
from typing import TYPE_CHECKING, Any

try:
    from opentelemetry.metrics import get_meter
except ImportError:  # pragma: no cover — opentelemetry-api is a core dependency
    get_meter = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from tapps_brain.metrics import MetricsSnapshot

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
    }
)
"""Bounded set of safe metric attribute (dimension) names.

Any attribute **not** in this set must be reviewed before use.  Attributes
that contain raw user content (memory text, query strings, entry keys) are
**permanently forbidden** — see the module docstring.
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
