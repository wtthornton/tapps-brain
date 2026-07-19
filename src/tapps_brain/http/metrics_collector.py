"""Prometheus metrics collection for the tapps-brain HTTP adapter (TAP-604).

Extracted from ``tapps_brain.http_adapter``.
Renders ``/metrics`` exposition text and tracks per-tenant request counters.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import OrderedDict
from contextlib import suppress
from typing import Any, cast

from tapps_brain.http.probe_cache import _get_hive_pool_stats, _probe_db, _probe_experience_schema

# ---------------------------------------------------------------------------
# Per-(project_id, agent_id) request counters (STORY-070.12)
# ---------------------------------------------------------------------------

# STORY-070.12: bounded per-(project_id, agent_id) request counters for
# Prometheus export.  agent_id cardinality is capped at 100 distinct values
# per project; overflow is bucketed as "other".
_MAX_AGENT_ID_CARDINALITY: int = 100
# TAP-600: bounded project cardinality — evict least-recently-used projects
# when the project count exceeds this limit.  Default 10 000; override via
# TAPPS_BRAIN_MAX_PROJECT_CARDINALITY.  Zero or negative disables the cap
# (unbounded growth — use only in testing or single-tenant deployments).


def _parse_max_project_cardinality() -> int:
    import structlog as _structlog

    _log = _structlog.get_logger(__name__)
    raw = os.environ.get("TAPPS_BRAIN_MAX_PROJECT_CARDINALITY", "10000") or "10000"
    try:
        return int(raw)
    except ValueError:
        _log.warning(
            "http_adapter.max_project_cardinality.invalid",
            raw=raw,
            fallback=10000,
        )
        return 10000


_MAX_PROJECT_CARDINALITY: int = _parse_max_project_cardinality()
_LABELED_REQUEST_COUNTS: dict[tuple[str, str], int] = {}
_LABELED_REQUEST_COUNTS_LOCK: threading.Lock = threading.Lock()
# TAP-599: per-project set of seen agent_ids for O(1) cardinality checks.
# Maintained in lock-step with _LABELED_REQUEST_COUNTS inside the lock.
_DISTINCT_AGENTS_PER_PROJECT: dict[str, set[str]] = {}
# TAP-600: LRU order tracker — OrderedDict preserves insertion/access order.
# Keys are project_ids; values are None.  The *first* key is the LRU project.
_PROJECT_LRU: OrderedDict[str, None] = OrderedDict()
# TAP-600: cumulative count of project evictions since process start.
_TENANT_LABELS_EVICTED_TOTAL: int = 0


def _record_labeled_request(project_id: str, agent_id: str) -> None:
    """Increment the per-(project_id, agent_id) request counter (STORY-070.12).

    TAP-599: Uses a per-project set for O(1) membership/cardinality checks
    instead of an O(N) set-comprehension over the full _LABELED_REQUEST_COUNTS
    dict.  Both structures are updated inside the same lock so they stay in
    sync.

    TAP-600: Maintains a bounded LRU of active project_ids.  When the number
    of distinct projects exceeds ``_MAX_PROJECT_CARDINALITY`` the
    least-recently-used project is evicted from all in-memory counter
    structures and ``_TENANT_LABELS_EVICTED_TOTAL`` is incremented so
    operators can detect high project-churn via the Prometheus
    ``tapps_brain_tenant_labels_evicted_total`` counter.
    """
    global _TENANT_LABELS_EVICTED_TOTAL
    with _LABELED_REQUEST_COUNTS_LOCK:
        # --- TAP-600: LRU bookkeeping -----------------------------------
        if _MAX_PROJECT_CARDINALITY > 0:
            if project_id in _PROJECT_LRU:
                _PROJECT_LRU.move_to_end(project_id)
            else:
                if len(_PROJECT_LRU) >= _MAX_PROJECT_CARDINALITY:
                    lru_project, _ = _PROJECT_LRU.popitem(last=False)
                    evict_keys = [k for k in _LABELED_REQUEST_COUNTS if k[0] == lru_project]
                    for k in evict_keys:
                        del _LABELED_REQUEST_COUNTS[k]
                    _DISTINCT_AGENTS_PER_PROJECT.pop(lru_project, None)
                    _TENANT_LABELS_EVICTED_TOTAL += 1
                _PROJECT_LRU[project_id] = None
        # --- agent cardinality cap (TAP-599) ----------------------------
        distinct = _DISTINCT_AGENTS_PER_PROJECT.setdefault(project_id, set())
        if agent_id not in distinct and len(distinct) >= _MAX_AGENT_ID_CARDINALITY:
            agent_id = "other"
        key = (project_id, agent_id)
        _LABELED_REQUEST_COUNTS[key] = _LABELED_REQUEST_COUNTS.get(key, 0) + 1
        # Note: when agent_id was remapped to "other" the add below can grow
        # the set to _MAX_AGENT_ID_CARDINALITY + 1.  This is intentional —
        # subsequent overflow agents still bucket to "other" via the
        # `agent_id not in distinct` check, which evaluates False for "other".
        distinct.add(agent_id)


# ---------------------------------------------------------------------------
# TAP-2866: per-(path, status) HTTP error counters
# ---------------------------------------------------------------------------

# Exported to Prometheus as tapps_brain_http_errors_total so a data-plane
# endpoint that is failing (e.g. /v1/experience 500ing) is observable even
# while /health reads green — the exact gap that hid the TAP-2865 incident.
_HTTP_ERROR_COUNTS: dict[tuple[str, str], int] = {}
_HTTP_ERROR_COUNTS_LOCK = threading.Lock()


def _record_http_error(path: str, status: int) -> None:
    """Increment the per-(path, status) HTTP error counter (TAP-2866)."""
    key = (path, str(status))
    with _HTTP_ERROR_COUNTS_LOCK:
        _HTTP_ERROR_COUNTS[key] = _HTTP_ERROR_COUNTS.get(key, 0) + 1


# ---------------------------------------------------------------------------
# STORY-078.6: /snapshot build SLO metrics
# ---------------------------------------------------------------------------

#: Histogram bucket upper bounds (seconds) for cold snapshot builds.
_SNAPSHOT_BUILD_HISTOGRAM_BUCKETS: tuple[float, ...] = (0.1, 0.5, 1, 2, 5, 10, 30)


class _SnapshotBuildHistogram:
    """Thread-safe fixed-bucket histogram for ``build_visual_snapshot`` duration."""

    __slots__ = ("_bucket_counts", "_buckets", "_count", "_lock", "_sum")

    def __init__(self, buckets: tuple[float, ...]) -> None:
        self._buckets = buckets
        self._bucket_counts: list[int] = [0] * len(buckets)
        self._sum: float = 0.0
        self._count: int = 0
        self._lock = threading.Lock()

    def observe(self, value_seconds: float) -> None:
        with self._lock:
            self._sum += value_seconds
            self._count += 1
            for i, bound in enumerate(self._buckets):
                if value_seconds <= bound:
                    self._bucket_counts[i] += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "buckets": self._buckets,
                "bucket_counts": list(self._bucket_counts),
                "sum": self._sum,
                "count": self._count,
            }

    def reset(self) -> None:
        with self._lock:
            self._bucket_counts = [0] * len(self._buckets)
            self._sum = 0.0
            self._count = 0


_SNAPSHOT_BUILD_HIST: _SnapshotBuildHistogram = _SnapshotBuildHistogram(
    _SNAPSHOT_BUILD_HISTOGRAM_BUCKETS
)
_SNAPSHOT_CACHE_HITS_TOTAL: int = 0
_SNAPSHOT_METRICS_LOCK = threading.Lock()


def record_snapshot_cache_hit() -> None:
    """Increment TTL cache hit counter (no histogram observation)."""
    global _SNAPSHOT_CACHE_HITS_TOTAL
    with _SNAPSHOT_METRICS_LOCK:
        _SNAPSHOT_CACHE_HITS_TOTAL += 1


def record_snapshot_build_duration(seconds: float) -> None:
    """Record one cold ``build_visual_snapshot`` wall duration."""
    _SNAPSHOT_BUILD_HIST.observe(seconds)


def get_snapshot_metrics_snapshot() -> dict[str, object]:
    """Frozen copy of snapshot SLO metrics (tests only)."""
    with _SNAPSHOT_METRICS_LOCK:
        cache_hits = _SNAPSHOT_CACHE_HITS_TOTAL
    return {
        "build_histogram": _SNAPSHOT_BUILD_HIST.snapshot(),
        "cache_hits_total": cache_hits,
    }


def reset_snapshot_metrics_for_tests() -> None:
    """Clear snapshot metrics state (tests only)."""
    global _SNAPSHOT_CACHE_HITS_TOTAL
    with _SNAPSHOT_METRICS_LOCK:
        _SNAPSHOT_CACHE_HITS_TOTAL = 0
    _SNAPSHOT_BUILD_HIST.reset()


# ---------------------------------------------------------------------------
# Prometheus text rendering
# ---------------------------------------------------------------------------


def _emit_gauge(lines: list[str], name: str, value: float, help_text: str = "") -> None:
    """Append a single Prometheus gauge (HELP/TYPE/value) to *lines*."""
    if help_text:
        lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    lines.append(f"{name} {value}")


def _emit_process_gauges(lines: list[str], start: float) -> None:
    """Process start-time, uptime, and Python-version gauges."""
    _emit_gauge(
        lines,
        "tapps_brain_process_start_time_seconds",
        start,
        "Unix timestamp when tapps-brain HTTP adapter was started.",
    )
    _emit_gauge(
        lines,
        "tapps_brain_process_uptime_seconds",
        time.time() - start,
        "Seconds since tapps-brain HTTP adapter started.",
    )
    _emit_gauge(
        lines,
        "tapps_brain_python_info",
        1.0,
        f"Python version info (version={sys.version_info.major}.{sys.version_info.minor}).",
    )


def _emit_db_gauges(lines: list[str], dsn: str | None) -> None:
    """Database readiness + applied-migration-version gauges."""
    is_ready, migration_version, _ = _probe_db(dsn)
    _emit_gauge(
        lines,
        "tapps_brain_db_ready",
        1.0 if is_ready else 0.0,
        "1 if the configured Postgres database responded to a probe, 0 otherwise.",
    )
    if migration_version is not None:
        _emit_gauge(
            lines,
            "tapps_brain_db_migration_version",
            float(migration_version),
            "Highest applied Hive schema migration version.",
        )


def _emit_experience_gauge(lines: list[str], dsn: str | None) -> None:
    """TAP-2866: deep write-path readiness for the experience_events table."""
    experience_writable, _ = _probe_experience_schema(dsn)
    _emit_gauge(
        lines,
        "tapps_brain_experience_writable",
        1.0 if experience_writable else 0.0,
        "1 if the experience_events write path (table + partitions) is present, 0 otherwise.",
    )


def _emit_http_error_counters(lines: list[str]) -> None:
    """TAP-2866: per-(path, status) HTTP error counter.

    No tenant labels, so it is emitted in full regardless of redaction.
    Alert with ``tapps_brain_http_errors_total{status=~"5.."} > 0``.
    """
    with _HTTP_ERROR_COUNTS_LOCK:
        error_snapshot = dict(_HTTP_ERROR_COUNTS)
    lines.append(
        "# HELP tapps_brain_http_errors_total HTTP 4xx/5xx responses by path and status (TAP-2866)."
    )
    lines.append("# TYPE tapps_brain_http_errors_total counter")
    for (err_path, err_status), err_count in sorted(error_snapshot.items()):
        safe_path = err_path.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(
            f'tapps_brain_http_errors_total{{path="{safe_path}",status="{err_status}"}} {err_count}'
        )


def _emit_request_counters(lines: list[str], *, redact: bool) -> None:
    """STORY-070.12: per-(project_id, agent_id) MCP request counters.

    TAP-547: drop labels entirely when *redact* — still emit the counter so
    scrapers have a total-volume signal, just not per-tenant.

    TAP-600: also always emits ``tapps_brain_tenant_labels_evicted_total``
    (even at zero) — snapshotted under the same lock acquisition as the
    request counts so the two series are consistent with each other.
    """
    with _LABELED_REQUEST_COUNTS_LOCK:
        snapshot_evicted = _TENANT_LABELS_EVICTED_TOTAL
        snapshot_counts = dict(_LABELED_REQUEST_COUNTS)
    lines.append(
        "# HELP tapps_brain_tenant_labels_evicted_total "
        "Cumulative number of project_id entries evicted from the in-memory "
        "request-counter LRU cache (TAP-600). Non-zero indicates high "
        "project_id churn; consider increasing "
        "TAPPS_BRAIN_MAX_PROJECT_CARDINALITY."
    )
    lines.append("# TYPE tapps_brain_tenant_labels_evicted_total counter")
    lines.append(f"tapps_brain_tenant_labels_evicted_total {snapshot_evicted}")
    if not snapshot_counts:
        return
    lines.append(
        "# HELP tapps_brain_mcp_requests_total "
        "Total MCP requests, labelled by project_id and agent_id."
    )
    lines.append("# TYPE tapps_brain_mcp_requests_total counter")
    if redact:
        lines.append(f"tapps_brain_mcp_requests_total {sum(snapshot_counts.values())}")
        return
    for (pid, aid), count in sorted(snapshot_counts.items()):
        safe_pid = pid.replace('"', '\\"')
        safe_aid = aid.replace('"', '\\"')
        lines.append(
            f'tapps_brain_mcp_requests_total{{project_id="{safe_pid}",'
            f'agent_id="{safe_aid}"}} {count}'
        )


def _emit_tool_call_counters(lines: list[str], *, redact: bool) -> None:
    """STORY-070.12: per-(project_id, agent_id, tool, status) tool-call counters.

    TAP-547: when *redact*, aggregate over (project_id, agent_id) but keep
    (tool, status) — those are not tenant-identifying and remain useful for ops.
    """
    # suppress(Exception): any import or runtime error must not crash /metrics.
    with suppress(Exception):  # pragma: no cover
        from tapps_brain.otel_tracer import get_tool_call_counts_snapshot

        tool_counts = get_tool_call_counts_snapshot()
        if not tool_counts:
            return
        lines.append(
            "# HELP tapps_brain_tool_calls_total "
            "Total MCP tool invocations labelled by project_id, agent_id, tool, and status."
        )
        lines.append("# TYPE tapps_brain_tool_calls_total counter")
        if redact:
            aggregated: dict[tuple[str, str], int] = {}
            for (_pid, _aid, tool, status), count in tool_counts.items():
                key = (tool, status)
                aggregated[key] = aggregated.get(key, 0) + count
            for (tool, status), count in sorted(aggregated.items()):
                safe_tool = tool.replace('"', '\\"')
                safe_status = status.replace('"', '\\"')
                lines.append(
                    f'tapps_brain_tool_calls_total{{tool="{safe_tool}",'
                    f'status="{safe_status}"}} {count}'
                )
        else:
            for (pid, aid, tool, status), count in sorted(tool_counts.items()):
                safe_pid = pid.replace('"', '\\"')
                safe_aid = aid.replace('"', '\\"')
                safe_tool = tool.replace('"', '\\"')
                safe_status = status.replace('"', '\\"')
                lines.append(
                    f'tapps_brain_tool_calls_total{{project_id="{safe_pid}",'
                    f'agent_id="{safe_aid}",tool="{safe_tool}",'
                    f'status="{safe_status}"}} {count}'
                )


def _emit_session_gauge(lines: list[str], store: Any) -> None:  # noqa: ANN401
    """TAP-549: in-memory session-state cardinality gauge.

    Alertable signal for the "client rotates session_id every call" failure
    mode — should stay well below _SESSION_STATE_HARD_CAP (10_000) on a healthy
    adapter.  Always emit (even when None/0) so dashboards have a stable series.
    """
    if store is not None and hasattr(store, "active_session_count"):
        with suppress(Exception):
            # Best-effort gauge — a broken store must never crash /metrics.
            _emit_gauge(
                lines,
                "tapps_brain_store_active_sessions",
                float(store.active_session_count()),
                "Distinct session_ids tracked in MemoryStore in-memory "
                "implicit-feedback helper dicts.",
            )


def _emit_pool_gauges(lines: list[str], store: Any) -> None:  # noqa: ANN401
    """STORY-066.7: live pool stats from the hive connection manager."""
    _pool_stats = _get_hive_pool_stats(store)
    if not _pool_stats:
        return
    _emit_gauge(
        lines,
        "tapps_brain_pool_size",
        float(_pool_stats.get("pool_size", 0)),
        "Current number of open connections in the Hive pool.",
    )
    _emit_gauge(
        lines,
        "tapps_brain_pool_available",
        float(_pool_stats.get("pool_available", 0)),
        "Number of idle connections available in the Hive pool.",
    )
    _emit_gauge(
        lines,
        "tapps_brain_pool_saturation",
        float(_pool_stats.get("pool_saturation", 0.0)),
        "Fraction of Hive pool max_size currently in use (0.0-1.0).",
    )
    # TAP-729: expose whether live pool stats were successfully read.
    # 0.0 means the pool is not open or get_stats() raised — operators
    # can alert on this to detect observability gaps.
    _emit_gauge(
        lines,
        "tapps_brain_pool_stats_available",
        1.0 if _pool_stats.get("pool_stats_available") else 0.0,
        "1 if pool stats were successfully read from psycopg_pool; "
        "0 if the pool is not open or get_stats() raised.",
    )


def _emit_missing_index_counters(lines: list[str], *, redact: bool) -> None:
    """TAP-655: per-project counter for missing HNSW indexes detected at startup.

    Non-zero means migration 002 was not applied on that project's DB.
    TAP-547: drop project_id label when *redact* to prevent tenant enumeration.
    """
    # suppress(Exception): any import or runtime error must not crash /metrics.
    with suppress(Exception):  # pragma: no cover
        from tapps_brain.postgres_private import get_missing_index_counts_snapshot

        missing_idx_counts = get_missing_index_counts_snapshot()
        if not missing_idx_counts:
            return
        lines.append(
            "# HELP tapps_brain_private_missing_indexes_total "
            "Number of startup checks that found idx_priv_embedding_hnsw absent "
            "(migration 002 not applied). Non-zero means vector recall falls back "
            "to a sequential scan."
        )
        lines.append("# TYPE tapps_brain_private_missing_indexes_total counter")
        if redact:
            lines.append(
                f"tapps_brain_private_missing_indexes_total {sum(missing_idx_counts.values())}"
            )
            return
        for project_id, count in sorted(missing_idx_counts.items()):
            safe_pid = project_id.replace('"', '\\"')
            _name = "tapps_brain_private_missing_indexes_total"
            lines.append(f'{_name}{{project_id="{safe_pid}"}} {count}')


def _emit_profile_filter_metrics(lines: list[str]) -> None:
    """STORY-073.4: profile-filter metrics (bounded by profile count x tool count)."""
    # suppress(Exception): any import or runtime error must not crash /metrics.
    with suppress(Exception):  # pragma: no cover
        from tapps_brain.mcp_server.tool_filter import get_profile_filter_metrics_snapshot

        _filter_snap = get_profile_filter_metrics_snapshot()

        # mcp_tools_list_total{profile}
        list_total = _filter_snap.get("list_total", {})
        if list_total:
            lines.append(
                "# HELP tapps_brain_mcp_tools_list_total Total tools/list calls per MCP profile."
            )
            lines.append("# TYPE tapps_brain_mcp_tools_list_total counter")
            for _profile, _count in sorted(list_total.items()):
                _sp = _profile.replace('"', '\\"')
                lines.append(f'tapps_brain_mcp_tools_list_total{{profile="{_sp}"}} {_count}')

        # mcp_tools_list_visible_tools{profile} — gauge
        list_visible = _filter_snap.get("list_visible", {})
        if list_visible:
            lines.append(
                "# HELP tapps_brain_mcp_tools_list_visible_tools "
                "Last observed visible tool count per MCP profile after filtering."
            )
            lines.append("# TYPE tapps_brain_mcp_tools_list_visible_tools gauge")
            for _profile, _vis in sorted(list_visible.items()):
                _sp = _profile.replace('"', '\\"')
                lines.append(f'tapps_brain_mcp_tools_list_visible_tools{{profile="{_sp}"}} {_vis}')

        # mcp_tools_call_total{profile, tool, outcome}
        call_total = _filter_snap.get("call_total", {})
        if call_total:
            lines.append(
                "# HELP tapps_brain_mcp_tools_call_total "
                "Total tools/call attempts, labelled by profile, tool, and outcome."
            )
            lines.append("# TYPE tapps_brain_mcp_tools_call_total counter")
            for (_profile, _tool, _outcome), _count in sorted(call_total.items()):
                _sp = _profile.replace('"', '\\"')
                _st = _tool.replace('"', '\\"')
                _so = _outcome.replace('"', '\\"')
                lines.append(
                    f'tapps_brain_mcp_tools_call_total{{profile="{_sp}",'
                    f'tool="{_st}",outcome="{_so}"}} {_count}'
                )


def _emit_profile_resolver_metrics(lines: list[str]) -> None:
    """STORY-073.4: profile resolver resolution-source + cache metrics."""
    # suppress(Exception): any runtime error must not crash /metrics.
    with suppress(Exception):  # pragma: no cover
        # Import lazily to avoid circular dependency with profile_resolver module.
        from tapps_brain.http.profile_resolver import _PROFILE_RESOLVER

        _resolver = _PROFILE_RESOLVER
        if _resolver is None:
            return
        _res_stats = _resolver.resolution_stats()
        if _res_stats:
            lines.append(
                "# HELP tapps_brain_mcp_profile_resolution_source_total "
                "Profile resolution source per MCP request."
            )
            lines.append("# TYPE tapps_brain_mcp_profile_resolution_source_total counter")
            for _src, _count in sorted(_res_stats.items()):
                _ss = _src.replace('"', '\\"')
                _rn = "tapps_brain_mcp_profile_resolution_source_total"
                lines.append(f'{_rn}{{source="{_ss}"}} {_count}')

        _cache = _resolver.cache_stats()
        # Only emit if at least one cache event has occurred.
        if _cache.get("hits", 0) + _cache.get("misses", 0) + _cache.get("invalidated", 0) > 0:
            lines.append(
                "# HELP tapps_brain_mcp_profile_cache_events_total "
                "Profile resolver cache events (hit/miss/invalidated)."
            )
            lines.append("# TYPE tapps_brain_mcp_profile_cache_events_total counter")
            # Map result label → cache_stats() key; extend here when new event types land.
            _result_to_key = {"hit": "hits", "miss": "misses", "invalidated": "invalidated"}
            for _result, _key in _result_to_key.items():
                _count = _cache.get(_key, 0)
                if _count:
                    _cn = "tapps_brain_mcp_profile_cache_events_total"
                    lines.append(f'{_cn}{{result="{_result}"}} {_count}')


def _emit_snapshot_metrics(lines: list[str]) -> None:
    """STORY-078.6: snapshot build duration + TTL cache hit counters."""
    with _SNAPSHOT_METRICS_LOCK:
        cache_hits = _SNAPSHOT_CACHE_HITS_TOTAL
    build_snap = _SNAPSHOT_BUILD_HIST.snapshot()
    build_count = int(cast("int", build_snap.get("count", 0)))

    if cache_hits > 0:
        lines.append(
            "# HELP tapps_brain_snapshot_cache_hits_total "
            "Visual snapshot TTL cache hits (no rebuild)."
        )
        lines.append("# TYPE tapps_brain_snapshot_cache_hits_total counter")
        lines.append(f"tapps_brain_snapshot_cache_hits_total {cache_hits}")

    if build_count <= 0:
        return

    metric_name = "tapps_brain_snapshot_build_duration_seconds"
    lines.append(f"# HELP {metric_name} Wall-clock seconds for cold build_visual_snapshot calls.")
    lines.append(f"# TYPE {metric_name} histogram")
    buckets: tuple[float, ...] = build_snap.get("buckets", ())  # type: ignore[assignment]
    bucket_counts: list[int] = build_snap.get("bucket_counts", [])  # type: ignore[assignment]
    total_sum: float = build_snap.get("sum", 0.0)  # type: ignore[assignment]
    for bound, bcount in zip(buckets, bucket_counts, strict=True):
        le = f"{bound:g}"
        lines.append(f'{metric_name}_bucket{{le="{le}"}} {bcount}')
    lines.append(f'{metric_name}_bucket{{le="+Inf"}} {build_count}')
    lines.append(f"{metric_name}_sum {total_sum}")
    lines.append(f"{metric_name}_count {build_count}")


def _emit_probe_histogram(lines: list[str]) -> None:
    """TAP-1849: tapps_brain_mcp_probe_duration_seconds histogram.

    Tracks tools/list latency split by cache_hit label so operators can
    distinguish warm (cache-hit) from cold (cache-miss) probe durations.
    """
    # suppress(Exception): crash guard — a broken import must not crash /metrics.
    # No pragma: no cover here; this block is exercised by test_metrics.py.
    with suppress(Exception):
        from tapps_brain.mcp_server.tool_filter import get_probe_duration_histogram_snapshot

        _probe_snap = get_probe_duration_histogram_snapshot()
        # Only emit if at least one observation exists across either path.
        # ``snapshot["count"]`` is an int (see _ProbeHistogram.snapshot) but
        # the broader ``dict[str, object]`` shape erases that — cast for >.
        _has_probe_data = any(int(cast("int", s.get("count", 0))) > 0 for s in _probe_snap.values())
        if not _has_probe_data:
            return
        _metric_name = "tapps_brain_mcp_probe_duration_seconds"
        lines.append(
            f"# HELP {_metric_name} Duration in seconds of MCP tools/list probe calls,"
            " labelled by cache_hit (true=warm, false=cold)."
        )
        lines.append(f"# TYPE {_metric_name} histogram")
        # Emit in consistent order: false (cold) then true (warm).
        for _cache_hit_label in ("false", "true"):
            _snap = _probe_snap.get(_cache_hit_label, {})
            _buckets: tuple[float, ...] = _snap.get("buckets", ())  # type: ignore[assignment]
            _bucket_counts: list[int] = _snap.get("bucket_counts", [])  # type: ignore[assignment]
            _total_count: int = _snap.get("count", 0)  # type: ignore[assignment]
            _total_sum: float = _snap.get("sum", 0.0)  # type: ignore[assignment]
            _lbl = f'cache_hit="{_cache_hit_label}"'
            for _bound, _bcount in zip(_buckets, _bucket_counts, strict=True):
                # Format bucket boundary: strip trailing zeros for readability.
                _le = f"{_bound:g}"
                lines.append(f'{_metric_name}_bucket{{{_lbl},le="{_le}"}} {_bcount}')
            # +Inf bucket = total count
            lines.append(f'{_metric_name}_bucket{{{_lbl},le="+Inf"}} {_total_count}')
            lines.append(f"{_metric_name}_sum{{{_lbl}}} {_total_sum}")
            lines.append(f"{_metric_name}_count{{{_lbl}}} {_total_count}")


def _collect_metrics(
    dsn: str | None,
    store: Any = None,  # noqa: ANN401
    *,
    redact_tenant_labels: bool = False,
    process_start_time: float | None = None,
) -> str:
    """Render Prometheus exposition text.

    ``redact_tenant_labels`` (TAP-547): when ``True`` the per-tenant labels
    ``project_id`` and ``agent_id`` are dropped from
    ``tapps_brain_mcp_requests_total`` and ``tapps_brain_tool_calls_total``
    and the counters are aggregated across those dimensions.  This is the
    shape served to anonymous (or unauthenticated) scrapers so reachable-
    but-unprivileged callers cannot enumerate tenant/agent activity.

    ``process_start_time``: Unix timestamp of process start.  Callers should
    pass their module-level ``_PROCESS_START_TIME`` constant.  When omitted,
    ``time.time()`` is used (slightly off from the real start but harmless for
    tests that don't check the exact value).

    The body is a flat sequence of ``_emit_*`` block helpers (TAP-2758) so each
    metric group stays independently readable and low-complexity.
    """
    _start = process_start_time if process_start_time is not None else time.time()
    lines: list[str] = []

    _emit_process_gauges(lines, _start)
    _emit_db_gauges(lines, dsn)
    _emit_experience_gauge(lines, dsn)
    _emit_http_error_counters(lines)
    _emit_request_counters(lines, redact=redact_tenant_labels)
    _emit_tool_call_counters(lines, redact=redact_tenant_labels)
    _emit_session_gauge(lines, store)
    _emit_pool_gauges(lines, store)
    _emit_missing_index_counters(lines, redact=redact_tenant_labels)
    _emit_profile_filter_metrics(lines)
    _emit_profile_resolver_metrics(lines)
    _emit_probe_histogram(lines)
    _emit_snapshot_metrics(lines)

    lines.append("")
    return "\n".join(lines)
