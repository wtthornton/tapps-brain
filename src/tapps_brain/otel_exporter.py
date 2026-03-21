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

    def _get_counter(self, name: str) -> Any:  # noqa: ANN401
        """Lazily create or return an OTel counter."""
        if name not in self._counters:
            self._counters[name] = self._meter.create_counter(
                name=name,
                description=f"tapps-brain counter: {name}",
            )
        return self._counters[name]

    def _get_histogram(self, name: str) -> Any:  # noqa: ANN401
        """Lazily create or return an OTel histogram."""
        if name not in self._histograms:
            self._histograms[name] = self._meter.create_histogram(
                name=name,
                description=f"tapps-brain histogram: {name}",
                unit="ms",
            )
        return self._histograms[name]

    def export(self, snapshot: MetricsSnapshot) -> None:
        """Export a snapshot to OpenTelemetry.

        Counters are emitted as OTel counter adds.  Histogram stats
        (min, max, mean, p50, p95, p99) are recorded as individual
        histogram observations so the OTel SDK can aggregate them.
        """
        for name, value in snapshot.counters.items():
            counter = self._get_counter(name)
            counter.add(value)

        for name, stats in snapshot.histograms.items():
            histogram = self._get_histogram(name)
            # Record representative values so the OTel SDK can compute aggregates
            if stats.count > 0:
                histogram.record(stats.mean)


def create_exporter(meter: Any = None) -> OTelExporter | None:  # noqa: ANN401
    """Create an exporter if OpenTelemetry is available.

    Returns ``None`` when the ``opentelemetry`` SDK is not installed.
    """
    if not feature_flags.otel:
        return None
    return OTelExporter(meter=meter)
