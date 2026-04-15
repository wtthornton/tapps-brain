"""Quality diagnostics, anomaly detection, and circuit breaker (EPIC-030).

Computes per-dimension scores (0.0–1.0), composite health, optional SQLite
history, EWMA-based anomalies, and a four-state circuit breaker.
"""

from __future__ import annotations

import importlib
import json
import math
import random
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

import structlog
from pydantic import BaseModel, Field

from tapps_brain._protocols import HealthDimension

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DimensionScore(BaseModel):
    """Normalized score for a single health dimension."""

    name: str
    score: float = Field(ge=0.0, le=1.0, description="Goalpost-normalized 0–1.")
    raw_details: dict[str, Any] = Field(default_factory=dict)


class DiagnosticsReport(BaseModel):
    """Full diagnostics snapshot for a memory store."""

    composite_score: float = Field(ge=0.0, le=1.0)
    dimensions: dict[str, DimensionScore]
    recorded_at: str
    recommendations: list[str] = Field(default_factory=list)
    hive_diagnostics: dict[str, dict[str, DimensionScore]] = Field(default_factory=dict)
    hive_composite_score: float | None = None
    circuit_state: str = "closed"
    gap_count: int = 0
    correlation_adjusted: bool = False
    anomalies: list[dict[str, Any]] = Field(default_factory=list)


class DiagnosticsConfig(BaseModel):
    """Host configuration for diagnostics (profile YAML)."""

    retention_days: int = Field(default=90, ge=1, le=3650)
    custom_dimension_paths: list[str] = Field(
        default_factory=list,
        description="Importable dotted paths to HealthDimension callables/classes.",
    )
    dimension_weights: dict[str, float] = Field(
        default_factory=dict,
        description="Optional per-dimension weight overrides (re-normalized to sum 1).",
    )


class CircuitState(StrEnum):
    """Circuit breaker states (STORY-030.4)."""

    CLOSED = "closed"
    DEGRADED = "degraded"
    OPEN = "open"
    HALF_OPEN = "half_open"


# ---------------------------------------------------------------------------
# Weight helpers
# ---------------------------------------------------------------------------


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Normalize positive weights to sum to 1.0."""
    pos = {k: max(0.0, v) for k, v in weights.items()}
    s = sum(pos.values())
    if s <= 0:
        n = len(pos)
        return dict.fromkeys(pos, 1.0 / n) if n else {}
    return {k: v / s for k, v in pos.items()}


def pearson_r(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation; None if undefined."""
    n = min(len(xs), len(ys))
    if n < 2:
        return None
    a = xs[-n:]
    b = ys[-n:]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    num = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    den_a = math.sqrt(sum((a[i] - mean_a) ** 2 for i in range(n)))
    den_b = math.sqrt(sum((b[i] - mean_b) ** 2 for i in range(n)))
    if den_a == 0 or den_b == 0:
        return None
    return num / (den_a * den_b)


def adjust_weights_for_correlation(
    dim_names: list[str],
    base_weights: dict[str, float],
    history_rows: list[dict[str, Any]],
    *,
    min_rows: int = 20,
    corr_threshold: float = 0.7,
    reduction: float = 0.3,
) -> tuple[dict[str, float], bool]:
    """Down-weight correlated dimension pairs; redistribute mass.

    Each *history_rows* item should map dimension name -> score (float).
    """
    if len(history_rows) < min_rows:
        return base_weights, False
    series: dict[str, list[float]] = {d: [] for d in dim_names}
    for row in history_rows:
        dims = row.get("dimensions") or row.get("dimension_scores") or {}
        if isinstance(dims, str):
            try:
                dims = json.loads(dims)
            except json.JSONDecodeError:
                continue
        if not isinstance(dims, dict):
            continue
        for d in dim_names:
            v = dims.get(d)
            if isinstance(v, (int, float)):
                series[d].append(float(v))
    lengths = [len(series[d]) for d in dim_names]
    if min(lengths) < min_rows:
        return base_weights, False

    w = dict(base_weights)
    adjusted = False
    factor = 1.0 - reduction / 2
    for i, da in enumerate(dim_names):
        for db in dim_names[i + 1 :]:
            r = pearson_r(series[da], series[db])
            if r is not None and r > corr_threshold:
                w[da] = max(0.0, w.get(da, 0) * factor)
                w[db] = max(0.0, w.get(db, 0) * factor)
                adjusted = True
    if adjusted:
        w = normalize_weights(w)
    return w, adjusted


# ---------------------------------------------------------------------------
# Built-in dimension implementations
# ---------------------------------------------------------------------------


@dataclass
class _Dim:
    """Simple HealthDimension wrapper."""

    _name: str
    _weight: float
    _fn: Any

    @property
    def name(self) -> str:
        return self._name

    @property
    def default_weight(self) -> float:
        return self._weight

    def check(self, store: MemoryStore) -> DimensionScore:
        return cast("DimensionScore", self._fn(store))


def _retrieval_effectiveness(store: MemoryStore) -> DimensionScore:
    entries = store.list_all()
    if not entries:
        return DimensionScore(name="retrieval_effectiveness", score=1.0, raw_details={})
    hit_rate = sum(1 for e in entries if e.access_count > 0) / len(entries)
    mean_conf = sum(e.confidence for e in entries) / len(entries)
    intrinsic = clamp01(0.55 * hit_rate + 0.45 * mean_conf)
    fb_score: float | None = None
    try:
        evs = store.query_feedback(event_type="recall_rated", limit=500)
    except Exception:
        evs = []
    if evs:
        rmap = {"helpful": 1.0, "partial": 0.5, "irrelevant": 0.0, "outdated": 0.0}
        vals = []
        for ev in evs:
            d = ev.details if isinstance(ev.details, dict) else {}
            r = str(d.get("rating", "helpful"))
            vals.append(rmap.get(r, 0.5))
        fb_score = sum(vals) / len(vals)
    score = clamp01(0.4 * fb_score + 0.6 * intrinsic) if fb_score is not None else intrinsic
    return DimensionScore(
        name="retrieval_effectiveness",
        score=score,
        raw_details={"hit_rate": hit_rate, "feedback_blend": fb_score is not None},
    )


def _freshness(store: MemoryStore) -> DimensionScore:
    from datetime import datetime

    from tapps_brain.decay import DecayConfig, _get_half_life
    from tapps_brain.models import MemoryTier

    entries = store.list_all()
    if not entries:
        return DimensionScore(name="freshness", score=1.0, raw_details={})
    cfg = DecayConfig()
    now = datetime.now(tz=UTC)
    scores: list[float] = []
    for e in entries:
        tier = e.tier if isinstance(e.tier, MemoryTier) else MemoryTier(str(e.tier))
        hl = max(1, _get_half_life(tier, cfg))
        try:
            raw = e.created_at.replace("Z", "+00:00")
            created = datetime.fromisoformat(raw)
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            age_days = max(0.0, (now - created).total_seconds() / 86400.0)
        except (ValueError, TypeError, AttributeError):
            age_days = 0.0
        scores.append(clamp01(math.exp(-age_days / float(hl))))
    avg = sum(scores) / len(scores)
    return DimensionScore(name="freshness", score=avg, raw_details={"entries": len(entries)})


def _completeness(store: MemoryStore) -> DimensionScore:
    entries = store.list_all()
    if not entries:
        return DimensionScore(name="completeness", score=1.0, raw_details={})
    ok = 0
    for e in entries:
        if e.value and str(e.value).strip() and e.source_agent and str(e.source_agent).strip():
            ok += 1
    ratio = ok / len(entries)
    return DimensionScore(name="completeness", score=ratio, raw_details={"filled": ok})


def _duplication(store: MemoryStore) -> DimensionScore:
    rep = store.health()
    n = max(1, rep.entry_count)
    ratio = min(1.0, rep.consolidation_candidates / n)
    return DimensionScore(
        name="duplication",
        score=clamp01(1.0 - ratio),
        raw_details={"candidates": rep.consolidation_candidates},
    )


def _staleness(store: MemoryStore) -> DimensionScore:
    rep = store.health()
    n = max(1, rep.entry_count)
    ratio = min(1.0, rep.gc_candidates / n)
    return DimensionScore(
        name="staleness",
        score=clamp01(1.0 - ratio),
        raw_details={"gc_candidates": rep.gc_candidates},
    )


def _integrity_dim(store: MemoryStore) -> DimensionScore:
    v = store.verify_integrity()
    total = int(v.get("total", 0))
    if total == 0:
        return DimensionScore(name="integrity", score=1.0, raw_details=v)
    tampered = int(v.get("tampered", 0))
    verified = int(v.get("verified", 0))
    denom = verified + tampered
    if denom == 0:
        score = 1.0
    else:
        score = verified / denom
    return DimensionScore(name="integrity", score=clamp01(score), raw_details=v)


def default_builtin_dimensions() -> list[HealthDimension]:
    return [
        _Dim("retrieval_effectiveness", 0.22, _retrieval_effectiveness),
        _Dim("freshness", 0.18, _freshness),
        _Dim("completeness", 0.12, _completeness),
        _Dim("duplication", 0.15, _duplication),
        _Dim("staleness", 0.15, _staleness),
        _Dim("integrity", 0.18, _integrity_dim),
    ]


def load_custom_dimensions(paths: list[str]) -> list[HealthDimension]:
    out: list[HealthDimension] = []
    for p in paths:
        mod_name, _, attr = p.rpartition(".")
        if not mod_name or not attr:
            continue
        mod = importlib.import_module(mod_name)
        obj = getattr(mod, attr, None)
        if obj is None:
            continue
        dim: HealthDimension | None = None
        if isinstance(obj, type):
            try:
                inst = obj()
            except TypeError:
                continue
            if isinstance(inst, HealthDimension):
                dim = inst
        elif callable(obj):
            cand = obj()
            if isinstance(cand, HealthDimension):
                dim = cand
        elif isinstance(obj, HealthDimension):
            dim = obj
        if dim is not None:
            out.append(dim)
    return out


def _hive_namespace_scores(
    store: MemoryStore,
) -> tuple[dict[str, dict[str, DimensionScore]], float | None]:
    hs = getattr(store, "_hive_store", None)
    if hs is None:
        return {}, None
    out: dict[str, dict[str, DimensionScore]] = {}
    profile = getattr(store, "profile", None)
    ns_list = ["universal"]
    if profile is not None:
        name = getattr(profile, "name", None)
        if isinstance(name, str) and name:
            ns_list.append(name)
    worst = 1.0
    for ns in ns_list:
        try:
            rows = hs.search("a", namespaces=[ns], limit=5, min_confidence=0.0)
        except Exception:
            rows = []
        n = len(rows)
        freshness = clamp01(1.0 if n > 0 else 0.4)
        dup = DimensionScore(name="hive_duplication", score=0.85, raw_details={"namespace": ns})
        fb = DimensionScore(name="hive_feedback", score=0.9, raw_details={"namespace": ns})
        fr = DimensionScore(name="hive_freshness", score=freshness, raw_details={"hits": n})
        out[ns] = {"freshness": fr, "duplication": dup, "feedback": fb}
        local_worst = min(fr.score, dup.score, fb.score)
        worst = min(worst, local_worst)
    return out, worst


def run_diagnostics(
    store: MemoryStore,
    *,
    dimensions: list[HealthDimension] | None = None,
    config: DiagnosticsConfig | None = None,
    history_for_correlation: list[dict[str, Any]] | None = None,
) -> DiagnosticsReport:
    """Compute dimension scores and composite (EPIC-030)."""
    cfg = config or DiagnosticsConfig()
    dims = list(dimensions) if dimensions is not None else default_builtin_dimensions()
    dims.extend(load_custom_dimensions(cfg.custom_dimension_paths))
    dim_names = [d.name for d in dims]
    weights = {d.name: d.default_weight for d in dims}
    weights.update(cfg.dimension_weights)
    weights = normalize_weights(weights)
    corr_adj = False
    if history_for_correlation is not None:
        weights, corr_adj = adjust_weights_for_correlation(
            dim_names, weights, history_for_correlation
        )
    scores: dict[str, DimensionScore] = {}
    for d in dims:
        try:
            scores[d.name] = d.check(store)
        except Exception:
            logger.debug("diagnostics_dimension_failed", dimension=d.name, exc_info=True)
            scores[d.name] = DimensionScore(name=d.name, score=0.5, raw_details={"error": True})
    composite = sum(weights.get(n, 0) * scores[n].score for n in scores)
    composite = clamp01(composite)
    hive_diag, hive_comp = _hive_namespace_scores(store)
    gaps = 0
    try:
        gaps = len(store.query_feedback(event_type="gap_reported", limit=5000))
    except Exception:
        logger.debug("diagnostics_gap_count_failed", exc_info=True)
    recs: list[str] = []
    gap_line: str | None = None
    try:
        from tapps_brain.flywheel import knowledge_gap_summary_for_diagnostics

        gap_line = knowledge_gap_summary_for_diagnostics(store)
    except Exception:
        gap_line = None
    if gap_line:
        recs.append(gap_line)
    elif gaps:
        recs.append(f"{gaps} knowledge gap(s) reported — consider capturing missing facts.")
    if composite < 0.6:
        recs.append("Composite score below 0.6 — review dimension breakdown.")
    now = datetime.now(tz=UTC).isoformat()
    return DiagnosticsReport(
        composite_score=composite,
        dimensions=scores,
        recorded_at=now,
        recommendations=recs,
        hive_diagnostics=hive_diag,
        hive_composite_score=hive_comp,
        gap_count=gaps,
        correlation_adjusted=corr_adj,
    )


# ---------------------------------------------------------------------------
# Diagnostics history (Postgres — migrations/private/004_diagnostics_history.sql)
# ---------------------------------------------------------------------------


class DiagnosticsHistoryStore:
    """Append-only diagnostics history scoped to ``(project_id, agent_id)``.

    Persisted in the Postgres ``diagnostics_history`` table created by
    migration 004.  Reuses the connection manager from
    :class:`~tapps_brain.postgres_private.PostgresPrivateBackend`.
    """

    def __init__(
        self,
        connection_manager: Any,  # PostgresConnectionManager  # noqa: ANN401
        *,
        project_id: str,
        agent_id: str,
    ) -> None:
        self._cm = connection_manager
        self._project_id = project_id
        self._agent_id = agent_id
        self._lock = threading.Lock()

    def close(self) -> None:
        return None

    def record(self, report: DiagnosticsReport, *, circuit_state: str = "closed") -> str:
        rid = str(uuid.uuid4())
        dim_json = json.dumps({k: v.score for k, v in report.dimensions.items()})
        payload = json.dumps(report.model_dump(mode="json"), default=str)
        with self._lock, self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO diagnostics_history (
                    project_id, agent_id, id, recorded_at, composite_score,
                    dimension_scores, circuit_state, full_report
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
                """,
                (
                    self._project_id,
                    self._agent_id,
                    rid,
                    report.recorded_at,
                    report.composite_score,
                    dim_json,
                    circuit_state,
                    payload,
                ),
            )
        return rid

    def history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, recorded_at, composite_score, dimension_scores,
                       circuit_state, full_report
                FROM diagnostics_history
                WHERE project_id = %s AND agent_id = %s
                ORDER BY recorded_at DESC
                LIMIT %s
                """,
                (self._project_id, self._agent_id, limit),
            )
            rows = cur.fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            recorded = r[1]
            recorded_str = recorded.isoformat() if hasattr(recorded, "isoformat") else str(recorded)
            dim_raw = r[3]
            if isinstance(dim_raw, dict):
                dim_json_str = json.dumps(dim_raw)
            elif isinstance(dim_raw, str):
                dim_json_str = dim_raw
            else:
                dim_json_str = "{}"
            results.append(
                {
                    "id": str(r[0]),
                    "recorded_at": recorded_str,
                    "composite_score": float(r[2]),
                    "dimension_scores": dim_json_str,
                    "circuit_state": str(r[4]),
                    "full_report": r[5],
                }
            )
        return results

    def prune_older_than(self, days: int) -> int:
        cutoff = (datetime.now(tz=UTC) - timedelta(days=days)).isoformat()
        with self._lock, self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM diagnostics_history
                WHERE project_id = %s AND agent_id = %s AND recorded_at < %s
                """,
                (self._project_id, self._agent_id, cutoff),
            )
            return int(cur.rowcount or 0)

    def rolling_average(self, *, dimension: str, window: int = 20) -> float | None:
        rows = self.history(limit=window)
        if not rows:
            return None
        vals: list[float] = []
        for r in rows:
            try:
                d = json.loads(r["dimension_scores"])
                if dimension in d and isinstance(d[dimension], (int, float)):
                    vals.append(float(d[dimension]))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        if not vals:
            return None
        return sum(vals) / len(vals)


# ---------------------------------------------------------------------------
# In-memory diagnostics history (no-Postgres fallback for tests / local dev)
# ---------------------------------------------------------------------------


class InMemoryDiagnosticsHistoryStore:
    """Thread-safe in-memory diagnostics history — used when no Postgres ``cm``
    is available (e.g. ``InMemoryPrivateBackend`` in unit/integration tests).

    Provides the same public interface as :class:`DiagnosticsHistoryStore` so
    ``store.py`` can use either interchangeably.
    """

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def close(self) -> None:
        pass

    def record(self, report: DiagnosticsReport, *, circuit_state: str = "closed") -> str:
        rid = str(uuid.uuid4())
        dim_json = json.dumps({k: v.score for k, v in report.dimensions.items()})
        recorded = report.recorded_at
        recorded_str = recorded.isoformat() if hasattr(recorded, "isoformat") else str(recorded)
        row: dict[str, Any] = {
            "id": rid,
            "recorded_at": recorded_str,
            "composite_score": float(report.composite_score),
            "dimension_scores": dim_json,
            "circuit_state": circuit_state,
            "full_report": report.model_dump(mode="json"),
        }
        with self._lock:
            self._records.append(row)
        return rid

    def history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            # Most recent first, matching Postgres ORDER BY recorded_at DESC
            return list(reversed(self._records[-limit:])) if self._records else []

    def prune_older_than(self, days: int) -> int:
        cutoff = (datetime.now(tz=UTC) - timedelta(days=days)).isoformat()
        with self._lock:
            before = len(self._records)
            self._records = [r for r in self._records if r["recorded_at"] >= cutoff]
            return before - len(self._records)

    def rolling_average(self, *, dimension: str, window: int = 20) -> float | None:
        rows = self.history(limit=window)
        if not rows:
            return None
        vals: list[float] = []
        for r in rows:
            try:
                d = json.loads(r["dimension_scores"])
                if dimension in d and isinstance(d[dimension], (int, float)):
                    vals.append(float(d[dimension]))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return sum(vals) / len(vals) if vals else None


# ---------------------------------------------------------------------------
# Anomaly detection (EWMA)
# ---------------------------------------------------------------------------


@dataclass
class AnomalyDetector:
    """EWMA-based anomaly detector per dimension (STORY-030.2)."""

    lam: float = 0.2
    min_obs: int = 20
    warn_sigma: float = 2.0
    crit_sigma: float = 3.0
    confirm_window: int = 3
    _ewma: dict[str, float] = field(default_factory=dict)
    _var_ewma: dict[str, float] = field(default_factory=dict)
    _streak: dict[str, int] = field(default_factory=dict)
    _obs_count: dict[str, int] = field(default_factory=dict)

    def reset_from_history(self, rows: list[dict[str, Any]]) -> None:
        """Warm-start EWMA from diagnostics_history rows (oldest first)."""
        self._ewma.clear()
        self._var_ewma.clear()
        self._streak.clear()
        self._obs_count.clear()
        chronological = list(reversed(rows))
        for r in chronological:
            try:
                dims = json.loads(r["dimension_scores"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(dims, dict):
                continue
            for name, val in dims.items():
                if isinstance(val, (int, float)):
                    self._observe(name, float(val), mutate=True)
                    self._obs_count[name] = self._obs_count.get(name, 0) + 1

    def _observe(self, name: str, x: float, *, mutate: bool) -> None:
        prev = self._ewma.get(name)
        if prev is None:
            new_m = x
            new_v = 0.0
        else:
            new_m = self.lam * x + (1 - self.lam) * prev
            diff = x - prev
            new_v = self.lam * diff**2 + (1 - self.lam) * self._var_ewma.get(name, 0.0)
        if mutate:
            self._ewma[name] = new_m
            self._var_ewma[name] = new_v

    def detect(self, report: DiagnosticsReport) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        for name, ds in report.dimensions.items():
            self._observe(name, ds.score, mutate=True)
            self._obs_count[name] = self._obs_count.get(name, 0) + 1
            if self._obs_count[name] < self.min_obs:
                continue
            m = self._ewma.get(name)
            v = max(1e-9, self._var_ewma.get(name, 0.0))
            sigma = math.sqrt(v)
            if m is None or sigma < 1e-6:
                continue
            z = abs(ds.score - m) / sigma
            level = None
            if z >= self.crit_sigma:
                level = "threshold_critical"
            elif z >= self.warn_sigma:
                level = "threshold_warning"
            if level:
                self._streak[name] = self._streak.get(name, 0) + 1
            else:
                self._streak[name] = 0
            if level and self._streak[name] >= self.confirm_window:
                alerts.append(
                    {
                        "dimension": name,
                        "level": level,
                        "z_score": round(z, 4),
                        "recommendation": f"Investigate {name}: score deviated from EWMA baseline.",
                    },
                )
        return alerts


# ---------------------------------------------------------------------------
# Circuit breaker + remediation
# ---------------------------------------------------------------------------


@dataclass
class CircuitBreaker:
    """Four-state breaker from composite diagnostic score."""

    state: CircuitState = CircuitState.CLOSED
    half_open_probes: int = 0
    probes_required: int = 3
    _last_remediation_mono: dict[str, float] = field(default_factory=dict)
    cooldown_seconds: float = 3600.0

    def transition(self, composite: float) -> CircuitState:
        if self.state == CircuitState.HALF_OPEN:
            if composite >= 0.6:
                self.state = CircuitState.CLOSED
                self.half_open_probes = 0
            elif composite >= 0.3:
                self.state = CircuitState.DEGRADED
            else:
                self.state = CircuitState.OPEN
            return self.state
        if composite >= 0.6:
            self.state = CircuitState.CLOSED
        elif composite >= 0.3:
            self.state = CircuitState.DEGRADED
        else:
            self.state = CircuitState.OPEN
        return self.state

    def enter_half_open_if_cooled(self, now_mono: float) -> bool:
        if self.state != CircuitState.OPEN:
            return False
        last = self._last_remediation_mono.get("half_open", 0.0)
        jitter = random.random() * 30.0
        if now_mono - last >= self.cooldown_seconds + jitter:
            self.state = CircuitState.HALF_OPEN
            self.half_open_probes = 0
            self._last_remediation_mono["half_open"] = now_mono
            return True
        return False

    def record_probe(self, composite: float) -> None:
        self.half_open_probes += 1
        if self.half_open_probes >= self.probes_required and composite >= 0.45:
            self.state = CircuitState.DEGRADED


def maybe_remediate(
    store: MemoryStore,
    report: DiagnosticsReport,
    breaker: CircuitBreaker,
    *,
    now_mono: float,
) -> list[str]:
    """Tier-1 auto-remediation when OPEN (STORY-030.4)."""
    actions: list[str] = []
    if breaker.state != CircuitState.OPEN:
        return actions
    dup = report.dimensions.get("duplication")
    st = report.dimensions.get("staleness")
    inte = report.dimensions.get("integrity")

    def _cool_ok(key: str) -> bool:
        return now_mono - breaker._last_remediation_mono.get(key, 0.0) >= breaker.cooldown_seconds

    if dup and dup.score < 0.5 and _cool_ok("consolidate"):
        try:
            from tapps_brain.auto_consolidation import run_periodic_consolidation_scan

            run_periodic_consolidation_scan(
                store=store,
                project_root=store.project_root,
                threshold=0.72,
                min_group_size=3,
                force=True,
            )
            breaker._last_remediation_mono["consolidate"] = now_mono
            actions.append("consolidate")
        except Exception:
            logger.debug("remediation_consolidate_failed", exc_info=True)
    if st and st.score < 0.5 and _cool_ok("gc"):
        try:
            store.gc(dry_run=False)
            breaker._last_remediation_mono["gc"] = now_mono
            actions.append("gc")
        except Exception:
            logger.debug("remediation_gc_failed", exc_info=True)
    if inte and inte.score < 0.8 and _cool_ok("integrity_alert"):
        breaker._last_remediation_mono["integrity_alert"] = now_mono
        actions.append("integrity_alert")
    return actions


def hive_recall_multiplier(state: CircuitState) -> float:
    if state == CircuitState.CLOSED:
        return 1.0
    if state == CircuitState.DEGRADED:
        return 0.5
    if state == CircuitState.HALF_OPEN:
        return 0.5
    return 0.0
