"""Memory-domain service functions (EPIC-070 STORY-070.1).

All functions return JSON-serialisable Python objects (dict / list / str / int /
bool). Wrappers in ``mcp_server`` / ``http_adapter`` are responsible for
``json.dumps`` and request-context resolution.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from pydantic import ValidationError as _PydanticValidationError

from tapps_brain.agent_brain import _content_key
from tapps_brain.agent_scope import agent_scope_valid_values_for_errors, normalize_agent_scope
from tapps_brain.memory_group import MEMORY_GROUP_UNSET
from tapps_brain.models import MemoryStatus, MemoryTier, tier_str
from tapps_brain.otel_tracer import start_mcp_tool_span
from tapps_brain.services._common import _MAX_CONFIDENCE_BOOST, validate_iso_timestamp
from tapps_brain.tier_normalize import normalize_save_tier

logger = structlog.get_logger(__name__)


def _save_rejection(result: Any) -> dict[str, Any] | None:
    """If ``MemoryStore.save`` returned an error dict, normalise it for callers."""
    if isinstance(result, dict) and result.get("error"):
        detail = str(result.get("detail") or result.get("message") or result.get("reason") or "")
        return {
            "error": str(result.get("error")),
            "detail": detail,
            "message": detail or str(result.get("error")),
        }
    return None


# ---------------------------------------------------------------------------
# brain_* simplified Agent Brain tools (EPIC-057)
# ---------------------------------------------------------------------------


def brain_remember(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    fact: str,
    tier: str = "procedural",
    share: bool = False,
    share_with: str = "",
    agent_scope: str = "",
    memory_group: str = "",
    temporal_sensitivity: str | None = None,
    failed_approaches: list[str] | None = None,
    supersedes: str | None = None,
) -> dict[str, Any]:
    """Save a memory and optionally supersede an existing entry.

    Scope precedence (TAP-989): an explicit ``agent_scope`` (one of
    ``"private"`` / ``"domain"`` / ``"hive"`` / ``"group:<name>"``) wins over
    the legacy ``share`` / ``share_with`` derivation. When ``agent_scope`` is
    empty, the legacy params are derived as before for back-compat.

    ``memory_group`` is a project-local partition (orthogonal to the Hive
    scope axis) — defaults to unset.

    When *supersedes* is a key of an existing entry, that entry is marked
    ``status=superseded`` with ``superseded_by`` pointing to the new key.

    When *supersedes* is not provided but an existing active entry shares
    the word-prefix of the new key, a ``supersession_candidate`` key is
    returned in the response so the caller can confirm with a follow-up call.
    """
    with start_mcp_tool_span("brain_remember", extra_attributes={"memory.tier": tier}):
        key = _content_key(fact)

        # TAP-989: explicit agent_scope wins over legacy share / share_with.
        # Legacy derivation only kicks in when agent_scope is empty (default).
        resolved_scope: str
        if agent_scope:
            try:
                resolved_scope = normalize_agent_scope(agent_scope)
            except ValueError as exc:
                return {
                    "error": "invalid_agent_scope",
                    "message": str(exc),
                    "valid_values": agent_scope_valid_values_for_errors(),
                }
        else:
            resolved_scope = "private"
            if share:
                resolved_scope = "group"
            elif share_with == "hive":
                resolved_scope = "hive"
            elif share_with:
                resolved_scope = f"group:{share_with}"

        save_kwargs: dict[str, Any] = {
            "key": key,
            "value": fact,
            "tier": tier,
            "agent_scope": resolved_scope,
            "temporal_sensitivity": temporal_sensitivity,
            "failed_approaches": failed_approaches,
            "status": MemoryStatus.active.value,
        }
        if memory_group:
            save_kwargs["memory_group"] = memory_group
        # Save the new entry first. Marking supersedes beforehand orphans the
        # prior row when this save is rejected (safety / write-rules).
        result = store.save(**save_kwargs)
        if isinstance(result, dict) and "error" in result:
            return result

        response: dict[str, Any] = {"saved": True, "key": key}

        if supersedes:
            old_entry = store.get(supersedes)
            if old_entry is not None:
                # Re-save preserves the historical record's provenance —
                # save() constructs a fresh MemoryEntry, so omitting tags/
                # source/scope would silently reset them to defaults.
                store.save(
                    key=old_entry.key,
                    value=old_entry.value,
                    tier=str(old_entry.tier),
                    source=old_entry.source.value,
                    source_agent=old_entry.source_agent,
                    scope=old_entry.scope.value,
                    tags=list(old_entry.tags),
                    branch=old_entry.branch,
                    agent_scope=old_entry.agent_scope,
                    status=MemoryStatus.superseded.value,
                    superseded_by=key,
                    skip_consolidation=True,
                    conflict_check=False,
                    dedup=False,
                )
            response["superseded"] = supersedes
            return response

        # -------------------------------------------------------
        # Supersession candidate detection: check whether any active
        # entry shares the word-prefix portion of the new key.
        # -------------------------------------------------------
        candidate = _find_supersession_candidate(store, key)
        if candidate:
            response["supersession_candidate"] = candidate

        return response


def _find_supersession_candidate(store: Any, new_key: str) -> str | None:
    """Return the key of an active entry that shares the word-prefix with *new_key*.

    ``_content_key`` produces keys of the form ``{word-slug}-{16hexchars}``.
    We extract the word-slug prefix and look for existing active entries whose
    key begins with that same prefix (and differs from *new_key*).

    This is a best-effort *advisory* hint decorating an already-persisted
    save: it scans only the in-memory cache (no durable load_all merge) and
    never raises — a failure here must not convert a successful save into a
    tool error.

    Returns the first matching key, or ``None``.
    """
    # Extract word-prefix: strip the trailing "-{16hexchars}" hash suffix.
    _hash_suffix = re.compile(r"-[0-9a-f]{16}$")
    prefix = _hash_suffix.sub("", new_key)
    if prefix == new_key:
        # Key has no recognisable hash suffix — skip detection.
        return None

    try:
        all_entries = store.list_all(include_superseded=False)
    except Exception:
        logger.warning("prefix_duplicate_list_failed", new_key=new_key, exc_info=True)
        return None

    for entry in all_entries:
        if entry.key == new_key:
            continue
        entry_status = getattr(entry, "status", MemoryStatus.active)
        if entry_status != MemoryStatus.active:
            continue
        if entry.key.startswith(prefix):
            return str(entry.key)
    return None


def brain_recall(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    query: str,
    max_results: int = 5,
    include_stale: bool = False,
    filter_tier: str | None = None,
    filter_tags: list[str] | None = None,
    filter_tags_any: list[str] | None = None,
    filter_memory_class: str | None = None,
) -> list[Any]:
    """Recall memories matching a query with optional structured pre-filters (TAP-733).

    By default, entries with ``status=stale`` or ``status=superseded`` are
    excluded.  Pass ``include_stale=True`` to include them (useful for
    diagnostic or audit queries).

    Args:
        store: MemoryStore instance.
        project_id: Project identifier.
        agent_id: Agent identifier.
        query: Search query string.
        max_results: Maximum number of results to return.
        include_stale: Include stale/superseded entries in results.
        filter_tier: Restrict to entries with this tier (e.g. ``"architectural"``).
        filter_tags: ALL tags must be present on each matching entry.
        filter_tags_any: ANY one of these tags must be present.
        filter_memory_class: Restrict to entries with this semantic class
            (``"incident"``, ``"guidance"``, ``"decision"``, ``"convention"``).
    """
    _excluded_statuses = {MemoryStatus.stale, MemoryStatus.superseded, MemoryStatus.archived}

    with start_mcp_tool_span("brain_recall"):
        entries = store.search(
            query,
            tier=filter_tier,
            tags=filter_tags_any or None,  # store.search tags= is OR (any)
            memory_class=filter_memory_class,
        )
        # Apply ALL-tags filter in Python (store.search tags= uses OR semantics)
        if filter_tags:
            entries = [e for e in entries if all(t in e.tags for t in filter_tags)]
        results: list[Any] = []
        for entry in entries:
            if len(results) >= max_results:
                break
            if isinstance(entry, dict):
                # Plain-dict path (legacy): no status field available; include by default.
                results.append(entry)
            else:
                if not include_stale:
                    entry_status = getattr(entry, "status", MemoryStatus.active)
                    if entry_status in _excluded_statuses:
                        continue
                item: dict[str, Any] = {
                    "key": entry.key,
                    "value": entry.value,
                    "tier": str(entry.tier),
                    "confidence": entry.confidence,
                    "tags": list(entry.tags) if entry.tags else [],
                }
                if getattr(entry, "memory_class", None) is not None:
                    item["memory_class"] = entry.memory_class
                failed = getattr(entry, "failed_approaches", None)
                if failed:
                    item["failed_approaches"] = list(failed)
                # Surface stale/superseded status when include_stale=True so
                # diagnostic callers can see why an entry was normally filtered out.
                entry_status = getattr(entry, "status", MemoryStatus.active)
                if entry_status != MemoryStatus.active:
                    item["status"] = str(entry_status)
                    stale_reason = getattr(entry, "stale_reason", None)
                    if stale_reason:
                        item["stale_reason"] = stale_reason
                results.append(item)
        return results


def brain_forget(store: Any, project_id: str, agent_id: str, *, key: str) -> dict[str, Any]:
    """Archive-then-delete a memory entry by key.

    Both public surfaces (the ``brain_forget`` MCP tool and ``POST /v1/forget``)
    promise the entry is "not permanently deleted", so the row is written to
    the ``gc_archive`` table (same recoverability model as GC eviction) before
    it is removed from the active store.

    Returns ``{"forgotten": True, "key": key}`` on success or
    ``{"forgotten": False, "reason": "not_found"}`` when the key is unknown.
    """
    with start_mcp_tool_span("brain_forget"):
        entry = store.get(key)
        if entry is None:
            return {"forgotten": False, "reason": "not_found"}
        _archive_forgotten_entry(getattr(store, "_persistence", None), entry, key)
        store.delete(key)
        return {"forgotten": True, "key": key}


def _archive_forgotten_entry(backend: Any, entry: Any, key: str) -> None:
    """Best-effort ``gc_archive`` write backing brain_forget's recoverability.

    Archive failure is logged but never blocks the forget — the caller
    explicitly asked for removal.
    """
    archive = getattr(backend, "archive_entry", None)
    if not callable(archive):
        return
    if not archive(entry):
        logger.warning("brain_forget.archive_failed", key=key)


def brain_learn_success(
    store: Any, project_id: str, agent_id: str, *, task_description: str, task_id: str = ""
) -> dict[str, Any]:
    """Record a successful task outcome as a ``procedural``-tier memory.

    The key is derived from a content hash of the description so identical
    descriptions deduplicate. Adds ``success`` and optional ``task:<id>`` tags.
    """
    with start_mcp_tool_span("brain_learn_success"):
        key = _content_key(f"success-{task_description}")
        tags = ["success"]
        if task_id:
            tags.append(f"task:{task_id}")
        out = store.save(key=key, value=task_description, tier="procedural", tags=tags)
        rejected = _save_rejection(out)
        if rejected is not None:
            return {**rejected, "learned": False, "key": key}
        return {"learned": True, "key": key}


def brain_learn_failure(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    description: str,
    task_id: str = "",
    error: str = "",
) -> dict[str, Any]:
    """Record a failed task outcome as a ``procedural``-tier memory.

    Key derives from a content hash; optional ``error`` is appended to the
    stored value. Tagged with ``failure`` and optional ``task:<id>``.
    """
    with start_mcp_tool_span("brain_learn_failure"):
        key = _content_key(f"failure-{description}")
        value = f"{description}\n\nError: {error}" if error else description
        tags = ["failure"]
        if task_id:
            tags.append(f"task:{task_id}")
        out = store.save(key=key, value=value, tier="procedural", tags=tags)
        rejected = _save_rejection(out)
        if rejected is not None:
            return {**rejected, "learned": False, "key": key}
        return {"learned": True, "key": key}


def brain_status(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    """Return the current agent's identity, group membership, and memory count.

    Includes ``hive_connected`` so callers can detect when a Hive backend is
    unavailable. ``memory_count`` counts *active* entries (matching what
    ``memory_list`` shows by default) and triggers a durable-entry merge, so
    this call does hit Postgres on a cold cache.
    """
    return {
        "agent_id": getattr(store, "agent_id", None),
        "groups": getattr(store, "groups", []),
        "expert_domains": getattr(store, "expert_domains", []),
        "memory_count": len(store.list_all(include_superseded=False)),
        "hive_connected": store._hive_store is not None,
    }


def audit_consumers(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    target_project_id: str = "",
    since: str = "",
) -> dict[str, Any]:
    """Cross-reference registered agents with observed ``brain_*`` tool calls.

    Joins two data sources that already exist independently:

    - :class:`~tapps_brain.backends.AgentRegistry` — agents declared in the
      YAML-backed registry (``~/.tapps-brain/hive/agents.yaml``).
    - :func:`~tapps_brain.otel_tracer.get_tool_call_counts_snapshot` — the
      per-``(project_id, agent_id, tool, status)`` counter populated by
      STORY-070.12 on every ``start_mcp_tool_span`` invocation.

    The counter is cumulative since process start (no time-window snapshots
    are persisted), so the *since* parameter is validated for shape but does
    not actually filter — the response is always "since process start" and
    the effective window is reported in ``window_effective`` for honesty.

    Args:
        target_project_id: Project to audit.  Defaults to the caller's
            contextvar-resolved ``project_id`` when empty.
        since: Optional ISO-8601 timestamp.  Validated for shape; recorded in
            the response.  Real windowed filtering is future work — see
            :issue:`TAP-2092`.

    Returns:
        Dict with keys ``declared_silent``, ``active``, ``unregistered_active``,
        ``as_of``, ``project_id``, ``window_effective``, ``since_requested``.
        On ``since`` parse failure: ``{"error": "invalid_since", ...}``.
    """
    if since:
        try:
            datetime.fromisoformat(since)
        except ValueError:
            return {
                "error": "invalid_since",
                "message": f"since must be a valid ISO-8601 timestamp, got {since!r}",
            }

    effective_pid = target_project_id or project_id

    from tapps_brain.backends import resolve_agent_registry
    from tapps_brain.otel_tracer import get_tool_call_counts_snapshot

    registry = resolve_agent_registry(getattr(store, "_hive_store", None))
    registered_ids = {a.id for a in registry.list_agents()}

    counts = get_tool_call_counts_snapshot()
    per_agent: dict[str, dict[str, int]] = {}
    for (pid, aid, tool, _status), n in counts.items():
        if pid != effective_pid:
            continue
        if not tool.startswith("brain_"):
            continue
        per_agent.setdefault(aid, {})
        per_agent[aid][tool] = per_agent[aid].get(tool, 0) + n

    active = sorted(
        (
            {
                "agent_id": aid,
                "total_calls": sum(tools.values()),
                "tools": dict(sorted(tools.items())),
            }
            for aid, tools in per_agent.items()
        ),
        key=lambda r: (-cast("int", r["total_calls"]), cast("str", r["agent_id"])),
    )

    active_ids = set(per_agent.keys())
    declared_silent = sorted(registered_ids - active_ids)
    unregistered_active = sorted(active_ids - registered_ids)

    return {
        "project_id": effective_pid,
        "declared_silent": declared_silent,
        "active": active,
        "unregistered_active": unregistered_active,
        "as_of": datetime.now(tz=UTC).isoformat(),
        "since_requested": since,
        "window_effective": "process_start",
    }


def recall_quality_metrics(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    window_seconds: int = 3600,
    target_project_id: str = "",
) -> dict[str, Any]:
    """Aggregate recent recall-quality samples from the in-process ring buffer.

    Reads :mod:`tapps_brain.recall_quality_buffer` snapshots and computes
    p50 / p95 percentiles over the last *window_seconds* seconds for
    ``top_score`` and ``oldest_returned_age_days``, plus the empty-recall
    rate.  Returns ``sample_count == 0`` when no samples fall in the window.

    Args:
        window_seconds: Lookback window in seconds (must be > 0).  Samples
            older than ``now - window_seconds`` are excluded.
        target_project_id: Project to aggregate over.  Defaults to the
            caller's contextvar-resolved project when empty.

    Returns:
        Dict with keys ``p50_top_score``, ``p95_top_score``,
        ``p50_oldest_age_days``, ``p95_oldest_age_days``, ``empty_recall_rate``,
        ``sample_count``, ``window_seconds``, ``project_id``, ``as_of``.
        Percentile fields are ``None`` when the relevant sub-sample is empty.
        On invalid *window_seconds*: ``{"error": "invalid_window", ...}``.
    """
    if window_seconds <= 0:
        return {
            "error": "invalid_window",
            "message": f"window_seconds must be > 0, got {window_seconds!r}",
        }

    effective_pid = target_project_id or project_id

    import time as _time

    from tapps_brain import recall_quality_buffer

    cutoff = _time.time() - float(window_seconds)
    samples = [s for s in recall_quality_buffer.snapshot(effective_pid) if s.timestamp >= cutoff]
    sample_count = len(samples)

    top_scores = sorted(s.top_score for s in samples if s.top_score is not None)
    oldest_ages = sorted(
        s.oldest_returned_age_days for s in samples if s.oldest_returned_age_days is not None
    )
    empty_count = sum(1 for s in samples if s.memory_count == 0)
    empty_rate = (empty_count / sample_count) if sample_count else 0.0

    return {
        "project_id": effective_pid,
        "window_seconds": int(window_seconds),
        "sample_count": sample_count,
        "p50_top_score": _percentile(top_scores, 50.0),
        "p95_top_score": _percentile(top_scores, 95.0),
        "p50_oldest_age_days": _percentile(oldest_ages, 50.0),
        "p95_oldest_age_days": _percentile(oldest_ages, 95.0),
        "empty_recall_rate": round(empty_rate, 4),
        "as_of": datetime.now(tz=UTC).isoformat(),
    }


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Return the *pct*-th percentile of *sorted_values* (linear interpolation).

    Expects *sorted_values* to be already ascending.  Returns ``None`` for an
    empty list.  Used by :func:`recall_quality_metrics` — kept here rather than
    importing ``statistics.quantiles`` to keep behaviour deterministic for
    tiny samples (n < 2).
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


# ---------------------------------------------------------------------------
# brain_export — Managed Agents-layout snapshot exporter (TAP-2099)
# ---------------------------------------------------------------------------

_EXPORT_SCHEMA_VERSION: int = 1
_EXPORT_VALID_LAYOUTS: frozenset[str] = frozenset({"managed-agents", "okf"})
_OKF_VERSION: str = "0.1"
_EXPORT_READONLY_BANNER: str = "<!-- READ-ONLY managed by tapps-brain. Edits ignored. -->"
_EXPORT_SECRET_TAG: str = "secret"

# Redaction patterns — applied in order; replacement carries the kind so
# the surviving text still hints at what was scrubbed.
_REDACTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"AKIA[0-9A-Z]{16}", "[REDACTED:aws-key]"),
    (r"gh[pousr]_[A-Za-z0-9_]{30,}", "[REDACTED:gh-token]"),
    (r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED:jwt]"),
    (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[REDACTED:email]"),
)


def _redact_value(text: str) -> tuple[str, int]:
    """Apply the redaction pattern set to *text*; return (clean_text, hit_count)."""
    redacted = text
    hits = 0
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted, n = re.subn(pattern, replacement, redacted)
        hits += n
    return redacted, hits


def _resolve_confidence(entry: Any) -> float:
    """Return the effective 0..1 confidence, resolving the -1.0 sentinel."""
    raw = float(getattr(entry, "confidence", -1.0))
    if raw >= 0.0:
        return raw
    from tapps_brain.models import _SOURCE_CONFIDENCE_DEFAULTS, MemorySource

    source = getattr(entry, "source", MemorySource.agent)
    return _SOURCE_CONFIDENCE_DEFAULTS.get(source, 0.5)


def _recency_score(last_accessed: str) -> float:
    """Map ``last_accessed`` ISO-8601 to a 0..1 score (newer = higher).

    Returns ``0.0`` when the timestamp is missing or malformed so unknown-age
    entries lose to anything with a real timestamp under the ranking tie-break.
    """
    if not last_accessed:
        return 0.0
    try:
        ts = datetime.fromisoformat(last_accessed)
        # Imported / legacy rows may be tz-naive — assume UTC so ranking does
        # not raise TypeError when subtracting from an aware ``now``.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return 0.0
    age_days = max(0.0, (datetime.now(tz=UTC) - ts).total_seconds() / 86400.0)
    return 1.0 / (1.0 + age_days / 30.0)


def _rank_score(entry: Any) -> float:
    """Combine confidence + recency per the TAP-2099 spec (max of the two)."""
    return max(_resolve_confidence(entry), _recency_score(getattr(entry, "last_accessed", "")))


def _build_frontmatter(entry: Any, *, redacted_value: str) -> str:
    """Render the per-file frontmatter + body for *entry*."""
    tags = list(getattr(entry, "tags", []) or [])
    source = getattr(entry, "source", "agent")
    source_value = source.value if hasattr(source, "value") else str(source)
    tags_field = "[" + ", ".join(tags) + "]"
    return (
        f"{_EXPORT_READONLY_BANNER}\n"
        f"---\n"
        f"key: {entry.key}\n"
        f"tier: {tier_str(entry.tier)}\n"
        f"confidence: {_resolve_confidence(entry):.4f}\n"
        f"source: {source_value}\n"
        f"created_at: {getattr(entry, 'created_at', '')}\n"
        f"last_accessed: {getattr(entry, 'last_accessed', '')}\n"
        f"tags: {tags_field}\n"
        f"---\n\n"
        f"{redacted_value}\n"
    )


def _one_line(text: str, limit: int = 140) -> str:
    """Collapse *text* to a single whitespace-normalized line, truncated to *limit*."""
    collapsed = " ".join(text.split())
    return collapsed[:limit]


def _build_okf_doc(entry: Any, *, redacted_value: str) -> str:
    """Render an OKF v0.1-conformant concept document for *entry*.

    Frontmatter is the first thing in the file (OKF conformance rule 1) with a
    non-empty ``type`` (the tier). String scalars are JSON-encoded so arbitrary
    values (colons, quotes) stay parseable YAML. The READ-ONLY banner moves into
    the body so it does not break frontmatter position.
    """
    tier = tier_str(entry.tier)
    tags = list(getattr(entry, "tags", []) or [])
    source = getattr(entry, "source", "agent")
    source_value = source.value if hasattr(source, "value") else str(source)
    timestamp = getattr(entry, "created_at", "") or getattr(entry, "last_accessed", "")
    description = _one_line(redacted_value)
    lines = [
        "---",
        f"type: {tier}",
        f"title: {json.dumps(entry.key)}",
        f"description: {json.dumps(description)}",
        f"key: {json.dumps(entry.key)}",
        f"tier: {tier}",
        f"confidence: {_resolve_confidence(entry):.4f}",
        f"source: {json.dumps(source_value)}",
        f"timestamp: {json.dumps(timestamp)}",
        f"tags: {json.dumps(tags)}",
        "---",
        "",
        _EXPORT_READONLY_BANNER,
        "",
        redacted_value,
        "",
    ]
    return "\n".join(lines)


def _build_okf_index(project_id: str, okf_entries: list[tuple[str, str, str, str]]) -> str:
    """Render the reserved bundle-root ``index.md`` (no frontmatter, grouped by tier)."""
    by_tier: dict[str, list[tuple[str, str]]] = {}
    for tier, key, desc, _created in okf_entries:
        by_tier.setdefault(tier, []).append((key, desc))
    lines = [
        f"# {project_id or 'tapps-brain'} knowledge bundle",
        "",
        f"<!-- okf_version: {_OKF_VERSION} -->",
        "",
    ]
    for tier in sorted(by_tier):
        lines.append(f"## {tier}")
        lines.append("")
        for key, desc in sorted(by_tier[tier]):
            suffix = f" — {desc}" if desc else ""
            lines.append(f"- [{key}]({tier}/{key}.md){suffix}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _build_okf_log(exported_at: str, okf_entries: list[tuple[str, str, str, str]]) -> str:
    """Render the reserved ``log.md`` — ISO date headings, newest first."""
    by_date: dict[str, list[tuple[str, str]]] = {}
    for tier, key, _desc, created in okf_entries:
        date = (created or exported_at)[:10]
        by_date.setdefault(date, []).append((tier, key))
    lines = ["# Log", ""]
    if not by_date:
        lines.extend([f"## {exported_at[:10]}", "", "- **Export** — empty snapshot.", ""])
        return "\n".join(lines).rstrip("\n") + "\n"
    for date in sorted(by_date, reverse=True):
        lines.append(f"## {date}")
        lines.append("")
        for tier, key in sorted(by_date[date]):
            lines.append(f"- **Creation** — `{tier}/{key}` recorded.")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def brain_export(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    output_dir: str,
    layout: str = "managed-agents",
    redact: bool = True,
    top_n_per_tier: int = 500,
    target_project_id: str = "",
) -> dict[str, Any]:
    """Snapshot top-N memories per tier into a Managed Agents-shaped folder.

    Writes ``<output_dir>/manifest.json`` and ``<output_dir>/<tier>/<key>.md``
    files.  Each file carries a ``READ-ONLY`` banner + frontmatter; values are
    redacted (AWS keys, GitHub tokens, JWTs, emails) when ``redact`` is true;
    entries tagged ``secret`` are skipped wholesale regardless of ``redact``.

    The export is a one-shot snapshot, NOT a continuous mirror — the
    TAP-2095 spike rejected the continuous-mirror path.  See
    :file:`docs/research/file-backed-memory-mirror.md` for the rationale and
    :file:`docs/guides/brain-export.md` for the operator-facing layout.

    Args:
        output_dir: Destination directory.  Created if absent; refuses to
            overwrite when the directory already contains files.
        layout: Layout name.  ``"managed-agents"`` (default) writes a banner +
            frontmatter per tier; ``"okf"`` writes an Open Knowledge Format v0.1
            bundle (frontmatter-first concept docs + reserved ``index.md`` /
            ``log.md``).
        redact: When true, apply the redaction pattern set to every value
            before write.  Independent of the ``secret``-tag skip.
        top_n_per_tier: Maximum entries to export per tier (default 500).
        target_project_id: Project to export.  Defaults to the caller's
            contextvar-resolved project when empty.  Recorded in
            ``manifest.json`` for the consumer to verify.

    Returns:
        Envelope with ``project_id``, ``output_dir``, ``layout``,
        ``schema_version``, ``exported_at``, ``tier_counts``,
        ``skipped_secret_tag``, ``redacted_fields``, ``files_written``.
        On invalid input: ``{"error": <code>, "message": <text>}``.
    """
    from pathlib import Path

    if layout not in _EXPORT_VALID_LAYOUTS:
        return {
            "error": "invalid_layout",
            "message": (f"layout must be one of {sorted(_EXPORT_VALID_LAYOUTS)}, got {layout!r}"),
        }
    if top_n_per_tier <= 0:
        return {
            "error": "invalid_top_n",
            "message": f"top_n_per_tier must be > 0, got {top_n_per_tier!r}",
        }
    if store is None or not hasattr(store, "iter_active_entries"):
        return {
            "error": "store_required",
            "message": "brain_export needs a MemoryStore with iter_active_entries()",
        }

    target = Path(output_dir)
    if target.exists() and any(target.iterdir()):
        return {
            "error": "output_exists_not_empty",
            "message": (
                f"refusing to write into non-empty directory {target!s}; "
                "pick a fresh path or empty it first"
            ),
        }
    target.mkdir(parents=True, exist_ok=True)

    effective_pid = target_project_id or project_id

    per_tier: dict[str, list[Any]] = {}
    skipped_secret = 0
    for entry in store.iter_active_entries():
        if _EXPORT_SECRET_TAG in (getattr(entry, "tags", []) or []):
            skipped_secret += 1
            continue
        tier_key = tier_str(entry.tier)
        per_tier.setdefault(tier_key, []).append(entry)

    tier_counts: dict[str, int] = {}
    files_written = 0
    redacted_fields = 0
    # (tier, key, description, created_at) collected for the OKF reserved files.
    okf_entries: list[tuple[str, str, str, str]] = []
    for tier_key, entries in per_tier.items():
        ranked = sorted(entries, key=_rank_score, reverse=True)[:top_n_per_tier]
        if not ranked:
            continue
        tier_dir = target / tier_key
        tier_dir.mkdir(exist_ok=True)
        tier_counts[tier_key] = len(ranked)
        for entry in ranked:
            raw_value = str(getattr(entry, "value", ""))
            if redact:
                clean_value, hits = _redact_value(raw_value)
                redacted_fields += hits
            else:
                clean_value = raw_value
            # Key charset is validator-constrained to [a-z0-9._-] (lowercase slug
            # starting with alphanumeric, max 128 chars), so it is filesystem-safe
            # without further sanitization.
            if layout == "okf":
                doc = _build_okf_doc(entry, redacted_value=clean_value)
                okf_entries.append(
                    (
                        tier_key,
                        entry.key,
                        _one_line(clean_value),
                        str(getattr(entry, "created_at", "") or ""),
                    )
                )
            else:
                doc = _build_frontmatter(entry, redacted_value=clean_value)
            (tier_dir / f"{entry.key}.md").write_text(doc, encoding="utf-8")
            files_written += 1

    exported_at = datetime.now(tz=UTC).isoformat()
    if layout == "okf":
        (target / "index.md").write_text(
            _build_okf_index(effective_pid, okf_entries), encoding="utf-8"
        )
        (target / "log.md").write_text(_build_okf_log(exported_at, okf_entries), encoding="utf-8")
    manifest = {
        "schema_version": _EXPORT_SCHEMA_VERSION,
        "project_id": effective_pid,
        "layout": layout,
        "exported_at": exported_at,
        "tier_counts": tier_counts,
        "files_written": files_written,
        "top_n_per_tier": int(top_n_per_tier),
        "redact": bool(redact),
        "skipped_secret_tag": skipped_secret,
        "redacted_fields": redacted_fields,
    }
    if layout == "okf":
        manifest["okf_version"] = _OKF_VERSION
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return {
        "project_id": effective_pid,
        "output_dir": str(target),
        "layout": layout,
        "schema_version": _EXPORT_SCHEMA_VERSION,
        "exported_at": exported_at,
        "tier_counts": tier_counts,
        "skipped_secret_tag": skipped_secret,
        "redacted_fields": redacted_fields,
        "files_written": files_written,
    }


# ---------------------------------------------------------------------------
# memory_* core CRUD
# ---------------------------------------------------------------------------


def _validate_and_normalize_save(
    store: Any,
    agent_id: str,
    *,
    key: str,
    value: str,
    tier: str,
    source: str,
    tags: list[str] | None,
    scope: str,
    confidence: float,
    agent_scope: str,
    source_agent: str,
    group: str | None,
) -> dict[str, Any]:
    """Validate + normalize save inputs, shared by :func:`memory_save` and
    :func:`memory_save_many` (TAP-2800).

    Returns either an error envelope (``{"error": ...}``) or the normalized
    keyword arguments for :meth:`MemoryStore.save` / one item of
    :meth:`MemoryStore.save_many`.  Centralising this keeps the single-save and
    batch-save validation byte-identical.
    """
    # "detail" is the canonical envelope key (openapi_contract.py); "message"
    # is kept as a legacy alias for older consumers.
    try:
        agent_scope = normalize_agent_scope(agent_scope)
    except ValueError as exc:
        return {
            "error": "invalid_agent_scope",
            "detail": str(exc),
            "message": str(exc),
            "valid_values": agent_scope_valid_values_for_errors(),
        }

    tier = normalize_save_tier(tier, store.profile)

    _valid_tiers: frozenset[str] = (
        frozenset(store.profile.layer_names)
        if store.profile is not None
        else frozenset(m.value for m in MemoryTier)
    )
    if tier not in _valid_tiers:
        _sorted_valid = sorted(_valid_tiers)
        _tier_msg = f"Invalid tier {tier!r}. Valid values: {_sorted_valid}"
        return {
            "error": "invalid_tier",
            "detail": _tier_msg,
            "message": _tier_msg,
            "valid_values": _sorted_valid,
        }
    _valid_sources = ("human", "agent", "inferred", "system")
    if source not in _valid_sources:
        _source_msg = f"Invalid source {source!r}. Valid values: {list(_valid_sources)}"
        return {
            "error": "invalid_source",
            "detail": _source_msg,
            "message": _source_msg,
            "valid_values": list(_valid_sources),
        }
    resolved_agent = source_agent or agent_id
    memory_group_arg: object = MEMORY_GROUP_UNSET if group is None else group

    return {
        "key": key,
        "value": value,
        "tier": tier,
        "source": source,
        "tags": tags,
        "scope": scope,
        "confidence": confidence,
        "agent_scope": agent_scope,
        "source_agent": resolved_agent,
        "memory_group": memory_group_arg,
    }


SUPERSEDE_GLOBAL = "global"
SUPERSEDE_KEY_SCOPED = "key-scoped"
_SUPERSEDE_MODES = frozenset({SUPERSEDE_GLOBAL, SUPERSEDE_KEY_SCOPED})


def _validate_supersede(supersede: str) -> dict[str, Any] | None:
    """Return a ``bad_request`` envelope when *supersede* is not a known mode.

    Fails closed rather than falling back to the default: silently treating a
    typo'd ``"key_scoped"`` as ``"global"`` would keep superseding a caller's
    neighbours after they explicitly asked it to stop.
    """
    if supersede in _SUPERSEDE_MODES:
        return None
    allowed = ", ".join(sorted(_SUPERSEDE_MODES))
    msg = f"supersede must be one of: {allowed} (got {supersede!r})"
    return {"error": "bad_request", "detail": msg, "message": msg}


def memory_save(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    key: str,
    value: str,
    tier: str = "pattern",
    source: str = "agent",
    tags: list[str] | None = None,
    scope: str = "project",
    confidence: float = -1.0,
    agent_scope: str = "private",
    source_agent: str = "",
    group: str | None = None,
    supersede: str = SUPERSEDE_GLOBAL,
) -> dict[str, Any]:
    """Save a memory entry with full structured validation.

    Validates ``agent_scope`` / ``tier`` / ``source`` against the active
    profile and returns a structured error envelope on bad input. Returns
    ``{"status": "saved", "key", "tier", "confidence", "memory_group"}``
    on success, or ``{"error": "bad_request", "detail": ...}`` when the
    underlying pydantic model rejects the payload (TAP-747).

    *supersede* selects how far a save may invalidate its neighbours:

    ``"global"`` (default)
        Current behaviour.  A textually similar entry in the same tier has its
        validity interval closed, whatever key it lives under, and its key is
        reported in ``invalidated``.
    ``"key-scoped"``
        Only this key's own history is touched; entries under other keys are
        left alone.  For key-spaces holding *independent facts* — one row per
        distinct thing — where topical similarity between neighbours is
        expected and is not a contradiction.
    """
    supersede_error = _validate_supersede(supersede)
    if supersede_error is not None:
        return supersede_error

    validated = _validate_and_normalize_save(
        store,
        agent_id,
        key=key,
        value=value,
        tier=tier,
        source=source,
        tags=tags,
        scope=scope,
        confidence=confidence,
        agent_scope=agent_scope,
        source_agent=source_agent,
        group=group,
    )
    if "error" in validated:
        return validated

    report: dict[str, Any] = {}
    try:
        result = store.save(
            **validated,
            report=report,
            conflict_check=(supersede != SUPERSEDE_KEY_SCOPED),
        )
    except _PydanticValidationError as exc:
        # TAP-747: pydantic slug-key validation raised from MemoryEntry.__init__
        # inside store.save() was escaping to the handler and producing HTTP 500.
        # Catch it here so that both single-item and batch routes see a structured
        # error dict ({"error": "bad_request", "detail": "<message>"}) and can
        # surface a 400 to the caller without any code change in the handlers.
        errors = exc.errors()
        msg = errors[0].get("msg", str(exc)) if errors else str(exc)
        return {"error": "bad_request", "detail": msg, "message": msg}

    return _save_result_envelope(result, requested_key=key, report=report)


def _save_result_envelope(
    result: Any,
    *,
    requested_key: str | None = None,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert a :meth:`MemoryStore.save` result into the MCP response envelope.

    A ``dict`` result (write-policy decision, dedup short-circuit error, …) is
    returned unchanged; a saved :class:`MemoryEntry` becomes the
    ``{"status": "saved", ...}`` envelope.  Shared by :func:`memory_save` and
    :func:`memory_save_many` (TAP-2800).

    When *requested_key* is supplied and the saved entry carries a different
    key, the write was coalesced onto some other row rather than persisted
    under the key the caller asked for.  That is reported as
    ``{"status": "coalesced", "persisted": False, "coalesced_into": <key>}``
    instead of ``"saved"`` — the envelope must never claim a write landed under
    a key that does not exist (TAP-5617).  After TAP-5615 no save path should
    reach this branch; it stays as a structural guarantee against future ones.

    *report* is the caller-owned dict passed to :meth:`MemoryStore.save`; its
    ``invalidated`` list (entries whose validity interval this save closed) is
    merged into the envelope when non-empty.
    """
    if isinstance(result, dict):
        return result
    envelope: dict[str, Any] = {
        "status": "saved",
        "key": result.key,
        "tier": str(result.tier),
        "confidence": result.confidence,
        "memory_group": result.memory_group,
    }
    if requested_key is not None and result.key != requested_key:
        envelope["status"] = "coalesced"
        envelope["key"] = requested_key
        envelope["coalesced_into"] = result.key
        envelope["persisted"] = False
    invalidated = (report or {}).get("invalidated")
    if invalidated:
        envelope["invalidated"] = list(invalidated)
    return envelope


def memory_get(store: Any, project_id: str, agent_id: str, *, key: str) -> dict[str, Any]:
    """Fetch a single memory entry by key.

    Returns the full :class:`~tapps_brain.models.MemoryEntry` as a JSON-mode
    dict, or ``{"error": "not_found", "key": key}`` when absent.
    """
    entry = store.get(key)
    if entry is None:
        return {"error": "not_found", "key": key}
    return entry.model_dump(mode="json")  # type: ignore[no-any-return]


def memory_delete(store: Any, project_id: str, agent_id: str, *, key: str) -> dict[str, Any]:
    """Hard-delete a memory entry. Idempotent.

    Returns ``{"deleted": bool, "key": key}`` — ``deleted`` is False when the
    key was already absent.
    """
    deleted = store.delete(key)
    return {"deleted": deleted, "key": key}


def memory_search(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    query: str,
    tier: str | None = None,
    scope: str | None = None,
    as_of: str | None = None,
    group: str | None = None,
    since: str = "",
    until: str = "",
    time_field: str = "created_at",
) -> list[dict[str, Any]] | dict[str, Any]:
    """Search memory entries with optional tier/scope/group + time-window filters.

    ``as_of`` (ISO-8601) returns the entry state at that point in time
    (bitemporal recall). ``since`` / ``until`` window by ``time_field``
    (``created_at`` by default; ``updated_at`` is also accepted). Returns
    a flat list of trimmed entry dicts, or ``{"error": "invalid_as_of"}``
    on a malformed timestamp.
    """
    if as_of is not None:
        # Blank as_of is invalid (unlike optional since/until): an empty
        # string is not None, so Postgres would still cast as_of::timestamptz.
        if not str(as_of).strip():
            detail = f"as_of must be a valid ISO-8601 timestamp, got {as_of!r}"
            return {"error": "invalid_as_of", "message": detail, "detail": detail}
        bad_as_of = validate_iso_timestamp("as_of", as_of)
        if bad_as_of is not None:
            return {
                "error": "invalid_as_of",
                "message": bad_as_of["detail"],
                "detail": bad_as_of["detail"],
            }
    for field_name, raw_ts in (("since", since), ("until", until)):
        bad = validate_iso_timestamp(field_name, raw_ts)
        if bad is not None:
            return bad
    results = store.search(
        query,
        tier=tier,
        scope=scope,
        as_of=as_of,
        memory_group=group,
        since=since.strip() or None,
        until=until.strip() or None,
        time_field=time_field,
    )
    return [
        {
            "key": e.key,
            "value": e.value,
            "tier": str(e.tier),
            "confidence": e.confidence,
            "tags": e.tags,
            "memory_group": e.memory_group,
        }
        for e in results
    ]


def memory_list(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    tier: str | None = None,
    scope: str | None = None,
    include_superseded: bool = False,
    group: str | None = None,
) -> list[dict[str, Any]]:
    """List memory entries with optional tier / scope / group filters.

    Values are truncated to the first 200 chars per row — call
    :func:`memory_get` for full content. ``include_superseded`` is False by
    default so the result reflects only currently-valid entries.
    """
    entries = store.list_all(
        tier=tier,
        scope=scope,
        include_superseded=include_superseded,
        memory_group=group,
    )
    return [
        {
            "key": e.key,
            "value": e.value[:200],
            "tier": str(e.tier),
            "confidence": e.confidence,
            "tags": e.tags,
            "scope": e.scope.value,
            "memory_group": e.memory_group,
        }
        for e in entries
    ]


def memory_list_groups(store: Any, project_id: str, agent_id: str) -> list[str]:
    """Return the distinct ``memory_group`` labels present in this project's store.

    ``memory_group`` is the local partition label (GitHub #49) — distinct from
    Hive namespaces and profile tiers. See :mod:`tapps_brain.memory_group`.
    """
    return store.list_memory_groups()  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Lifecycle: recall, reinforce, ingest, supersede, history
# ---------------------------------------------------------------------------


def memory_recall(
    store: Any, project_id: str, agent_id: str, *, message: str, group: str | None = None
) -> dict[str, Any]:
    """Run the recall orchestrator with optional ``memory_group`` filter.

    Returns the full :class:`~tapps_brain.models.RecallResult` payload
    (``memory_section``, ``memories``, ``token_count``, ``recall_time_ms``,
    ``truncated``) plus optional ``recall_diagnostics`` and ``quality_warning``
    fields when the diagnostics circuit breaker is non-CLOSED.
    """
    result = store.recall(message, memory_group=group)
    payload: dict[str, Any] = {
        "memory_section": result.memory_section,
        "memory_count": result.memory_count,
        "token_count": result.token_count,
        "recall_time_ms": result.recall_time_ms,
        "truncated": result.truncated,
        "memories": result.memories,
    }
    if result.recall_diagnostics is not None:
        payload["recall_diagnostics"] = result.recall_diagnostics.model_dump(mode="json")
    if result.quality_warning:
        payload["quality_warning"] = result.quality_warning
    return payload


def memory_reinforce(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    key: str,
    confidence_boost: float = 0.0,
) -> dict[str, Any]:
    """Reset the decay clock on an entry and optionally bump confidence.

    ``confidence_boost`` is clamped to ``[0.0, _MAX_CONFIDENCE_BOOST]`` and
    further constrained by source-based confidence ceilings. Returns
    ``{"error": "not_found"}`` when the key is unknown.
    """
    if not (0.0 <= confidence_boost <= _MAX_CONFIDENCE_BOOST):
        return {
            "error": "invalid_confidence_boost",
            "message": (
                f"confidence_boost must be in [0.0, {_MAX_CONFIDENCE_BOOST}],"
                f" got {confidence_boost}"
            ),
        }
    try:
        entry = store.reinforce(key, confidence_boost=confidence_boost)
    except KeyError:
        return {"error": "not_found", "key": key}
    return {
        "status": "reinforced",
        "key": entry.key,
        "confidence": entry.confidence,
        "access_count": entry.access_count,
    }


# ---------------------------------------------------------------------------
# Bulk operations (STORY-070.6)
# ---------------------------------------------------------------------------

_DEFAULT_MAX_BATCH_WRITE = 100
_DEFAULT_MAX_BATCH_READ = 50


def _batch_limit(default: int) -> int:
    """Return the configured batch size limit from env (or ``default``)."""
    import os

    raw = os.environ.get("TAPPS_BRAIN_MAX_BATCH_SIZE", "").strip()
    if raw.isdigit():
        return int(raw)
    return default


def memory_save_many(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    entries: list[dict[str, Any]],
    supersede: str = SUPERSEDE_GLOBAL,
) -> dict[str, Any]:
    """Save multiple memory entries.

    Returns::

        {
            "results": [<per-item save result>, ...],
            "saved_count": int,
            "error_count": int,
        }

    Per-item results follow the same shape as :func:`memory_save`.
    Partial failures are surfaced in the per-item result and do **not** abort
    the remaining items.

    *supersede* applies to the whole batch; see :func:`memory_save` for the
    modes.  It is batch-wide rather than per-entry because supersede scope is a
    property of the key-space being written, not of an individual row.
    """
    supersede_error = _validate_supersede(supersede)
    if supersede_error is not None:
        return supersede_error

    limit = _batch_limit(_DEFAULT_MAX_BATCH_WRITE)
    if len(entries) > limit:
        return {
            "error": "batch_too_large",
            "message": f"Maximum batch size is {limit}, got {len(entries)}.",
            "limit": limit,
        }

    results: list[dict[str, Any]] = [{} for _ in entries]
    saved = 0
    errors = 0
    # (original index, normalized save kwargs) for rows that pass validation and
    # therefore need a real persist — these go to the single batched save_many.
    to_persist: list[tuple[int, dict[str, Any]]] = []

    with start_mcp_tool_span(
        "memory_save_many",
        extra_attributes={"memory.batch_size": len(entries)},
    ):
        for i, raw_entry in enumerate(entries):
            with start_mcp_tool_span(
                "memory_save_many.item",
                extra_attributes={"memory.batch_index": i},
            ):
                if not isinstance(raw_entry, dict):
                    results[i] = {
                        "error": "bad_entry",
                        "message": "Entry must be a JSON object.",
                        "index": i,
                    }
                    errors += 1
                    continue
                # Field coercion can raise on malformed items (non-string key,
                # non-numeric confidence) — keep that per-item, honoring the
                # documented partial-failure contract.
                try:
                    key = str(raw_entry.get("key") or "").strip()
                    # Avoid ``value or ""`` — JSON ``0`` / ``false`` are falsy but
                    # valid (see ``_ensure_str_value`` / TAP-2675).
                    if "value" not in raw_entry or raw_entry.get("value") is None:
                        value = ""
                    else:
                        from tapps_brain.store import _ensure_str_value

                        value = _ensure_str_value(raw_entry.get("value"))
                    confidence = float(raw_entry.get("confidence", -1.0))
                except (TypeError, ValueError):
                    results[i] = {
                        "error": "bad_entry",
                        "message": "Malformed entry field (key/confidence).",
                        "index": i,
                    }
                    errors += 1
                    continue
                if not key or value == "":
                    results[i] = {
                        "error": "bad_entry",
                        "message": "key and value are required.",
                        "index": i,
                    }
                    errors += 1
                    continue
                validated = _validate_and_normalize_save(
                    store,
                    agent_id,
                    key=key,
                    value=value,
                    tier=raw_entry.get("tier", "pattern"),
                    source=raw_entry.get("source", "agent"),
                    tags=raw_entry.get("tags"),
                    scope=raw_entry.get("scope", "project"),
                    confidence=confidence,
                    agent_scope=raw_entry.get("agent_scope", "private"),
                    source_agent="",
                    group=raw_entry.get("group"),
                )
                if "error" in validated:
                    results[i] = validated
                    errors += 1
                    continue
                # Batch-wide supersede scope; save_many items mirror save kwargs.
                validated["conflict_check"] = supersede != SUPERSEDE_KEY_SCOPED
                to_persist.append((i, validated))

        # TAP-2800: persist every valid row in ONE batched DB round-trip rather
        # than N independent write-throughs.  Results align 1:1 with the input.
        store_results = store.save_many([kwargs for _, kwargs in to_persist])

        for (i, _kwargs), res in zip(to_persist, store_results, strict=True):
            envelope = _save_result_envelope(res, requested_key=_kwargs["key"])
            results[i] = envelope
            if "error" in envelope:
                errors += 1
            elif envelope.get("persisted") is False:
                # Coalesced onto another row: it neither landed under the
                # requested key nor failed, so it counts as neither (TAP-5617).
                # The per-row envelope carries the detail.
                pass
            else:
                saved += 1

    return {
        "results": results,
        "saved_count": saved,
        "error_count": errors,
    }


def memory_recall_many(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    queries: list[str | dict[str, Any]],
) -> dict[str, Any]:
    """Run recall for multiple queries.

    Returns::

        {
            "results": [[<memory>, ...], ...],   # one list per query, in order
            "query_count": int,
        }

    Each inner list follows the same shape as a single :func:`memory_recall`
    response.
    """
    limit = _batch_limit(_DEFAULT_MAX_BATCH_READ)
    if len(queries) > limit:
        return {
            "error": "batch_too_large",
            "message": f"Maximum recall batch size is {limit}, got {len(queries)}.",
            "limit": limit,
        }

    results: list[dict[str, Any]] = []

    with start_mcp_tool_span(
        "memory_recall_many",
        extra_attributes={"memory.batch_size": len(queries)},
    ):
        for i, raw_query in enumerate(queries):
            with start_mcp_tool_span(
                "memory_recall_many.item",
                extra_attributes={"memory.batch_index": i},
            ):
                if isinstance(raw_query, dict):
                    message = (raw_query.get("message") or raw_query.get("query") or "").strip()
                    group = raw_query.get("group")
                else:
                    message = str(raw_query).strip()
                    group = None

                if not message:
                    results.append(
                        {
                            "error": "bad_query",
                            "message": "Query message must be a non-empty string.",
                            "index": i,
                        }
                    )
                else:
                    results.append(
                        memory_recall(store, project_id, agent_id, message=message, group=group)
                    )

    return {
        "results": results,
        "query_count": len(queries),
    }


def memory_reinforce_many(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reinforce multiple memory entries.

    Returns::

        {
            "results": [<per-item reinforce result>, ...],
            "reinforced_count": int,
            "error_count": int,
        }

    Per-item results follow the same shape as :func:`memory_reinforce`.
    """
    limit = _batch_limit(_DEFAULT_MAX_BATCH_WRITE)
    if len(entries) > limit:
        return {
            "error": "batch_too_large",
            "message": f"Maximum batch size is {limit}, got {len(entries)}.",
            "limit": limit,
        }

    results: list[dict[str, Any]] = []
    reinforced = 0
    errors = 0

    with start_mcp_tool_span(
        "memory_reinforce_many",
        extra_attributes={"memory.batch_size": len(entries)},
    ):
        for i, raw_entry in enumerate(entries):
            with start_mcp_tool_span(
                "memory_reinforce_many.item",
                extra_attributes={"memory.batch_index": i},
            ):
                if not isinstance(raw_entry, dict):
                    item = {
                        "error": "bad_entry",
                        "message": "Entry must be a JSON object.",
                        "index": i,
                    }
                    errors += 1
                else:
                    # Coercion errors stay per-item: a malformed entry mid-batch
                    # must not abort the call after earlier reinforces persisted.
                    try:
                        key = str(raw_entry.get("key") or "").strip()
                        boost = float(raw_entry.get("confidence_boost", 0.0))
                    except (TypeError, ValueError):
                        item = {
                            "error": "bad_entry",
                            "message": "Malformed entry field (key/confidence_boost).",
                            "index": i,
                        }
                        errors += 1
                        results.append(item)
                        continue
                    if not key:
                        item = {
                            "error": "bad_entry",
                            "message": "key is required.",
                            "index": i,
                        }
                        errors += 1
                    else:
                        item = memory_reinforce(
                            store,
                            project_id,
                            agent_id,
                            key=key,
                            confidence_boost=boost,
                        )
                        if "error" in item:
                            errors += 1
                        else:
                            reinforced += 1
                results.append(item)

    return {
        "results": results,
        "reinforced_count": reinforced,
        "error_count": errors,
    }


def memory_ingest(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    context: str,
    source: str = "agent",
    agent_scope: str = "private",
) -> dict[str, Any]:
    """Extract durable facts from a free-form context blob and persist them.

    Runs rule-based extraction (no LLM) via :mod:`tapps_brain.extraction`
    to find decision-like statements, then saves each as a new entry. Returns
    the list of created keys plus a count.
    """
    try:
        agent_scope = normalize_agent_scope(agent_scope)
    except ValueError as exc:
        return {"error": "invalid_agent_scope", "message": str(exc)}

    created_keys = store.ingest_context(context, source=source, agent_scope=agent_scope)
    return {
        "status": "ingested",
        "created_keys": created_keys,
        "count": len(created_keys),
    }


def memory_supersede(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    old_key: str,
    new_value: str,
    key: str | None = None,
    tier: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Replace an existing entry, recording the old one as superseded.

    Preserves the version chain so :func:`memory_history` can reconstruct the
    full timeline. Returns ``{"error": "not_found"}`` for an unknown
    ``old_key`` or ``{"error": "already_superseded"}`` when the chain head
    has moved on.
    """
    kwargs: dict[str, Any] = {}
    if key is not None:
        kwargs["key"] = key
    if tier is not None:
        kwargs["tier"] = tier
    if tags is not None:
        kwargs["tags"] = tags
    try:
        entry = store.supersede(old_key, new_value, **kwargs)
    except KeyError:
        return {"error": "not_found", "key": old_key}
    except ValueError as exc:
        return {"error": "already_superseded", "message": str(exc)}
    return {
        "status": "superseded",
        "old_key": old_key,
        "new_key": entry.key,
        "tier": str(entry.tier),
        "confidence": entry.confidence,
    }


def memory_history(
    store: Any, project_id: str, agent_id: str, *, key: str
) -> list[dict[str, Any]] | dict[str, Any]:
    """Return the supersede chain for a key in chronological order.

    Each row contains the trimmed value plus ``valid_at`` / ``invalid_at`` /
    ``superseded_by`` so callers can reconstruct the timeline. Returns
    ``{"error": "not_found"}`` for an unknown or empty-chain key.
    """
    try:
        chain = store.history(key)
    except KeyError:
        return {"error": "not_found", "key": key}
    if not chain:
        return {"error": "not_found", "key": key}
    return [
        {
            "key": e.key,
            "value": e.value[:200],
            "tier": str(e.tier),
            "confidence": e.confidence,
            "valid_at": e.valid_at,
            "invalid_at": e.invalid_at,
            "superseded_by": e.superseded_by,
        }
        for e in chain
    ]


# ---------------------------------------------------------------------------
# Session indexing / capture
# ---------------------------------------------------------------------------


def memory_index_session(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    session_id: str,
    chunks: list[str],
) -> dict[str, Any]:
    """Persist session chunks to the searchable session index.

    Chunks are stored in the Postgres ``session_chunks`` table with a tsvector
    index, scoped to ``(project_id, agent_id)``. Use
    :func:`memory_search_sessions` to query them later.
    """
    stored = store.index_session(session_id, chunks)
    return {
        "status": "indexed",
        "session_id": session_id,
        "chunks_stored": stored,
    }


def memory_search_sessions(
    store: Any, project_id: str, agent_id: str, *, query: str, limit: int = 10
) -> dict[str, Any]:
    """Full-text search the session chunk index for relevant past sessions.

    Returns up to ``limit`` matching chunks with their session ids and scores.
    Trade-off: broader coverage than memory recall but noisier — relies on
    high-quality session flush prompts.
    """
    results = store.search_sessions(query, limit=limit)
    return {
        "results": results,
        "count": len(results),
    }


def memory_capture(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    response: str,
    source: str = "agent",
    agent_scope: str = "private",
) -> dict[str, Any]:
    """Capture durable facts from an agent's response and persist them.

    Used by post-turn hooks: the :class:`RecallOrchestrator` extracts decision-
    like statements from ``response`` and saves each as a new entry. Returns
    the list of created keys.
    """
    from tapps_brain.recall import RecallOrchestrator

    try:
        agent_scope = normalize_agent_scope(agent_scope)
    except ValueError as exc:
        return {"error": "invalid_agent_scope", "message": str(exc)}

    orchestrator = RecallOrchestrator(store)
    created_keys = orchestrator.capture(response, source=source, agent_scope=agent_scope)
    return {
        "status": "captured",
        "created_keys": created_keys,
        "count": len(created_keys),
    }


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


def memory_export(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    project_root: str,
    tier: str | None = None,
    scope: str | None = None,
    min_confidence: float | None = None,
    include_relations: bool = True,
    include_embeddings: bool = False,
    export_format: str = "json",
) -> dict[str, Any]:
    """Export memory entries as a versioned JSON-serialisable bundle (TAP-5027).

    Applies optional tier / scope / minimum-confidence filters. The output is
    accepted by :func:`memory_import`. For Managed Agents-shaped exports use
    :func:`brain_export` instead.

    Default format is the native ``tapps-memory`` 1.0 envelope (with optional
    ``relations``). Pass ``export_format="mif"`` for MIF v2 interchange.
    """
    from tapps_brain.io import export_bundle_dict

    _ = project_id, agent_id  # tenant identity is already bound on *store*
    return export_bundle_dict(
        store,
        project_root=project_root,
        tier=tier,
        scope=scope,
        min_confidence=min_confidence,
        include_relations=include_relations,
        include_embeddings=include_embeddings,
        export_format=export_format,
    )


def memory_import(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    memories_json: str,
    overwrite: bool = False,
    max_entries: int | None = None,
) -> dict[str, Any]:
    """Import a JSON bundle produced by :func:`memory_export` (TAP-5027).

    Accepts the native envelope, legacy ``{memories: [...]}``, bare arrays,
    and MIF v2 documents. ``overwrite=False`` skips existing keys. Restores
    ``relations`` / ``embeddings`` when present (embeddings only when the
    sidecar model id matches the active provider).
    """
    from tapps_brain.io import (
        DEFAULT_MAX_IMPORT_ENTRIES,
        import_memory_dicts,
        parse_import_payload,
    )

    _ = project_id, agent_id
    try:
        data = json.loads(memories_json)
    except json.JSONDecodeError as exc:
        return {"error": "invalid_json", "message": str(exc)}

    try:
        parsed = parse_import_payload(
            data,
            max_entries=max_entries if max_entries is not None else DEFAULT_MAX_IMPORT_ENTRIES,
        )
    except ValueError as exc:
        return {"error": "invalid_format", "message": str(exc)}

    result = import_memory_dicts(
        store,
        parsed["memories"],
        overwrite=overwrite,
        relations=parsed.get("relations"),
        embeddings=parsed.get("embeddings"),
    )
    return {
        "status": "imported",
        "imported": result["imported_count"],
        "skipped": result["skipped_count"],
        "errors": result["error_count"],
        "relations_restored": result.get("relations_restored", 0),
        "embeddings_restored": result.get("embeddings_restored", 0),
        "embeddings_skipped_mismatch": result.get("embeddings_skipped_mismatch", 0),
        "detected_format": parsed.get("detected_format"),
    }


# ---------------------------------------------------------------------------
# GC / consolidation config
# ---------------------------------------------------------------------------


def memory_gc_config(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    """Return the current :class:`~tapps_brain.gc.GCConfig` as a dict.

    Reflects the active profile's settings plus any runtime overrides applied
    via :func:`memory_gc_config_set`.
    """
    return store.get_gc_config().to_dict()  # type: ignore[no-any-return]


def memory_gc_config_set(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    floor_retention_days: int | None = None,
    session_expiry_days: int | None = None,
    contradicted_threshold: float | None = None,
) -> dict[str, Any]:
    """Partially update the GC config. ``None`` values keep the current value.

    Returns the resulting full :class:`GCConfig` dict. Changes apply
    immediately to the running store but are not persisted to the YAML profile.
    """
    from tapps_brain.gc import GCConfig

    current = store.get_gc_config()
    new_cfg = GCConfig(
        floor_retention_days=(
            floor_retention_days
            if floor_retention_days is not None
            else current.floor_retention_days
        ),
        session_expiry_days=(
            session_expiry_days if session_expiry_days is not None else current.session_expiry_days
        ),
        contradicted_threshold=(
            contradicted_threshold
            if contradicted_threshold is not None
            else current.contradicted_threshold
        ),
        # Not settable via this tool, but must be carried forward — omitting it
        # silently reset a CLI-configured TTL back to the dataclass default.
        session_index_ttl_days=current.session_index_ttl_days,
    )
    store.set_gc_config(new_cfg)
    return {"status": "updated", **new_cfg.to_dict()}


def memory_consolidation_config(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    """Return the current :class:`ConsolidationConfig` as a dict.

    Controls whether and when similar entries are deterministically merged
    (no LLM) on save. See [EPIC-058](../planning/epics/EPIC-058.md).
    """
    return store.get_consolidation_config().to_dict()  # type: ignore[no-any-return]


def memory_consolidation_config_set(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    enabled: bool | None = None,
    threshold: float | None = None,
    min_entries: int | None = None,
) -> dict[str, Any]:
    """Partially update the consolidation config. ``None`` values are kept.

    Returns the resulting full config dict. Use the
    ``maintenance consolidation-threshold-sweep`` CLI to pick a threshold
    before changing it in production.
    """
    from tapps_brain.store import ConsolidationConfig

    current = store.get_consolidation_config()
    new_cfg = ConsolidationConfig(
        enabled=enabled if enabled is not None else current.enabled,
        threshold=threshold if threshold is not None else current.threshold,
        min_entries=min_entries if min_entries is not None else current.min_entries,
    )
    store.set_consolidation_config(new_cfg)
    return {"status": "updated", **new_cfg.to_dict()}


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------


def memory_relations(store: Any, project_id: str, agent_id: str, *, key: str) -> dict[str, Any]:
    """Return the outgoing subject-predicate-object relations for ``key``.

    Relations are extracted deterministically (no LLM) by
    :mod:`tapps_brain.relations`. See :func:`memory_query_relations` for
    SPO-pattern queries and :func:`memory_find_related` for graph traversal.
    """
    relations = store.get_relations(key)
    return {"key": key, "relations": relations, "count": len(relations)}


def memory_relations_get_batch(
    store: Any, project_id: str, agent_id: str, *, keys_json: str
) -> dict[str, Any]:
    """Fetch relations for many keys in a single round-trip.

    ``keys_json`` is a JSON array of string keys. Returns a ``results`` dict
    keyed by entry key plus a ``total_count`` sum. Missing keys map to an
    empty list rather than raising.
    """
    try:
        keys = json.loads(keys_json)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": "invalid_keys_json", "message": str(exc)}
    if not isinstance(keys, list):
        return {"error": "invalid_keys_json", "message": "Expected a JSON array of strings."}
    results = store.get_relations_batch([str(k) for k in keys])
    total = sum(len(v) for v in results.values())
    return {"results": results, "total_count": total}


def memory_find_related(
    store: Any, project_id: str, agent_id: str, *, key: str, max_hops: int = 2
) -> dict[str, Any]:
    """Walk the relation graph from ``key`` up to ``max_hops`` levels deep.

    Returns each related entry's key with the hop count at which it was first
    reached. ``max_hops`` must be ``>= 1``. The richer first-class KG path is
    via :func:`brain_get_neighbors`.
    """
    if max_hops < 1:
        return {"error": "invalid_max_hops", "message": "max_hops must be >= 1"}
    try:
        results = store.find_related(key, max_hops=max_hops)
        return {
            "key": key,
            "max_hops": max_hops,
            "related": [{"key": k, "hops": h} for k, h in results],
            "count": len(results),
        }
    except KeyError:
        return {"error": "not_found", "message": f"Entry '{key}' not found."}


def memory_query_relations(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    subject: str = "",
    predicate: str = "",
    object_entity: str = "",
) -> dict[str, Any]:
    """SPO-pattern query over extracted relations.

    Empty values match any. ``query_relations(predicate="depends_on")``
    returns every ``X depends_on Y`` triple in the store, regardless of
    subject/object.
    """
    matches = store.query_relations(
        subject=subject or None,
        predicate=predicate or None,
        object_entity=object_entity or None,
    )
    return {"relations": matches, "count": len(matches)}


# ---------------------------------------------------------------------------
# Audit / tags
# ---------------------------------------------------------------------------


def memory_audit(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    key: str = "",
    event_type: str = "",
    since: str = "",
    until: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Query the Postgres ``audit_log`` table with optional filters.

    All filters are AND-combined; empty values match any. ``since`` / ``until``
    accept ISO-8601 timestamps. Limited to ``limit`` rows (default 50). See
    :class:`tapps_brain.audit.AuditReader`.
    """
    if limit < 1:
        return {"error": "invalid_limit", "message": "limit must be >= 1"}
    for field_name, raw_ts in (("since", since), ("until", until)):
        bad = validate_iso_timestamp(field_name, raw_ts)
        if bad is not None:
            return bad
    entries = store.audit(
        key=key or None,
        event_type=event_type or None,
        since=since or None,
        until=until or None,
        limit=limit,
    )
    return {
        "events": [e.model_dump(mode="json") for e in entries],
        "count": len(entries),
    }


def memory_list_tags(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    """Return all tags in use with their usage counts, sorted by frequency.

    Ties are broken alphabetically. Use :func:`memory_entries_by_tag` to look
    up entries carrying a specific tag.
    """
    counts = store.list_tags()
    tags_list = sorted(
        [{"tag": t, "count": c} for t, c in counts.items()],
        key=lambda x: (-x["count"], x["tag"]),
    )
    return {"tags": tags_list, "total": len(tags_list)}


def memory_update_tags(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    key: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, Any]:
    """Add and/or remove tags on a memory entry.

    Both lists are optional. Tags that are already present (for ``add``) or
    already absent (for ``remove``) are no-ops. Returns the updated tag list.
    """
    result = store.update_tags(key, add=add, remove=remove)
    if isinstance(result, dict):
        return result
    return {
        "status": "updated",
        "key": result.key,
        "tags": result.tags,
    }


def memory_entries_by_tag(
    store: Any, project_id: str, agent_id: str, *, tag: str, tier: str = ""
) -> dict[str, Any]:
    """Return all entries carrying ``tag``, optionally filtered by tier.

    Values are returned in full (not truncated). Empty ``tier`` matches any.
    """
    entries = store.entries_by_tag(tag, tier=tier or None)
    return {
        "tag": tag,
        "entries": [
            {
                "key": e.key,
                "value": e.value,
                "tier": str(e.tier),
                "confidence": e.confidence,
                "tags": e.tags,
            }
            for e in entries
        ],
        "count": len(entries),
    }


# ---------------------------------------------------------------------------
# Async-native variants (EPIC-072 STORY-072.5)
#
# These functions validate synchronously (pure CPU, no IO) then issue the
# write via ``AsyncMemoryStore`` so Postgres I/O stays off the thread pool.
# The sync originals remain for non-native code paths.
# ---------------------------------------------------------------------------


async def async_memory_save(
    async_store: Any,
    project_id: str,
    agent_id: str,
    *,
    key: str,
    value: str,
    tier: str = "pattern",
    source: str = "agent",
    tags: list[str] | None = None,
    scope: str = "project",
    confidence: float = -1.0,
    agent_scope: str = "private",
    source_agent: str = "",
    group: str | None = None,
    supersede: str = SUPERSEDE_GLOBAL,
) -> dict[str, Any]:
    """Async-native counterpart of :func:`memory_save`.

    Validates synchronously (pure CPU) then routes the write through
    :class:`~tapps_brain.aio.AsyncMemoryStore`, which when an async backend is
    wired sends the Postgres I/O to ``AsyncPostgresPrivateBackend`` instead
    of the default thread pool.
    """
    supersede_error = _validate_supersede(supersede)
    if supersede_error is not None:
        return supersede_error

    try:
        agent_scope = normalize_agent_scope(agent_scope)
    except ValueError as exc:
        return {
            "error": "invalid_agent_scope",
            "message": str(exc),
            "valid_values": agent_scope_valid_values_for_errors(),
        }

    tier = normalize_save_tier(tier, async_store.profile)

    _valid_tiers: frozenset[str] = (
        frozenset(async_store.profile.layer_names)
        if async_store.profile is not None
        else frozenset(m.value for m in MemoryTier)
    )
    if tier not in _valid_tiers:
        _sorted_valid = sorted(_valid_tiers)
        return {
            "error": "invalid_tier",
            "message": f"Invalid tier {tier!r}. Valid values: {_sorted_valid}",
            "valid_values": _sorted_valid,
        }
    _valid_sources = ("human", "agent", "inferred", "system")
    if source not in _valid_sources:
        return {
            "error": "invalid_source",
            "message": f"Invalid source {source!r}. Valid values: {list(_valid_sources)}",
            "valid_values": list(_valid_sources),
        }
    resolved_agent = source_agent or agent_id
    memory_group_arg: object = MEMORY_GROUP_UNSET if group is None else group

    report: dict[str, Any] = {}
    try:
        result = await async_store.save(
            key=key,
            value=value,
            tier=tier,
            source=source,
            tags=tags,
            scope=scope,
            confidence=confidence,
            agent_scope=agent_scope,
            source_agent=resolved_agent,
            memory_group=memory_group_arg,
            report=report,
            conflict_check=(supersede != SUPERSEDE_KEY_SCOPED),
        )
    except _PydanticValidationError as exc:
        errors = exc.errors()
        msg = errors[0].get("msg", str(exc)) if errors else str(exc)
        return {"error": "bad_request", "detail": msg, "message": msg}

    return _save_result_envelope(result, requested_key=key, report=report)


async def async_brain_forget(
    async_store: Any, project_id: str, agent_id: str, *, key: str
) -> dict[str, Any]:
    """Async-native counterpart of :func:`brain_forget`.

    Same return shape and same archive-then-delete semantics; the Postgres
    writes go through the async backend when one is wired.
    """
    import asyncio
    import inspect

    entry = await async_store.get(key)
    if entry is None:
        return {"forgotten": False, "reason": "not_found"}
    async_backend = getattr(async_store, "_async_backend", None)
    archive = getattr(async_backend, "archive_entry", None)
    if callable(archive):
        archived = archive(entry)
        if inspect.isawaitable(archived):
            archived = await archived
        if not archived:
            logger.warning("brain_forget.archive_failed", key=key)
    else:
        sync_store = getattr(async_store, "_store", None)
        backend = getattr(sync_store, "_persistence", None)
        if backend is not None:
            await asyncio.to_thread(_archive_forgotten_entry, backend, entry, key)
    await async_store.delete(key)
    return {"forgotten": True, "key": key}


async def async_brain_learn_success(
    async_store: Any,
    project_id: str,
    agent_id: str,
    *,
    task_description: str,
    task_id: str = "",
) -> dict[str, Any]:
    """Async-native counterpart of :func:`brain_learn_success`.

    Same key derivation and tagging; the Postgres write goes through the
    async backend when one is wired.
    """
    key = _content_key(f"success-{task_description}")
    tags = ["success"]
    if task_id:
        tags.append(f"task:{task_id}")
    out = await async_store.save(key=key, value=task_description, tier="procedural", tags=tags)
    rejected = _save_rejection(out)
    if rejected is not None:
        return {**rejected, "learned": False, "key": key}
    return {"learned": True, "key": key}


async def async_brain_learn_failure(
    async_store: Any,
    project_id: str,
    agent_id: str,
    *,
    description: str,
    task_id: str = "",
    error: str = "",
) -> dict[str, Any]:
    """Async-native counterpart of :func:`brain_learn_failure`.

    Same key derivation, tagging, and ``error``-append behaviour; the
    Postgres write goes through the async backend when one is wired.
    """
    key = _content_key(f"failure-{description}")
    value = f"{description}\n\nError: {error}" if error else description
    tags = ["failure"]
    if task_id:
        tags.append(f"task:{task_id}")
    out = await async_store.save(key=key, value=value, tier="procedural", tags=tags)
    rejected = _save_rejection(out)
    if rejected is not None:
        return {**rejected, "learned": False, "key": key}
    return {"learned": True, "key": key}


# ---------------------------------------------------------------------------
# Async-native reinforce shims (STORY-072.9, TAP-1566)
# ---------------------------------------------------------------------------


async def async_memory_reinforce(
    async_store: Any,
    project_id: str,
    agent_id: str,
    *,
    key: str,
    confidence_boost: float = 0.0,
) -> dict[str, Any]:
    """Async-native counterpart of :func:`memory_reinforce`.

    Routes the reinforce write through ``AsyncMemoryStore.reinforce`` which,
    when an async backend is wired, captures the persistence layer so the
    Postgres write goes through ``AsyncPostgresPrivateBackend`` instead of a
    thread-pool thread.

    Returns the same response shape as :func:`memory_reinforce`.
    """
    if not (0.0 <= confidence_boost <= _MAX_CONFIDENCE_BOOST):
        return {
            "error": "invalid_confidence_boost",
            "message": (
                f"confidence_boost must be in [0.0, {_MAX_CONFIDENCE_BOOST}],"
                f" got {confidence_boost}"
            ),
        }
    try:
        entry = await async_store.reinforce(key, confidence_boost=confidence_boost)
    except KeyError:
        return {"error": "not_found", "key": key}
    return {
        "status": "reinforced",
        "key": entry.key,
        "confidence": entry.confidence,
        "access_count": entry.access_count,
    }


async def async_memory_reinforce_many(
    async_store: Any,
    project_id: str,
    agent_id: str,
    *,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Async-native counterpart of :func:`memory_reinforce_many`.

    Loops :func:`async_memory_reinforce` so each per-item reinforce gets the
    async-native write path while preserving partial-failure semantics.
    """
    limit = _batch_limit(_DEFAULT_MAX_BATCH_WRITE)
    if len(entries) > limit:
        return {
            "error": "batch_too_large",
            "message": f"Maximum reinforce batch size is {limit}, got {len(entries)}.",
            "limit": limit,
        }

    results: list[dict[str, Any]] = []
    reinforced = 0
    errors = 0

    with start_mcp_tool_span(
        "memory_reinforce_many",
        extra_attributes={"memory.batch_size": len(entries)},
    ):
        for i, raw_entry in enumerate(entries):
            with start_mcp_tool_span(
                "memory_reinforce_many.item",
                extra_attributes={"memory.batch_index": i},
            ):
                if not isinstance(raw_entry, dict):
                    item: dict[str, Any] = {
                        "error": "bad_entry",
                        "message": "Entry must be a JSON object.",
                        "index": i,
                    }
                    errors += 1
                else:
                    # Coercion errors stay per-item: a malformed entry mid-batch
                    # must not abort the call after earlier reinforces persisted.
                    try:
                        key = str(raw_entry.get("key") or "").strip()
                        boost = float(raw_entry.get("confidence_boost", 0.0))
                    except (TypeError, ValueError):
                        item = {
                            "error": "bad_entry",
                            "message": "Malformed entry field (key/confidence_boost).",
                            "index": i,
                        }
                        errors += 1
                        results.append(item)
                        continue
                    if not key:
                        item = {
                            "error": "bad_entry",
                            "message": "key is required.",
                            "index": i,
                        }
                        errors += 1
                    else:
                        item = await async_memory_reinforce(
                            async_store,
                            project_id,
                            agent_id,
                            key=key,
                            confidence_boost=boost,
                        )
                        if "error" in item:
                            errors += 1
                        else:
                            reinforced += 1
                results.append(item)

    return {
        "results": results,
        "reinforced_count": reinforced,
        "error_count": errors,
    }
