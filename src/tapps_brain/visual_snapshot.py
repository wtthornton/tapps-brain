"""Versioned JSON snapshot for brain visual surfaces (dashboard / hero / demos).

Contract: aggregated metadata only — no memory bodies. Tag names and group names
are omitted unless ``privacy="local"``. See ``docs/planning/brain-visual-implementation-plan.md``.

Key public API: :func:`compute_fingerprint_hex`, :func:`theme_from_fingerprint`,
:class:`DiagnosticsSummary`, :class:`AccessBucket`, :class:`AccessStats`.
The :func:`build_visual_snapshot` function assembles the full
:class:`VisualSnapshot` from a live ``MemoryStore``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.metrics import StoreHealthReport
    from tapps_brain.store import MemoryStore

_THEME_SEED_BYTE_LEN = 8

VISUAL_SNAPSHOT_SCHEMA_VERSION: Literal[2] = 2

PrivacyTier = Literal["standard", "strict", "local"]

PRIVACY_STANDARD = (
    "Aggregated metadata; excludes memory text and keys. "
    "Tags and named memory groups omitted unless privacy tier is local."
)
PRIVACY_STRICT = (
    "Strict tier: store path and tampered key list redacted; no tag or memory_group names."
)
PRIVACY_LOCAL = (
    "Local tier: includes tag frequencies and named memory_group counts. Do not share publicly."
)

# Back-compat alias for imports/tests
PRIVACY_NOTICE = PRIVACY_STANDARD

_IDENTITY_SCHEMA_VERSION = 2
_TOP_TAGS_LIMIT = 20
_SCORE_OK_MIN = 0.7
_SCORE_WARN_MIN = 0.55
_CAP_WARN_RATIO = 0.8
_CAP_FAIL_RATIO = 0.95
#: TAP-4332: run the live consolidation gauge scan inline only for stores at or
#: below this size; larger stores reuse the cached gauge so /snapshot never
#: stalls on the O(n^2) similarity scan (TAP-4330).
_SNAPSHOT_INLINE_SCAN_MAX_ENTRIES = 2000
#: TAP-4332: refresh the persisted diagnostics row at most once per this many
#: seconds on the snapshot path, so the dashboard stays current without running
#: diagnostics on every poll.
_DIAG_REFRESH_TTL_SEC = 300.0
_MAINT_BACKLOG_WARN = 200


class AgentEntry(TypedDict):
    """Per-agent row for the agent registry live table."""

    agent_id: str
    namespace: str
    scope: str
    registered_at: str
    last_write_at: str | None


class DiagnosticsSummary(BaseModel):
    """Subset of diagnostics safe for visual telemetry."""

    composite_score: float = Field(ge=0.0, le=1.0)
    circuit_state: str
    recorded_at: str


class VisualThemeTokens(BaseModel):
    """Deterministic theme seeds aligned with NLT Labs amber palette (see BRAND-STYLE-GUIDE).

    Hues are constrained to the gold/amber wedge so exports never encode cyan/blue/purple chrome.
    """

    hue_primary: int = Field(ge=0, le=359, description="Base hue in amber family (snapshot seed).")
    hue_accent: int = Field(ge=0, le=359)
    accent_chroma: float = Field(ge=0.0, le=1.0, description="Relative saturation scale.")
    surface_lightness: int = Field(
        ge=6,
        le=18,
        description="Primary panel background lightness scale (dark-first).",
    )
    text_lightness: int = Field(ge=88, le=98, description="Primary text on dark.")
    flow_angle_deg: int = Field(
        ge=0,
        le=359,
        description="Background gradient direction seed (degrees).",
    )


class HiveNamespaceRow(BaseModel):
    """Per-namespace entry count and last write timestamp (no memory text)."""

    name: str
    entry_count: int = Field(default=0, ge=0)
    last_write_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC of the most recent write to this namespace, or None.",
    )


# Backward-compatible alias (STORY-065.4); prefer HiveNamespaceRow in new code.
NamespaceDetail = HiveNamespaceRow


class HiveHealthSummary(BaseModel):
    """Hive hub telemetry (no memory text)."""

    connected: bool = False
    status: str = Field(default="ok", description="ok | warn")
    entries: int = 0
    agents: int = 0
    namespaces: list[HiveNamespaceRow] = Field(
        default_factory=list,
        description="Per-namespace entry count and last write; populated when connected.",
    )


class AccessBucket(BaseModel):
    """Histogram bucket for access_count on entries."""

    label: str
    count: int = Field(ge=0, description="Number of memories in this bucket.")


class AccessStats(BaseModel):
    """Aggregated access signals (no per-key data)."""

    sum_access_count: int = Field(ge=0)
    mean_access_count: float = Field(ge=0.0)
    entries_with_access: int = Field(ge=0, description="Count of entries with access_count > 0.")
    sum_total_access_count: int = Field(default=0, ge=0)
    sum_useful_access_count: int = Field(default=0, ge=0)
    buckets: list[AccessBucket] = Field(default_factory=list)


class TagStat(BaseModel):
    """Tag frequency (local privacy tier only)."""

    tag: str
    count: int = Field(ge=1)


class SnapshotAggregates(BaseModel):
    """Pre-computed snapshot rollups (no memory bodies or keys)."""

    tier_distribution: dict[str, int] = Field(default_factory=dict)
    agent_scope_counts: dict[str, int] = Field(default_factory=dict)
    access_stats: AccessStats
    memory_group_counts: dict[str, int] = Field(default_factory=dict)
    tag_counts: dict[str, int] = Field(default_factory=dict)


class MemoryVelocity(BaseModel):
    """Recent write and recall velocity counts (derived from Postgres timestamps).

    writes_*  — entries whose ``created_at`` falls within the window.
    recalls_* — entries whose ``last_accessed`` falls within the window
                *and* ``last_accessed != created_at`` (guards against counting
                the initial save as a recall).
    """

    writes_1h: int = Field(default=0, ge=0)
    recalls_1h: int = Field(default=0, ge=0)
    writes_24h: int = Field(default=0, ge=0)
    recalls_24h: int = Field(default=0, ge=0)


ScorecardStatus = Literal["ok", "warn", "fail", "info", "unknown"]


class ScorecardCheck(BaseModel):
    """Single row for operator scorecard / issue triage (deterministic from snapshot inputs)."""

    id: str = Field(description="Stable slug for dashboards and tickets.")
    title: str
    status: ScorecardStatus
    detail: str = Field(description="Human-readable evidence for this row.")
    ticket_hint: str = Field(
        default="",
        description="Optional next step when filing an issue.",
    )


class RetrievalMetrics(BaseModel):
    """In-process retrieval pipeline counters (resets on process restart).

    All values accumulate since the last process start.  They are 0 when no
    queries have been issued since startup.  Collected from module-level
    accumulators in :mod:`tapps_brain.otel_tracer`; never raises.
    """

    total_queries: int = Field(default=0, ge=0, description="store.recall()/search() calls.")
    bm25_hits: int = Field(default=0, ge=0, description="Cumulative BM25 candidate count.")
    vector_hits: int = Field(default=0, ge=0, description="Cumulative vector candidate count.")
    rrf_fusions: int = Field(
        default=0, ge=0, description="Queries where both BM25+vector legs had candidates."
    )
    mean_latency_ms: float = Field(
        default=0.0, ge=0.0, description="Running mean recall/search latency (ms)."
    )


class LatencyHistogramBucket(BaseModel):
    """One bar in the recall latency histogram."""

    label: str
    count: int = Field(ge=0)


class RetrievalDetail(BaseModel):
    """Latency percentiles and histogram for the retrieval dashboard panel."""

    latency_p50_ms: float | None = Field(
        default=None, description="Median recall latency (ms) from in-process samples."
    )
    latency_p95_ms: float | None = Field(default=None, description="95th-pct recall latency (ms).")
    latency_p99_ms: float | None = Field(default=None, description="99th-pct recall latency (ms).")
    latency_histogram: list[LatencyHistogramBucket] | None = Field(
        default=None,
        description="Fixed-bucket histogram of recall latencies; null when no samples yet.",
    )
    embedding_model: str | None = Field(
        default=None,
        description="Active embedding model id when an embedding provider is configured.",
    )
    cache_hit_ratio: float | None = Field(
        default=None,
        description="Optional recall cache hit ratio (not yet instrumented).",
    )


class VisualSnapshot(BaseModel):
    """``brain-visual.json`` contract (schema version 2)."""

    schema_version: Literal[2] = Field(default=VISUAL_SNAPSHOT_SCHEMA_VERSION)
    generated_at: str = Field(description="ISO-8601 UTC when the snapshot was built.")
    fingerprint_sha256: str = Field(description="SHA-256 of canonical identity payload (hex).")
    identity_schema_version: int = Field(
        default=_IDENTITY_SCHEMA_VERSION,
        description="Version of the hashed identity object (bump when fingerprint inputs change).",
    )
    privacy_tier: PrivacyTier = "standard"
    privacy: str = Field(default=PRIVACY_STANDARD)
    health: dict[str, Any]
    agent_scope_counts: dict[str, int]
    hive_attached: bool
    hive_health: HiveHealthSummary = Field(default_factory=HiveHealthSummary)
    retrieval_effective_mode: str = Field(
        default="unknown",
        description="bm25_only | hybrid_pgvector_hnsw | hybrid_pgvector_empty | "
        "hybrid_on_the_fly_embeddings | unknown",
    )
    retrieval_summary: str = ""
    vector_index_enabled: bool = True
    vector_index_rows: int = Field(default=0, ge=0)
    memory_group_count: int = Field(
        default=0,
        ge=0,
        description="Distinct non-empty memory_group values.",
    )
    memory_group_counts: dict[str, int] | None = Field(
        default=None,
        description="Named group → entry count; set only for privacy_tier=local.",
    )
    velocity: MemoryVelocity = Field(
        default_factory=MemoryVelocity,
        description="Recent write/recall rate (1 h and 24 h windows) from Postgres timestamps.",
    )
    access_stats: AccessStats | None = None
    tag_stats: list[TagStat] | None = Field(
        default=None,
        description="Top tags by frequency; set only for privacy_tier=local.",
    )
    retrieval_metrics: RetrievalMetrics = Field(
        default_factory=RetrievalMetrics,
        description="In-process BM25/vector/RRF counters and mean latency since last restart.",
    )
    retrieval: RetrievalDetail | None = Field(
        default=None,
        description="Latency percentiles and histogram from in-process recall observations.",
    )
    diagnostics: DiagnosticsSummary | None = None
    scorecard: list[ScorecardCheck] = Field(
        default_factory=list,
        description="Deterministic pass/warn/fail rows for operators and issue templates.",
    )
    agent_registry: list[AgentEntry] = Field(
        default_factory=list,
        description=(
            "Per-agent rows from the Hive agent_registry table; "
            "populated only when Hive is connected. "
            "Sorted by last_write_at descending (most-recently-active first). "
            "agent_id is truncated to 8 chars on standard/strict privacy tiers."
        ),
    )
    diagnostics_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "STORY-069.7: recent diagnostics history rows (most recent first). "
            "Each row carries ``project_id`` so /snapshot?project= can filter."
        ),
    )
    feedback_events: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "STORY-069.7: recent feedback events (default 200). "
            "Each event carries ``project_id`` for /snapshot?project= filtering."
        ),
    )
    theme: VisualThemeTokens


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_fingerprint_hex(identity: dict[str, object]) -> str:
    """Return hex SHA-256 of the canonical identity object."""
    body = _canonical_json(identity).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def theme_from_fingerprint(fingerprint_hex: str) -> VisualThemeTokens:
    """Derive theme tokens deterministically from fingerprint bytes.

    Accent hues stay in ~28-48 degrees (amber/gold) per NLT Labs brand
    (no blue/cyan/purple accents).
    """
    digest = bytes.fromhex(fingerprint_hex)
    if len(digest) < _THEME_SEED_BYTE_LEN:
        digest = digest.ljust(_THEME_SEED_BYTE_LEN, b"0")
    b = digest
    hue_p = 28 + (b[0] % 20)
    hue_a = min(48, hue_p + 2 + (b[1] % 10))
    chroma = round(0.5 + (b[2] % 14) / 100.0, 2)
    surf = 6 + (b[3] % 7)
    text = 90 + (b[4] % 9)
    flow = int.from_bytes(b[6:8], "big") % 360
    return VisualThemeTokens(
        hue_primary=hue_p,
        hue_accent=hue_a,
        accent_chroma=chroma,
        surface_lightness=surf,
        text_lightness=text,
        flow_angle_deg=flow,
    )


def _hive_attached(store: MemoryStore) -> bool:
    return getattr(store, "_hive_store", None) is not None


def _empty_access_stats() -> AccessStats:
    return AccessStats(
        sum_access_count=0,
        mean_access_count=0.0,
        entries_with_access=0,
        sum_total_access_count=0,
        sum_useful_access_count=0,
        buckets=[
            AccessBucket(label="0", count=0),
            AccessBucket(label="1-5", count=0),
            AccessBucket(label="6-20", count=0),
            AccessBucket(label="21+", count=0),
        ],
    )


def _agent_scope_counts_from_entries(entries: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in entries:
        scope = getattr(e, "agent_scope", None) or "private"
        counts[scope] = counts.get(scope, 0) + 1
    return dict(sorted(counts.items()))


def _agent_scope_counts_from_aggregates(aggregates: SnapshotAggregates) -> dict[str, int]:
    return dict(sorted(aggregates.agent_scope_counts.items()))


def _collect_hive_health(_store: MemoryStore) -> HiveHealthSummary:
    """Best-effort Hive hub stats (matches health_check spirit; never raises)."""
    try:
        from tapps_brain.backends import AgentRegistry, resolve_hive_backend_from_env

        hive = resolve_hive_backend_from_env()
        if hive is None:
            return HiveHealthSummary(connected=False, status="skipped")
        try:
            # Single GROUP BY query — prefer namespace_detail_list() when available.
            if hasattr(hive, "namespace_detail_list"):
                raw_details = hive.namespace_detail_list()
                ns_rows = [
                    HiveNamespaceRow(
                        name=str(row["namespace"]),
                        entry_count=int(row.get("entry_count", 0)),
                        last_write_at=row.get("last_write_at"),
                    )
                    for row in raw_details
                ]
            else:
                ns_counts = hive.count_by_namespace()
                ns_rows = [
                    HiveNamespaceRow(name=str(ns), entry_count=int(count), last_write_at=None)
                    for ns, count in ns_counts.items()
                ]
            ns_rows_sorted = sorted(ns_rows, key=lambda d: d.name)
            total_entries = sum(d.entry_count for d in ns_rows_sorted)
            registry = AgentRegistry()
            return HiveHealthSummary(
                connected=True,
                status="ok",
                namespaces=ns_rows_sorted,
                entries=total_entries,
                agents=len(registry.list_agents()),
            )
        finally:
            hive.close()
    except Exception:
        return HiveHealthSummary(connected=False, status="warn")


_AGENT_ID_TRUNCATE_LEN = 8
_AGENT_SILENT_HOURS = 24


def _collect_agent_registry(
    hive_backend: object, *, privacy: PrivacyTier = "standard"
) -> list[AgentEntry]:
    """Best-effort agent registry rows with last_write_at (never raises).

    Queries agent_registry LEFT JOIN hive_memories to derive last_write_at.
    Returns [] if the backend is not Postgres-backed, the table does not exist,
    or any other error occurs (pre-migration schema guard).

    Args:
        hive_backend: An open HiveBackend (must have ``._cm`` for Postgres queries).
        privacy: Privacy tier — for ``local`` the full agent_id is preserved;
            for ``standard`` / ``strict`` it is truncated to 8 chars + ``…``.
    """
    try:
        cm = getattr(hive_backend, "_cm", None)
        if cm is None:
            return []
        with cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ar.id,
                    ar.profile,
                    ar.registered_at::text,
                    MAX(COALESCE(hm.updated_at, hm.created_at))::text AS last_write_at
                FROM agent_registry ar
                LEFT JOIN hive_memories hm ON hm.source_agent = ar.id
                GROUP BY ar.id, ar.profile, ar.registered_at
                ORDER BY MAX(COALESCE(hm.updated_at, hm.created_at)) DESC NULLS LAST
                """
            )
            rows = cur.fetchall()
        result: list[AgentEntry] = []
        for row in rows:
            agent_id = str(row[0]) if row[0] is not None else ""
            if privacy != "local" and len(agent_id) > _AGENT_ID_TRUNCATE_LEN:
                agent_id = agent_id[:_AGENT_ID_TRUNCATE_LEN] + "\u2026"
            result.append(
                AgentEntry(
                    agent_id=agent_id,
                    namespace=str(row[1]) if row[1] else "universal",
                    scope="hive",
                    registered_at=str(row[2]) if row[2] else "",
                    last_write_at=row[3],
                )
            )
        return result
    except Exception:
        return []


def _memory_group_counts_from_entries(entries: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in entries:
        mg = getattr(e, "memory_group", None)
        if mg:
            counts[mg] = counts.get(mg, 0) + 1
    return counts


def _memory_group_stats(
    entries: list[Any],
    *,
    privacy: PrivacyTier,
) -> tuple[int, dict[str, int] | None]:
    counts = _memory_group_counts_from_entries(entries)
    n = len(counts)
    if privacy == "local":
        return n, dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return n, None


def _memory_group_stats_from_aggregates(
    aggregates: SnapshotAggregates,
    *,
    privacy: PrivacyTier,
) -> tuple[int, dict[str, int] | None]:
    counts = aggregates.memory_group_counts
    n = len(counts)
    if privacy == "local":
        return n, dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return n, None


_ACCESS_LO = 5
_ACCESS_HI = 20


def _access_stats_from_entries(entries: list[Any]) -> AccessStats:
    if not entries:
        return _empty_access_stats()
    b0 = b1 = b2 = b3 = 0
    sum_ac = 0
    sum_total = 0
    sum_useful = 0
    with_access = 0
    for e in entries:
        ac = int(getattr(e, "access_count", 0) or 0)
        sum_ac += ac
        if ac > 0:
            with_access += 1
        sum_total += int(getattr(e, "total_access_count", 0) or 0)
        sum_useful += int(getattr(e, "useful_access_count", 0) or 0)
        if ac == 0:
            b0 += 1
        elif ac <= _ACCESS_LO:
            b1 += 1
        elif ac <= _ACCESS_HI:
            b2 += 1
        else:
            b3 += 1
    n = len(entries)
    return AccessStats(
        sum_access_count=sum_ac,
        mean_access_count=round(sum_ac / n, 4),
        entries_with_access=with_access,
        sum_total_access_count=sum_total,
        sum_useful_access_count=sum_useful,
        buckets=[
            AccessBucket(label="0", count=b0),
            AccessBucket(label="1-5", count=b1),
            AccessBucket(label="6-20", count=b2),
            AccessBucket(label="21+", count=b3),
        ],
    )


def _tag_counts_from_entries(entries: list[Any]) -> dict[str, int]:
    freq: dict[str, int] = {}
    for e in entries:
        for t in getattr(e, "tags", None) or []:
            if isinstance(t, str) and t.strip():
                freq[t] = freq.get(t, 0) + 1
    return freq


def _tag_stats_local(entries: list[Any], *, limit: int) -> list[TagStat]:
    ranked = sorted(_tag_counts_from_entries(entries).items(), key=lambda kv: (-kv[1], kv[0]))
    return [TagStat(tag=k, count=v) for k, v in ranked[:limit]]


def _tag_stats_from_counts(tag_counts: dict[str, int], *, limit: int) -> list[TagStat]:
    ranked = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [TagStat(tag=k, count=v) for k, v in ranked[:limit]]


def _snapshot_aggregates_from_entries(entries: list[Any]) -> SnapshotAggregates:
    """Reference rollup used by in-memory stores and golden parity tests."""
    return SnapshotAggregates(
        tier_distribution=_tier_distribution_from_entries(entries),
        agent_scope_counts=_agent_scope_counts_from_entries(entries),
        access_stats=_access_stats_from_entries(entries),
        memory_group_counts=_memory_group_counts_from_entries(entries),
        tag_counts=_tag_counts_from_entries(entries),
    )


def _tier_distribution_from_entries(entries: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in entries:
        tier = getattr(e, "tier", None)
        if tier is None:
            key = "pattern"
        elif hasattr(tier, "value"):
            key = str(tier.value)
        else:
            key = str(tier)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _try_load_snapshot_aggregates(store: MemoryStore) -> SnapshotAggregates | None:
    """Return backend SQL aggregates when supported; otherwise ``None``."""
    backend = store._persistence
    # Unit-test MagicMock stores auto-vivify attributes; never treat them as SQL backends.
    if type(backend).__module__ == "unittest.mock":
        return None
    agg_fn = getattr(backend, "snapshot_aggregates", None)
    if not callable(agg_fn):
        return None
    project_id = getattr(store, "_project_id", None) or getattr(backend, "_project_id", None)
    if project_id is None:
        return None
    return cast("SnapshotAggregates", agg_fn(project_id))


def _redact_health(hdump: dict[str, Any], privacy: PrivacyTier) -> dict[str, Any]:
    out = dict(hdump)
    if privacy == "strict":
        out["store_path"] = "<redacted>"
        out["integrity_tampered_keys"] = []
    return out


def _privacy_copy(tier: PrivacyTier) -> str:
    if tier == "strict":
        return PRIVACY_STRICT
    if tier == "local":
        return PRIVACY_LOCAL
    return PRIVACY_STANDARD


def _build_scorecard(
    report: StoreHealthReport,
    *,
    diagnostics: DiagnosticsSummary | None,
    hive_attached: bool,
    hive_health: HiveHealthSummary,
    retrieval_mode: str,
    skip_diagnostics: bool,
) -> list[ScorecardCheck]:
    """Derive scorecard rows from store health and related snapshot fields."""
    checks: list[ScorecardCheck] = []
    entry_count = int(getattr(report, "entry_count", 0) or 0)
    max_entries = int(getattr(report, "max_entries", 5000) or 5000)

    if entry_count == 0:
        checks.append(
            ScorecardCheck(
                id="store_entries",
                title="Store contents",
                status="info",
                detail="No memories in this project store yet.",
                ticket_hint="",
            )
        )
    else:
        checks.append(
            ScorecardCheck(
                id="store_entries",
                title="Store contents",
                status="ok",
                detail=f"{entry_count} memor(y/ies) within max {max_entries}.",
                ticket_hint="",
            )
        )

    if skip_diagnostics:
        checks.append(
            ScorecardCheck(
                id="diagnostics_data",
                title="Diagnostics data",
                status="unknown",
                detail="Diagnostics omitted (export used --skip-diagnostics).",
                ticket_hint="Re-export without --skip-diagnostics for circuit/score signals.",
            )
        )
    elif diagnostics is None:
        # TAP-4332: not skipped — there is simply no recorded diagnostics row yet.
        checks.append(
            ScorecardCheck(
                id="diagnostics_data",
                title="Diagnostics data",
                status="unknown",
                detail="No recent diagnostics recorded yet.",
                ticket_hint="Run `tapps-brain diagnostics health` to record the first snapshot.",
            )
        )
    else:
        checks.append(
            ScorecardCheck(
                id="diagnostics_data",
                title="Diagnostics data",
                status="ok",
                detail="Composite score and circuit state included in this export.",
                ticket_hint="",
            )
        )
        circuit = (diagnostics.circuit_state or "").lower()
        if circuit == "closed":
            cstat: ScorecardStatus = "ok"
            cdetail = "Diagnostics circuit is closed (nominal)."
        elif circuit in {"degraded", "half_open"}:
            cstat = "warn"
            cdetail = f"Circuit state: {diagnostics.circuit_state}."
        elif circuit == "open":
            cstat = "fail"
            cdetail = f"Circuit state: {diagnostics.circuit_state}."
        else:
            cstat = "warn"
            cdetail = f"Unknown circuit state: {diagnostics.circuit_state!r}."
        checks.append(
            ScorecardCheck(
                id="diagnostics_circuit",
                title="Diagnostics circuit",
                status=cstat,
                detail=cdetail,
                ticket_hint="Run `tapps-brain diagnostics health` and review scorecard dimensions.",
            )
        )
        score = float(diagnostics.composite_score)
        if score >= _SCORE_OK_MIN:
            sstat: ScorecardStatus = "ok"
        elif score >= _SCORE_WARN_MIN:
            sstat = "warn"
        else:
            sstat = "fail"
        checks.append(
            ScorecardCheck(
                id="diagnostics_composite",
                title="Diagnostics composite score",
                status=sstat,
                detail=f"Composite score {score:.2f} (0-1).",
                ticket_hint="Inspect diagnostics history and recall quality signals.",
            )
        )

    tampered = int(getattr(report, "integrity_tampered", 0) or 0)
    if tampered == 0:
        checks.append(
            ScorecardCheck(
                id="integrity_tampered",
                title="Integrity (tampered)",
                status="ok",
                detail="No tampered integrity hashes reported.",
                ticket_hint="",
            )
        )
    elif bool(getattr(report, "integrity_likely_key_mismatch", False)):
        # TAP-4331: every hashed entry failed -> signing-key mismatch (data
        # restored under a different ~/.tapps-brain/integrity.key), not tampering.
        checks.append(
            ScorecardCheck(
                id="integrity_tampered",
                title="Integrity (key mismatch)",
                status="warn",
                detail=(
                    f"All {tampered} entr(y/ies) failed verification — likely a signing-key "
                    "mismatch (data restored under a different integrity key), not tampering."
                ),
                ticket_hint=(
                    "If the data is trusted, re-sign under the current key: "
                    "`tapps-brain maintenance resign-integrity`."
                ),
            )
        )
    else:
        checks.append(
            ScorecardCheck(
                id="integrity_tampered",
                title="Integrity (tampered)",
                status="fail",
                detail=f"{tampered} entr(y/ies) failed integrity verification.",
                ticket_hint=(
                    "Run store maintenance / verify_integrity; do not ignore on shared stores."
                ),
            )
        )

    no_hash = int(getattr(report, "integrity_no_hash", 0) or 0)
    if no_hash > 0:
        checks.append(
            ScorecardCheck(
                id="integrity_no_hash",
                title="Integrity (missing hash)",
                status="warn",
                detail=(
                    f"{no_hash} entr(y/ies) have no integrity hash (legacy or pending backfill)."
                ),
                ticket_hint="Consider re-saving or running migration path if hashes are expected.",
            )
        )
    else:
        checks.append(
            ScorecardCheck(
                id="integrity_no_hash",
                title="Integrity (missing hash)",
                status="ok",
                detail="No entries missing integrity hashes.",
                ticket_hint="",
            )
        )

    if max_entries > 0:
        ratio = entry_count / max_entries
        if ratio >= _CAP_FAIL_RATIO:
            cap_stat: ScorecardStatus = "fail"
            cap_detail = (
                f"Store at {ratio * 100:.0f}% of max_entries ({entry_count}/{max_entries})."
            )
        elif ratio >= _CAP_WARN_RATIO:
            cap_stat = "warn"
            cap_detail = (
                f"Store at {ratio * 100:.0f}% of max_entries ({entry_count}/{max_entries})."
            )
        else:
            cap_stat = "ok"
            cap_detail = (
                f"Capacity {ratio * 100:.0f}% of max_entries ({entry_count}/{max_entries})."
            )
        checks.append(
            ScorecardCheck(
                id="store_capacity",
                title="Store capacity",
                status=cap_stat,
                detail=cap_detail,
                ticket_hint="Raise max_entries in profile or archive/GC if appropriate.",
            )
        )

    rma = int(getattr(report, "rate_limit_minute_anomalies", 0) or 0)
    rla = int(getattr(report, "rate_limit_lifetime_anomalies", 0) or 0)
    if rma == 0 and rla == 0:
        checks.append(
            ScorecardCheck(
                id="rate_limits",
                title="Rate limit anomalies",
                status="ok",
                detail="No minute/lifetime rate-limit anomalies recorded.",
                ticket_hint="",
            )
        )
    else:
        checks.append(
            ScorecardCheck(
                id="rate_limits",
                title="Rate limit anomalies",
                status="warn",
                detail=f"Minute anomalies: {rma}; lifetime anomalies: {rla}.",
                ticket_hint="Review burst writes and profile rate_limit settings.",
            )
        )

    gc_c = int(getattr(report, "gc_candidates", 0) or 0)
    cons_c = int(getattr(report, "consolidation_candidates", 0) or 0)
    backlog = gc_c + cons_c
    if backlog > _MAINT_BACKLOG_WARN:
        mb_stat: ScorecardStatus = "warn"
        mb_detail = f"Maintenance backlog: {gc_c} GC + {cons_c} consolidation candidate(s)."
    elif backlog > 0:
        mb_stat = "info"
        mb_detail = f"Some maintenance candidates: {gc_c} GC, {cons_c} consolidation."
    else:
        mb_stat = "ok"
        mb_detail = "No GC or consolidation candidates reported."
    checks.append(
        ScorecardCheck(
            id="maintenance_backlog",
            title="Maintenance backlog",
            status=mb_stat,
            detail=mb_detail,
            ticket_hint="Run GC / consolidation when maintenance windows allow.",
        )
    )

    if hive_attached and not hive_health.connected:
        checks.append(
            ScorecardCheck(
                id="hive_hub",
                title="Hive hub reachability",
                status="warn",
                detail="Store has Hive injection but hub snapshot could not connect.",
                ticket_hint="Check ~/.tapps-brain/hive/, AgentRegistry, and Hive CLI health.",
            )
        )
    elif hive_attached and hive_health.connected:
        if hive_health.agents == 0:
            checks.append(
                ScorecardCheck(
                    id="hive_hub",
                    title="Hive hub reachability",
                    status="warn",
                    detail="Hub connected but no agents registered.",
                    ticket_hint="Register agents via `tapps-brain agent register` or equivalent.",
                )
            )
        else:
            checks.append(
                ScorecardCheck(
                    id="hive_hub",
                    title="Hive hub reachability",
                    status="ok",
                    detail=(
                        f"Hub connected; {hive_health.agents} agent(s), "
                        f"{hive_health.entries} shared entr(y/ies)."
                    ),
                    ticket_hint="",
                )
            )
    else:
        checks.append(
            ScorecardCheck(
                id="hive_hub",
                title="Hive hub reachability",
                status="info",
                detail="Hive not injected on this store (local-only mode) or hub not queried.",
                ticket_hint="",
            )
        )

    if retrieval_mode == "hybrid_pgvector_hnsw":
        rstat: ScorecardStatus = "ok"
        rdetail = "Hybrid BM25 + pgvector HNSW active."
    elif retrieval_mode == "bm25_only":
        rstat = "info"
        rdetail = "BM25-only retrieval (vector stack unavailable or empty)."
    elif retrieval_mode == "hybrid_pgvector_empty":
        rstat = "warn"
        rdetail = (
            "pgvector HNSW index ready but no embedded rows yet — vector leg may run on the fly."
        )
    elif retrieval_mode == "hybrid_on_the_fly_embeddings":
        rstat = "info"
        rdetail = "Hybrid without precomputed pgvector HNSW; vectors computed on demand."
    elif retrieval_mode == "unknown":
        rstat = "warn"
        rdetail = "Could not classify retrieval mode."
    else:
        rstat = "info"
        rdetail = f"Retrieval mode: {retrieval_mode}."
    checks.append(
        ScorecardCheck(
            id="retrieval_stack",
            title="Retrieval stack",
            status=rstat,
            detail=rdetail,
            ticket_hint=("pgvector HNSW + tsvector hybrid; see docs/guides/postgres-dsn.md."),
        )
    )

    return checks


def _collect_velocity(store: MemoryStore) -> MemoryVelocity:
    """Best-effort velocity counts from Postgres ``private_memories`` (never raises).

    Returns :class:`MemoryVelocity` with all zeros when the store backend has no
    Postgres connection manager (e.g. the in-process unit-test backend).
    """
    try:
        backend = store._persistence
        cm = getattr(backend, "_cm", None)
        project_id = getattr(backend, "_project_id", None)
        agent_id = getattr(backend, "_agent_id", None)
        if cm is None or project_id is None or agent_id is None:
            return MemoryVelocity()
        with cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE created_at > NOW() - INTERVAL '1 hour'
                    ) AS writes_1h,
                    COUNT(*) FILTER (
                        WHERE created_at > NOW() - INTERVAL '24 hours'
                    ) AS writes_24h,
                    COUNT(*) FILTER (
                        WHERE last_accessed > NOW() - INTERVAL '1 hour'
                          AND last_accessed != created_at
                    ) AS recalls_1h,
                    COUNT(*) FILTER (
                        WHERE last_accessed > NOW() - INTERVAL '24 hours'
                          AND last_accessed != created_at
                    ) AS recalls_24h
                FROM private_memories
                WHERE project_id = %s AND agent_id = %s
                """,
                (project_id, agent_id),
            )
            row = cur.fetchone()
        if row is None:
            return MemoryVelocity()
        return MemoryVelocity(
            writes_1h=int(row[0] or 0),
            writes_24h=int(row[1] or 0),
            recalls_1h=int(row[2] or 0),
            recalls_24h=int(row[3] or 0),
        )
    except Exception:
        return MemoryVelocity()


def _collect_retrieval_metrics() -> RetrievalMetrics:
    """Read in-process retrieval counters from otel_tracer accumulators.

    Returns :class:`RetrievalMetrics` with zeros when the module is absent or
    any error occurs — this helper never raises.
    """
    try:
        from tapps_brain.otel_tracer import get_retrieval_meter_snapshot

        snap = get_retrieval_meter_snapshot()
        return RetrievalMetrics(
            total_queries=int(snap.get("total_queries", 0)),
            bm25_hits=int(snap.get("bm25_hits", 0)),
            vector_hits=int(snap.get("vector_hits", 0)),
            rrf_fusions=int(snap.get("rrf_fusions", 0)),
            mean_latency_ms=float(snap.get("mean_latency_ms", 0.0)),
        )
    except Exception:
        return RetrievalMetrics()


def _collect_retrieval_detail(store: MemoryStore) -> RetrievalDetail:
    """Build latency percentiles/histogram and optional embedding model id."""
    detail = RetrievalDetail()
    try:
        from tapps_brain.otel_tracer import get_retrieval_latency_detail

        lat = get_retrieval_latency_detail()
        p50 = lat.get("latency_p50_ms")
        p95 = lat.get("latency_p95_ms")
        p99 = lat.get("latency_p99_ms")
        detail.latency_p50_ms = float(p50) if isinstance(p50, (int, float)) else None
        detail.latency_p95_ms = float(p95) if isinstance(p95, (int, float)) else None
        detail.latency_p99_ms = float(p99) if isinstance(p99, (int, float)) else None
        raw_hist = lat.get("latency_histogram")
        if isinstance(raw_hist, list) and raw_hist:
            detail.latency_histogram = [
                LatencyHistogramBucket(
                    label=str(row.get("label", "")),
                    count=int(row.get("count", 0)),
                )
                for row in raw_hist
                if isinstance(row, dict)
            ] or None
    except Exception:
        pass

    try:
        provider = getattr(store, "_embedding_provider", None)
        model_id = getattr(provider, "model_id", None) if provider is not None else None
        if model_id:
            detail.embedding_model = str(model_id)
    except Exception:
        pass

    return detail


def _maybe_refresh_diagnostics(store: MemoryStore) -> None:
    """Throttled diagnostics refresh for the dashboard (TAP-4332).

    Records a fresh diagnostics row at most once per :data:`_DIAG_REFRESH_TTL_SEC`
    so the dashboard's composite score / circuit state stay current without running
    diagnostics on every poll.  Cheap now that the duplication/staleness dimensions
    skip the O(n^2) consolidation scan.  Best-effort — never raises on the snapshot
    path.  ``run_remediation=False`` keeps this read-driven refresh from triggering
    remediation side effects.
    """
    try:
        latest = store.diagnostics_history(limit=1)
    except Exception:
        return
    if latest:
        raw = str(latest[0].get("recorded_at") or "")
        try:
            recorded = datetime.fromisoformat(raw)
        except ValueError:
            recorded = None
        if recorded is not None:
            if recorded.tzinfo is None:
                recorded = recorded.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - recorded).total_seconds()
            if age < _DIAG_REFRESH_TTL_SEC:
                return
    with contextlib.suppress(Exception):
        store.diagnostics(record_history=True, run_remediation=False)


def build_visual_snapshot(
    store: MemoryStore,
    *,
    skip_diagnostics: bool = False,
    privacy: PrivacyTier = "standard",
) -> VisualSnapshot:
    """Build a versioned visual snapshot from an open store."""
    from tapps_brain.health_check import retrieval_health_slice

    # EPIC-078 / TAP-4330+4332: the O(n^2) consolidation similarity scan in
    # health() can take minutes on a full store (thousands of entries) and 504 the
    # /snapshot endpoint.  Run the live gauge scan inline only for normal-sized
    # stores; above the cap reuse the cached gauge so the request never stalls.
    report = store.health(consolidation_scan_max_entries=_SNAPSHOT_INLINE_SCAN_MAX_ENTRIES)
    hdump = _redact_health(report.model_dump(mode="json"), privacy)
    aggregates = _try_load_snapshot_aggregates(store)
    if aggregates is not None:
        agent_scopes = _agent_scope_counts_from_aggregates(aggregates)
        mg_count, mg_counts = _memory_group_stats_from_aggregates(aggregates, privacy=privacy)
        access_stats = aggregates.access_stats
        tag_stats: list[TagStat] | None = (
            _tag_stats_from_counts(aggregates.tag_counts, limit=_TOP_TAGS_LIMIT)
            if privacy == "local"
            else None
        )
    else:
        entries = store.list_all()
        agent_scopes = _agent_scope_counts_from_entries(entries)
        mg_count, mg_counts = _memory_group_stats(entries, privacy=privacy)
        access_stats = _access_stats_from_entries(entries)
        tag_stats = _tag_stats_local(entries, limit=_TOP_TAGS_LIMIT) if privacy == "local" else None
    hive_on = _hive_attached(store)

    mode, summary = retrieval_health_slice(store)
    _raw_n = getattr(store, "vector_row_count", 0)
    sv_n = int(_raw_n() if callable(_raw_n) else _raw_n)

    identity: dict[str, object] = {
        "identity_schema_version": _IDENTITY_SCHEMA_VERSION,
        "agent_scope_counts": agent_scopes,
        "entry_count": report.entry_count,
        "federation_enabled": report.federation_enabled,
        "hive_attached": hive_on,
        "profile_name": report.profile_name,
        "schema_version": report.schema_version,
        "store_path": report.store_path if privacy != "strict" else "<redacted>",
        "tier_distribution": dict(sorted(report.tier_distribution.items())),
        "memory_group_count": mg_count,
    }
    fingerprint = compute_fingerprint_hex(identity)
    theme = theme_from_fingerprint(fingerprint)

    # EPIC-078: do NOT recompute diagnostics on every /snapshot request.  A live
    # run used to execute the O(n^2) consolidation scan (the duplication + staleness
    # dimensions each called store.health()).  TAP-4332 made those dimensions skip
    # the scan, so a refresh is now O(n); we throttle it to once per TTL and read
    # the persisted row.  Stays None until the first row is recorded.
    diagnostics: DiagnosticsSummary | None = None
    if not skip_diagnostics:
        _maybe_refresh_diagnostics(store)
        try:
            _latest_diag = store.diagnostics_history(limit=1)
        except Exception:
            _latest_diag = []
        if _latest_diag:
            _drow = _latest_diag[0]
            score = float(_drow.get("composite_score") or 0.0)
            diagnostics = DiagnosticsSummary(
                composite_score=min(1.0, max(0.0, score)),
                circuit_state=str(_drow.get("circuit_state") or "closed"),
                recorded_at=str(_drow.get("recorded_at") or ""),
            )

    velocity = _collect_velocity(store)

    hive_health = _collect_hive_health(store)

    # Collect agent registry (separate connection; best-effort, never raises).
    agent_registry: list[AgentEntry] = []
    try:
        from tapps_brain.backends import resolve_hive_backend_from_env

        _hive_for_agents = resolve_hive_backend_from_env()
        if _hive_for_agents is not None:
            try:
                agent_registry = _collect_agent_registry(_hive_for_agents, privacy=privacy)
            finally:
                _hive_for_agents.close()
    except Exception:  # nosec B110 — hive agent registry unavailable; snapshot continues without it
        pass

    # STORY-069.7: include recent diagnostics history + feedback events with
    # project_id so /snapshot?project= can filter per-tenant views.  Both are
    # best-effort and must never fail the snapshot build.
    diagnostics_history: list[dict[str, Any]] = []
    try:
        _diag_hist = store.diagnostics_history(limit=100)
        for _row in _diag_hist:
            if "project_id" not in _row:
                _row["project_id"] = getattr(store, "_project_id", None)
            diagnostics_history.append(_row)
    except Exception:
        diagnostics_history = []

    feedback_events: list[dict[str, Any]] = []
    try:
        _events = store.query_feedback(limit=200)
        for _ev in _events:
            _dump = _ev.model_dump(mode="json")
            # Do NOT impute project_id here — preserve None for legacy/unknown
            # rows so /snapshot?project=<id> can safely exclude them.
            _dump.setdefault("project_id", None)
            feedback_events.append(_dump)
    except Exception:
        feedback_events = []

    scorecard = _build_scorecard(
        report,
        diagnostics=diagnostics,
        hive_attached=hive_on,
        hive_health=hive_health,
        retrieval_mode=mode,
        skip_diagnostics=skip_diagnostics,
    )

    now = datetime.now(tz=UTC).isoformat()
    return VisualSnapshot(
        generated_at=now,
        fingerprint_sha256=fingerprint,
        identity_schema_version=_IDENTITY_SCHEMA_VERSION,
        privacy_tier=privacy,
        privacy=_privacy_copy(privacy),
        health=hdump,
        agent_scope_counts=agent_scopes,
        hive_attached=hive_on,
        hive_health=hive_health,
        retrieval_effective_mode=mode,
        retrieval_summary=summary,
        retrieval_metrics=_collect_retrieval_metrics(),
        retrieval=_collect_retrieval_detail(store),
        vector_index_enabled=True,
        vector_index_rows=sv_n,
        memory_group_count=mg_count,
        memory_group_counts=mg_counts,
        velocity=velocity,
        access_stats=access_stats,
        tag_stats=tag_stats,
        diagnostics=diagnostics,
        scorecard=scorecard,
        agent_registry=agent_registry,
        diagnostics_history=diagnostics_history,
        feedback_events=feedback_events,
        theme=theme,
    )


def snapshot_to_json(snapshot: VisualSnapshot) -> str:
    """Serialize snapshot with stable key order for diff-friendly exports."""
    data = snapshot.model_dump(mode="json")
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def capture_png(  # pragma: no cover
    html_path: Path,
    json_path: Path,
    output: Path,
    *,
    width: int = 1280,
    height: int = 900,
    theme: str = "light",
    wait_ms: int = 600,
) -> None:
    """Capture a headless PNG of the brain-visual dashboard.

    Opens ``html_path`` (the static demo's ``index.html``) in a headless
    Chromium browser, injects the snapshot from ``json_path`` via
    ``applySnapshot()``, and writes a full-page screenshot to ``output``.

    Requires the ``visual`` optional extra (Playwright)::

        uv sync --extra visual
        playwright install chromium

    Args:
        html_path: Path to ``examples/brain-visual/index.html``.
        json_path: Path to a ``brain-visual.json`` snapshot.
        output: Destination PNG file path.
        width: Viewport width in pixels (default 1280).
        height: Viewport height in pixels (default 900).
        theme: ``"light"`` (default) or ``"dark"``.
        wait_ms: Extra settle time after snapshot injection (default 600 ms).

    Raises:
        RuntimeError: When Playwright is not installed.
        FileNotFoundError: When ``html_path`` or ``json_path`` do not exist.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as err:
        raise RuntimeError(
            "PNG capture requires Playwright. "
            "Install with: uv sync --extra visual && playwright install chromium"
        ) from err

    if not html_path.exists():
        raise FileNotFoundError(f"HTML not found: {html_path}")
    if not json_path.exists():
        raise FileNotFoundError(f"Snapshot JSON not found: {json_path}")

    snap_data = json.loads(json_path.read_text(encoding="utf-8"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(
                f"file://{html_path.resolve()}",
                wait_until="domcontentloaded",
                timeout=15_000,
            )
            if theme == "dark":
                page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
            page.evaluate("(d) => applySnapshot(d)", snap_data)
            page.wait_for_timeout(wait_ms)
            output.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output), full_page=True)
        finally:
            browser.close()
