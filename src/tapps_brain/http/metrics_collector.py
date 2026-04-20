"""Prometheus metrics collection for the tapps-brain HTTP adapter (TAP-604).

Extracted from ``tapps_brain.http_adapter``.
Renders ``/metrics`` exposition text and tracks per-tenant request counters.
"""

from __future__ import annotations

import sys
import threading
import time
from contextlib import suppress
from typing import Any

from tapps_brain.http.probe_cache import _get_hive_pool_stats, _probe_db

# ---------------------------------------------------------------------------
# Per-(project_id, agent_id) request counters (STORY-070.12)
# ---------------------------------------------------------------------------

# STORY-070.12: bounded per-(project_id, agent_id) request counters for
# Prometheus export.  agent_id cardinality is capped at 100 distinct values
# per project; overflow is bucketed as "other".
_MAX_AGENT_ID_CARDINALITY: int = 100
_LABELED_REQUEST_COUNTS: dict[tuple[str, str], int] = {}
_LABELED_REQUEST_COUNTS_LOCK: threading.Lock = threading.Lock()
# TAP-599: per-project set of seen agent_ids for O(1) cardinality checks.
# Maintained in lock-step with _LABELED_REQUEST_COUNTS inside the lock.
_DISTINCT_AGENTS_PER_PROJECT: dict[str, set[str]] = {}


def _record_labeled_request(project_id: str, agent_id: str) -> None:
    """Increment the per-(project_id, agent_id) request counter (STORY-070.12).

    TAP-599: Uses a per-project set for O(1) membership/cardinality checks
    instead of an O(N) set-comprehension over the full _LABELED_REQUEST_COUNTS
    dict.  Both structures are updated inside the same lock so they stay in
    sync.
    """
    with _LABELED_REQUEST_COUNTS_LOCK:
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
# Prometheus text rendering
# ---------------------------------------------------------------------------


def _collect_metrics(
    dsn: str | None,
    store: Any = None,
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
    """
    _start = process_start_time if process_start_time is not None else time.time()
    lines: list[str] = []

    def gauge(name: str, value: float, help_text: str = "") -> None:
        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    gauge(
        "tapps_brain_process_start_time_seconds",
        _start,
        "Unix timestamp when tapps-brain HTTP adapter was started.",
    )
    gauge(
        "tapps_brain_process_uptime_seconds",
        time.time() - _start,
        "Seconds since tapps-brain HTTP adapter started.",
    )
    gauge(
        "tapps_brain_python_info",
        1.0,
        f"Python version info (version={sys.version_info.major}.{sys.version_info.minor}).",
    )

    is_ready, migration_version, _ = _probe_db(dsn)
    gauge(
        "tapps_brain_db_ready",
        1.0 if is_ready else 0.0,
        "1 if the configured Postgres database responded to a probe, 0 otherwise.",
    )
    if migration_version is not None:
        gauge(
            "tapps_brain_db_migration_version",
            float(migration_version),
            "Highest applied Hive schema migration version.",
        )

    # STORY-070.12: per-(project_id, agent_id) request counters.
    # TAP-547: drop labels entirely when redacting — we still emit the
    # counter so scrapers have a total-volume signal, just not per-tenant.
    with _LABELED_REQUEST_COUNTS_LOCK:
        snapshot_counts = dict(_LABELED_REQUEST_COUNTS)
    if snapshot_counts:
        lines.append(
            "# HELP tapps_brain_mcp_requests_total "
            "Total MCP requests, labelled by project_id and agent_id."
        )
        lines.append("# TYPE tapps_brain_mcp_requests_total counter")
        if redact_tenant_labels:
            total = sum(snapshot_counts.values())
            lines.append(f"tapps_brain_mcp_requests_total {total}")
        else:
            for (pid, aid), count in sorted(snapshot_counts.items()):
                safe_pid = pid.replace('"', '\\"')
                safe_aid = aid.replace('"', '\\"')
                lines.append(
                    f'tapps_brain_mcp_requests_total{{project_id="{safe_pid}",'
                    f'agent_id="{safe_aid}"}} {count}'
                )

    # STORY-070.12: per-(project_id, agent_id, tool, status) tool call counters.
    # TAP-547: when redacting, aggregate over (project_id, agent_id) but
    # keep (tool, status) — those are not tenant-identifying and remain
    # useful for ops / alerting on anonymous scrapes.
    # suppress(Exception): any import or runtime error must not crash /metrics.
    with suppress(Exception):  # pragma: no cover
        from tapps_brain.otel_tracer import get_tool_call_counts_snapshot

        tool_counts = get_tool_call_counts_snapshot()
        if tool_counts:
            lines.append(
                "# HELP tapps_brain_tool_calls_total "
                "Total MCP tool invocations labelled by project_id, agent_id, tool, and status."
            )
            lines.append("# TYPE tapps_brain_tool_calls_total counter")
            if redact_tenant_labels:
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

    # TAP-549: in-memory session-state cardinality gauge.  Alertable
    # signal for the "client rotates session_id every call" failure mode
    # — the gauge should stay well below _SESSION_STATE_HARD_CAP (10_000)
    # on a healthy adapter; sustained growth means the sweep / eviction
    # isn't keeping up.  Always emit (even when None/0) so dashboards
    # have a stable series.
    if store is not None and hasattr(store, "active_session_count"):
        with suppress(Exception):
            # Best-effort gauge — a broken store must never crash /metrics.
            gauge(
                "tapps_brain_store_active_sessions",
                float(store.active_session_count()),
                "Distinct session_ids tracked in MemoryStore in-memory "
                "implicit-feedback helper dicts.",
            )

    # STORY-066.7: live pool stats from the hive connection manager.
    _pool_stats = _get_hive_pool_stats(store)
    if _pool_stats:
        gauge(
            "tapps_brain_pool_size",
            float(_pool_stats.get("pool_size", 0)),
            "Current number of open connections in the Hive pool.",
        )
        gauge(
            "tapps_brain_pool_available",
            float(_pool_stats.get("pool_available", 0)),
            "Number of idle connections available in the Hive pool.",
        )
        gauge(
            "tapps_brain_pool_saturation",
            float(_pool_stats.get("pool_saturation", 0.0)),
            "Fraction of Hive pool max_size currently in use (0.0-1.0).",
        )

    # TAP-655: per-project counter for missing HNSW indexes detected at startup.
    # Non-zero means migration 002 was not applied on that project's DB.
    # TAP-547: drop project_id label when redacting to prevent tenant enumeration.
    # suppress(Exception): any import or runtime error must not crash /metrics.
    with suppress(Exception):  # pragma: no cover
        from tapps_brain.postgres_private import get_missing_index_counts_snapshot

        missing_idx_counts = get_missing_index_counts_snapshot()
        if missing_idx_counts:
            lines.append(
                "# HELP tapps_brain_private_missing_indexes_total "
                "Number of startup checks that found idx_priv_embedding_hnsw absent "
                "(migration 002 not applied). Non-zero means vector recall falls back "
                "to a sequential scan."
            )
            lines.append("# TYPE tapps_brain_private_missing_indexes_total counter")
            if redact_tenant_labels:
                total = sum(missing_idx_counts.values())
                lines.append(f"tapps_brain_private_missing_indexes_total {total}")
            else:
                for project_id, count in sorted(missing_idx_counts.items()):
                    safe_pid = project_id.replace('"', '\\"')
                    lines.append(
                        f'tapps_brain_private_missing_indexes_total{{project_id="{safe_pid}"}} {count}'
                    )

    # STORY-073.4: profile-filter metrics (cardinality bounded by profile count × tool count).
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
                lines.append(
                    f'tapps_brain_mcp_tools_list_visible_tools{{profile="{_sp}"}} {_vis}'
                )

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

    # STORY-073.4: profile resolver resolution-source + cache metrics.
    # suppress(Exception): any runtime error must not crash /metrics.
    with suppress(Exception):  # pragma: no cover
        # Import lazily to avoid circular dependency with profile_resolver module.
        from tapps_brain.http.profile_resolver import _PROFILE_RESOLVER

        _resolver = _PROFILE_RESOLVER
        if _resolver is not None:
            _res_stats = _resolver.resolution_stats()
            if _res_stats:
                lines.append(
                    "# HELP tapps_brain_mcp_profile_resolution_source_total "
                    "Profile resolution source per MCP request."
                )
                lines.append("# TYPE tapps_brain_mcp_profile_resolution_source_total counter")
                for _src, _count in sorted(_res_stats.items()):
                    _ss = _src.replace('"', '\\"')
                    lines.append(
                        f'tapps_brain_mcp_profile_resolution_source_total{{source="{_ss}"}} {_count}'
                    )

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
                        lines.append(
                            f'tapps_brain_mcp_profile_cache_events_total{{result="{_result}"}} {_count}'
                        )

    lines.append("")
    return "\n".join(lines)
