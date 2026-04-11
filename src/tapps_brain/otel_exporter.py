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

This policy is enforced by code review.  See the telemetry policy doc for
the rationale (STORY-061.6).
"""

from __future__ import annotations

import importlib.util
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
