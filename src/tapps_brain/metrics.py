"""In-memory metrics collector for observability (EPIC-007).

Provides lightweight counters and histograms with no external dependencies.
Thread-safe via ``threading.Lock``. Zero-cost when not read — just atomic
counter increments and reservoir sampling.
"""

from __future__ import annotations

import math
import random
import threading
import time
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class HistogramStats(BaseModel):
    """Computed statistics from a histogram."""

    count: int = 0
    min: float = 0.0
    max: float = 0.0
    mean: float = 0.0
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0


class MetricsSnapshot(BaseModel):
    """Frozen snapshot of all counters and histograms."""

    counters: dict[str, int] = Field(default_factory=dict)
    histograms: dict[str, HistogramStats] = Field(default_factory=dict)
    captured_at: str = Field(default="")

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict for JSON serialization."""
        return self.model_dump(mode="json")


class StoreHealthReport(BaseModel):
    """Aggregate health view for a project memory store (EPIC-007)."""

    store_path: str = Field(description="Project root path backing the store.")
    entry_count: int = 0
    max_entries: int = 500
    schema_version: int = 0
    tier_distribution: dict[str, int] = Field(default_factory=dict)
    oldest_entry_age_days: float = 0.0
    consolidation_candidates: int = 0
    gc_candidates: int = 0
    federation_enabled: bool = False
    federation_project_count: int = 0


# ---------------------------------------------------------------------------
# Reservoir for histogram sampling
# ---------------------------------------------------------------------------

_RESERVOIR_SIZE = 1024


class _Reservoir:
    """Vitter's Algorithm R reservoir sampler for histogram data."""

    __slots__ = ("_count", "_max", "_min", "_samples", "_sum")

    def __init__(self) -> None:
        self._samples: list[float] = []
        self._count: int = 0
        self._min: float = math.inf
        self._max: float = -math.inf
        self._sum: float = 0.0

    def add(self, value: float) -> None:
        self._count += 1
        self._sum += value
        self._min = min(self._min, value)
        self._max = max(self._max, value)

        if len(self._samples) < _RESERVOIR_SIZE:
            self._samples.append(value)
        else:
            j = random.randint(0, self._count - 1)
            if j < _RESERVOIR_SIZE:
                self._samples[j] = value

    def stats(self) -> HistogramStats:
        if self._count == 0:
            return HistogramStats()

        sorted_samples = sorted(self._samples)
        n = len(sorted_samples)

        return HistogramStats(
            count=self._count,
            min=self._min,
            max=self._max,
            mean=self._sum / self._count,
            p50=sorted_samples[int(n * 0.5)] if n > 0 else 0.0,
            p95=sorted_samples[min(int(n * 0.95), n - 1)] if n > 0 else 0.0,
            p99=sorted_samples[min(int(n * 0.99), n - 1)] if n > 0 else 0.0,
        )


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Thread-safe in-memory metrics collector.

    Supports counters (increment) and histograms (observe) with no
    external dependencies. Use ``snapshot()`` to get a frozen copy.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._histograms: dict[str, _Reservoir] = {}

    def increment(self, name: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
        """Increment a counter by *value* (default 1)."""
        key = self._tagged_name(name, tags)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def observe(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a value in a histogram."""
        key = self._tagged_name(name, tags)
        with self._lock:
            if key not in self._histograms:
                self._histograms[key] = _Reservoir()
            self._histograms[key].add(value)

    def snapshot(self) -> MetricsSnapshot:
        """Return a frozen copy of all counters and histograms."""
        from datetime import UTC, datetime

        with self._lock:
            counters = dict(self._counters)
            histograms = {k: v.stats() for k, v in self._histograms.items()}

        return MetricsSnapshot(
            counters=counters,
            histograms=histograms,
            captured_at=datetime.now(tz=UTC).isoformat(),
        )

    def reset(self) -> None:
        """Clear all metrics (for testing)."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()

    @staticmethod
    def _tagged_name(name: str, tags: dict[str, str] | None) -> str:
        """Build a metric key from name and optional tags."""
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"


# ---------------------------------------------------------------------------
# Timer context manager for latency measurement
# ---------------------------------------------------------------------------


class MetricsTimer:
    """Context manager that records elapsed time to a histogram."""

    __slots__ = ("_collector", "_name", "_start", "_tags")

    def __init__(
        self, collector: MetricsCollector, name: str, tags: dict[str, str] | None = None
    ) -> None:
        self._collector = collector
        self._name = name
        self._tags = tags
        self._start: float = 0.0

    def __enter__(self) -> MetricsTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        self._collector.observe(self._name, elapsed_ms, self._tags)
