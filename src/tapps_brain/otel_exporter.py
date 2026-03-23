"""Optional OpenTelemetry exporter for tapps-brain metrics (STORY-007.5).

Converts ``MetricsSnapshot`` counters and histograms into OpenTelemetry
metrics. Requires the ``opentelemetry-api`` and ``opentelemetry-sdk``
packages — install via ``pip install tapps-brain[otel]``.

When OpenTelemetry is not installed, :func:`create_exporter` returns
``None`` and no metrics are exported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tapps_brain._feature_flags import feature_flags

if TYPE_CHECKING:
    from tapps_brain.metrics import MetricsSnapshot


class OTelExporter:
    """Exports :class:`MetricsSnapshot` data to OpenTelemetry.

    Uses the OTel Metrics API to create counters and histograms that
    mirror the in-memory collector's state.

    .. note::
        Each :meth:`export` call sends only the *delta* since the last
        export, not the cumulative total.  This matches the OTel
        ``Counter`` contract where ``add()`` accepts a non-negative
        increment, not an absolute value.
    """

    def __init__(self, meter: Any = None) -> None:  # noqa: ANN401
        """Initialise with an optional OTel ``Meter`` instance.

        If *meter* is ``None``, a default meter named ``tapps_brain``
        is created from the global meter provider.
        """
        if meter is not None:
            self._meter = meter
        else:
            from opentelemetry.metrics import get_meter

            self._meter = get_meter("tapps_brain")

        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        # Track last-exported counter values so we only send deltas.
        self._last_counter_values: dict[str, int] = {}

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

    def export(self, snapshot: MetricsSnapshot) -> None:
        """Export a snapshot to OpenTelemetry.

        Only the *delta* since the last call is sent for counters so that
        repeated exports do not double-count.  Histogram stats are recorded
        as a single observation of ``stats.mean`` so the OTel SDK can
        aggregate them.

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
        except Exception:  # OTel SDK failures must not propagate to callers
            pass


def create_exporter(meter: Any = None) -> OTelExporter | None:  # noqa: ANN401
    """Create an exporter if OpenTelemetry is available.

    Returns ``None`` when the ``opentelemetry`` SDK is not installed.
    """
    if not feature_flags.otel:
        return None
    return OTelExporter(meter=meter)
