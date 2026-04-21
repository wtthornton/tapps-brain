"""In-memory metrics collector for observability (EPIC-007).

Provides lightweight counters and histograms with no external dependencies.
Thread-safe via ``threading.Lock``. Zero-cost when not read — just atomic
counter increments and reservoir sampling.

``MemoryStore.save`` also records phase histograms (milliseconds) under
``store.save.phase.*``: ``lock_build_ms``, ``persist_ms``, ``relations_ms``;
optional ``embed_ms``, ``hive_ms``, and ``consolidate_ms`` when those paths run.
Expose via ``memory://metrics`` (MCP) or ``MemoryStore.get_metrics()``.
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
    """Frozen snapshot of all counters, histograms, and gauges.

    Gauges (``gauges`` field) hold point-in-time float readings — for example
    Postgres pool utilisation.  They are exported as OTel observable up-down
    counters and are reset to ``{}`` between snapshots (not cumulative).
    """

    counters: dict[str, int] = Field(default_factory=dict)
    histograms: dict[str, HistogramStats] = Field(default_factory=dict)
    gauges: dict[str, float] = Field(
        default_factory=dict,
        description="Point-in-time float readings (e.g. pool in-use connections).",
    )
    captured_at: str = Field(default="")

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict for JSON serialization."""
        return self.model_dump(mode="json")


_SAVE_PHASE_HIST_KEYS: tuple[str, ...] = (
    "store.save.phase.lock_build_ms",
    "store.save.phase.embed_ms",
    "store.save.phase.persist_ms",
    "store.save.phase.hive_ms",
    "store.save.phase.relations_ms",
    "store.save.phase.consolidate_ms",
)


def compact_save_phase_summary(snap: MetricsSnapshot) -> str:
    """One-line p50 + sample counts for ``MemoryStore.save`` sub-phases (operators / health)."""
    parts: list[str] = []
    for full in _SAVE_PHASE_HIST_KEYS:
        st = snap.histograms.get(full)
        if st is None or st.count <= 0:
            continue
        short = full.removeprefix("store.save.phase.")
        parts.append(f"{short} p50={st.p50:.1f}ms n={st.count}")
    return "; ".join(parts)


class StoreHealthReport(BaseModel):
    """Aggregate health view for a project memory store (EPIC-007)."""

    store_path: str = Field(description="Project root path backing the store.")
    entry_count: int = 0
    max_entries: int = 5000
    max_entries_per_group: int | None = Field(
        default=None,
        description="``MemoryProfile.limits.max_entries_per_group`` when set (STORY-044.7).",
    )
    schema_version: int = 0
    package_version: str = Field(
        default="",
        description="Installed tapps-brain distribution version (PEP 440).",
    )
    profile_name: str | None = Field(
        default=None,
        description="Active MemoryProfile name when a profile is loaded.",
    )
    profile_seed_version: str | None = Field(
        default=None,
        description=(
            "``MemoryProfile.seeding.seed_version`` when set (EPIC-044 STORY-044.6); "
            "operators use with seed/reseed summaries."
        ),
    )
    tier_distribution: dict[str, int] = Field(default_factory=dict)
    oldest_entry_age_days: float = 0.0
    consolidation_candidates: int = 0
    gc_candidates: int = 0
    federation_enabled: bool = False
    federation_project_count: int = 0
    # Integrity verification (H4c)
    integrity_verified: int = 0
    integrity_tampered: int = 0
    integrity_no_hash: int = 0
    integrity_tampered_keys: list[str] = Field(default_factory=list)
    # Rate limiter anomaly counts (H6c)
    rate_limit_minute_anomalies: int = 0
    rate_limit_lifetime_anomalies: int = 0
    rate_limit_total_writes: int = 0
    rate_limit_exempt_writes: int = 0
    # Relation graph (M3)
    relation_count: int = 0
    # Save-path phase latencies (EPIC-051.6); empty when no samples since process start
    save_phase_summary: str = Field(
        default="",
        description="Compact p50 ms + n for store.save phase histograms (see get_metrics).",
    )
    # RAG safety counters (EPIC-044 STORY-044.1); since process start, same collector as save phases
    rag_safety_ruleset_version: str = Field(
        default="",
        description="Effective bundled RAG safety pattern ruleset semver for this store profile.",
    )
    rag_safety_blocked_count: int = Field(
        default=0,
        description="Times content was fully blocked (save or injection check).",
    )
    rag_safety_sanitized_count: int = Field(
        default=0,
        description="Times content was sanitised (redacted patterns) rather than blocked.",
    )
    # GC counters (EPIC-044 STORY-044.5); since process start, same collector as save phases
    gc_runs_total: int = Field(
        default=0,
        description="Number of ``MemoryStore.gc`` invocations (including dry-run).",
    )
    gc_archived_rows_total: int = Field(
        default=0,
        description="Total rows archived across live GC runs.",
    )
    gc_archive_bytes_total: int = Field(
        default=0,
        description="Total UTF-8 bytes appended to archive JSONL across live GC runs.",
    )
    # TAP-549: distinct session_ids currently tracked in the in-memory
    # implicit-feedback helper dicts.  Unbounded growth (e.g. a client
    # rotating ``session_id`` on every call) previously slow-burned the
    # adapter toward OOM; exposed here so operators can alert on growth.
    active_session_count: int = Field(
        default=0,
        description=(
            "Distinct session_ids tracked in MemoryStore session-state "
            "helper dicts.  Bounded by MemoryStore's LRU cap and the "
            "GC-driven stale-session sweep (TAP-549)."
        ),
    )
    # TAP-726: Bloom filter saturation (approximate FP rate at current count).
    # Exposed so operators can alert when the filter degrades past a threshold
    # (e.g. > 0.10 = dedup fast-path is providing little benefit).
    bloom_saturation: float = Field(
        default=0.0,
        description=(
            "Approximate false-positive rate of the write-path Bloom filter "
            "at its current insertion count (TAP-726).  Near 0.01 at design "
            "load; approaches 1.0 when the filter is saturated."
        ),
    )
    # Hive Postgres health (EPIC-058 STORY-058.3)
    hive_connected: bool = False
    hive_schema_version: int = 0
    hive_schema_current: bool = True
    hive_pool_size: int = 0
    hive_pool_available: int = 0
    hive_latency_ms: float = 0.0


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
        self._gauges: dict[str, float] = {}

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

    def set_gauge(self, name: str, value: float) -> None:
        """Set a point-in-time gauge reading (e.g. pool in-use connections).

        Unlike counters and histograms, gauges are **not** cumulative.  Each
        :meth:`snapshot` call replaces the previous value.  Use for quantities
        that go up *and* down — pool utilisation, entry count, queue depth.

        .. warning::
            Gauge names must never contain raw user content.  Only use
            short, fixed metric names (see ``ALLOWED_METRIC_DIMENSIONS`` in
            ``otel_exporter``).
        """
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> MetricsSnapshot:
        """Return a frozen copy of all counters, histograms, and gauges."""
        from datetime import UTC, datetime

        with self._lock:
            counters = dict(self._counters)
            histograms = {k: v.stats() for k, v in self._histograms.items()}
            gauges = dict(self._gauges)

        return MetricsSnapshot(
            counters=counters,
            histograms=histograms,
            gauges=gauges,
            captured_at=datetime.now(tz=UTC).isoformat(),
        )

    def reset(self) -> None:
        """Clear all metrics (for testing)."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            self._gauges.clear()

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


# ---------------------------------------------------------------------------
# STORY-073.4: Profile-filter metric name constants
# ---------------------------------------------------------------------------

#: Counter — incremented on every ``tools/list`` call, labelled by profile.
MCP_TOOLS_LIST_TOTAL = "mcp_tools_list_total"

#: Gauge — last observed visible-tool count per profile after filtering.
MCP_TOOLS_LIST_VISIBLE_TOOLS = "mcp_tools_list_visible_tools"

#: Counter — tool invocations labelled by profile, tool, and outcome.
#: ``outcome`` ∈ ``{allowed, denied_profile, error}``.
MCP_TOOLS_CALL_TOTAL = "mcp_tools_call_total"

#: Counter — profile resolution source per request.
#: ``source`` ∈ ``{header, agent_registry, default}``.
MCP_PROFILE_RESOLUTION_SOURCE_TOTAL = "mcp_profile_resolution_source_total"

#: Counter — profile resolver cache events.
#: ``result`` ∈ ``{hit, miss, invalidated}``.
MCP_PROFILE_CACHE_EVENTS_TOTAL = "mcp_profile_cache_events_total"
