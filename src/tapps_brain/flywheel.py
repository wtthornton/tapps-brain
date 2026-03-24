"""Continuous improvement flywheel: feedback → confidence, gaps, reports (EPIC-031)."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from pydantic import BaseModel, Field

from tapps_brain._protocols import ReportSection
from tapps_brain.feedback import FeedbackEvent
from tapps_brain.models import MemoryEntry, MemoryTier

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore


class _GapSearchStore(Protocol):
    """Minimal store surface for gap tier-weighting (local MemoryStore or Hive stub)."""

    def search(self, query: str) -> list[MemoryEntry]: ...


logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_FEEDBACK_CURSOR_KEY = "feedback_cursor"

_DEFAULT_TIER_VOLATILITY: dict[str, float] = {
    "architectural": 0.3,
    "pattern": 0.5,
    "procedural": 0.7,
    "context": 1.0,
}

_TIER_GAP_WEIGHT: dict[str, float] = {
    "architectural": 1.2,
    "pattern": 1.1,
    "procedural": 1.0,
    "context": 0.9,
}


class FlywheelConfig(BaseModel):
    """Tunable flywheel behaviour."""

    base_K: float = Field(default=1.0, gt=0.0)
    tier_volatility: dict[str, float] = Field(
        default_factory=lambda: dict(_DEFAULT_TIER_VOLATILITY)
    )
    min_confidence: float = Field(default=0.05, ge=0.0, le=1.0)
    store_self_report_memory: bool = Field(
        default=False,
        description="When True, persist generated reports as a context-tier system memory.",
    )
    custom_report_sections: list[Any] = Field(
        default_factory=list,
        description="Extra ReportSection instances registered at init time.",
    )
    hive_negative_project_threshold: int = Field(default=3, ge=1)


def beta_mean(positive: float, negative: float) -> float:
    """Beta posterior mean with Jeffreys prior Beta(0.5, 0.5)."""
    return (positive + 0.5) / (positive + negative + 1.0)


def _tier_str(tier: MemoryTier | str) -> str:
    return tier.value if isinstance(tier, MemoryTier) else str(tier)


def _token_set(text: str) -> set[str]:
    return {t for t in re.split(r"\s+", text.lower().strip()) if t}


def jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity on whitespace tokens."""
    sa, sb = _token_set(a), _token_set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))


def _parse_cursor(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    try:
        d = json.loads(raw)
        return (d.get("ts"), d.get("id"))
    except (json.JSONDecodeError, TypeError):
        return None, None


def _event_after_cursor(ev: FeedbackEvent, ts: str | None, eid: str | None) -> bool:
    if ts is None or eid is None:
        return True
    if ev.timestamp > ts:
        return True
    if ev.timestamp < ts:
        return False
    return ev.id > eid


def _feedback_deltas(ev: FeedbackEvent) -> list[tuple[str | None, float, float]]:
    """Map a feedback event to (entry_key, d_pos, d_neg) updates."""
    out: list[tuple[str | None, float, float]] = []
    ek = ev.entry_key
    et = ev.event_type
    if et == "recall_rated" and ek:
        rating = ""
        if isinstance(ev.details, dict):
            rating = str(ev.details.get("rating", ""))
        if rating == "helpful":
            out.append((ek, 1.0, 0.0))
        elif rating in ("irrelevant", "outdated"):
            out.append((ek, 0.0, 1.0))
        elif rating == "partial":
            pass
    elif et == "implicit_positive" and ek:
        out.append((ek, 1.0, 0.0))
    elif et == "implicit_negative" and ek:
        out.append((ek, 0.0, 0.2))
    elif et == "implicit_correction" and ek:
        out.append((ek, 0.0, 0.5))
    elif et == "issue_flagged" and ek:
        out.append((ek, 0.0, 1.0))
    return out


class FeedbackProcessor:
    """Bayesian-style confidence updates from feedback events."""

    def __init__(self, config: FlywheelConfig | None = None) -> None:
        self.config = config or FlywheelConfig()

    def process_feedback(self, store: MemoryStore, *, since: str | None = None) -> dict[str, Any]:
        """Apply unprocessed feedback events; return summary counts."""
        from tapps_brain.store import MemoryStore as _MS

        if not isinstance(store, _MS):
            raise TypeError("store must be MemoryStore")
        raw = store._persistence.flywheel_meta_get(_FEEDBACK_CURSOR_KEY)
        cur_ts, cur_id = _parse_cursor(raw)
        events = store.query_feedback(limit=100_000)
        events.sort(key=lambda e: (e.timestamp, e.id))
        processed = 0
        adjustments = 0
        last_ts: str | None = None
        last_id: str | None = None
        for ev in events:
            if not _event_after_cursor(ev, cur_ts, cur_id):
                continue
            apply_updates = since is None or ev.timestamp >= since
            if apply_updates:
                for ek, d_pos, d_neg in _feedback_deltas(ev):
                    if not ek:
                        continue
                    with store._lock:
                        entry = store._entries.get(ek)
                    if entry is None:
                        continue
                    new_pos = float(entry.positive_feedback_count) + d_pos
                    new_neg = float(entry.negative_feedback_count) + d_neg
                    bayes = beta_mean(new_pos, new_neg)
                    tier = _tier_str(entry.tier)
                    vol = float(self.config.tier_volatility.get(tier, 0.7))
                    k_factor = self.config.base_K * vol
                    delta = k_factor * (bayes - entry.confidence)
                    new_conf = max(self.config.min_confidence, min(1.0, entry.confidence + delta))
                    old_c = entry.confidence
                    store.update_fields(
                        ek,
                        positive_feedback_count=round(new_pos, 6),
                        negative_feedback_count=round(new_neg, 6),
                        confidence=round(new_conf, 6),
                    )
                    store._persistence.append_audit(
                        "flywheel_confidence",
                        ek,
                        extra={
                            "feedback_event_id": ev.id,
                            "old_confidence": old_c,
                            "new_confidence": new_conf,
                            "delta_pos": d_pos,
                            "delta_neg": d_neg,
                        },
                    )
                    adjustments += 1
            processed += 1
            last_ts, last_id = ev.timestamp, ev.id
        if last_ts is not None and last_id is not None:
            store._persistence.flywheel_meta_set(
                _FEEDBACK_CURSOR_KEY,
                json.dumps({"ts": last_ts, "id": last_id}, separators=(",", ":")),
            )
        return {"processed_events": processed, "confidence_adjustments": adjustments}


# ---------------------------------------------------------------------------
# Knowledge gaps
# ---------------------------------------------------------------------------


class KnowledgeGap(BaseModel):
    """Clustered knowledge gap signal."""

    query_pattern: str
    count: float
    first_reported: str
    last_reported: str
    descriptions: list[str] = Field(default_factory=list)
    priority_score: float = 0.0


class GapTracker:
    """Aggregate gap_reported + zero-result recall signals."""

    def __init__(self, jaccard_threshold: float = 0.6) -> None:
        self.jaccard_threshold = jaccard_threshold

    def analyze_gaps(
        self,
        store: MemoryStore,
        *,
        since: str | None = None,
        use_semantic_clustering: bool = False,
    ) -> list[KnowledgeGap]:
        """Return clustered gaps sorted by priority (desc)."""
        instances: list[tuple[str, str, float, list[str]]] = []
        # (query, ts, weight, desc_parts)
        try:
            evs = store.query_feedback(event_type="gap_reported", limit=10_000)
        except Exception:
            evs = []
        for ev in evs:
            if since is not None and ev.timestamp < since:
                continue
            q = ""
            desc: list[str] = []
            if isinstance(ev.details, dict):
                q = str(ev.details.get("query", "")).strip()
                d = ev.details.get("description")
                if isinstance(d, str) and d.strip():
                    desc.append(d.strip())
            if q:
                instances.append((q, ev.timestamp, 1.0, desc))
        for q, ts in store.zero_result_gap_signals():
            if since is not None and ts < since:
                continue
            if q.strip():
                instances.append((q.strip(), ts, 0.5, []))

        if not instances:
            return []

        if use_semantic_clustering:
            clustered = self._cluster_hdbscan(instances)
            if clustered is not None:
                return self._prioritize_gaps(store, clustered)

        clustered = self._cluster_jaccard(instances)
        return self._prioritize_gaps(store, clustered)

    def _cluster_jaccard(
        self,
        instances: list[tuple[str, str, float, list[str]]],
    ) -> list[tuple[list[tuple[str, str, float, list[str]]], str]]:
        """Greedy clustering by pairwise Jaccard on query strings."""
        remaining = list(instances)
        clusters: list[list[tuple[str, str, float, list[str]]]] = []
        while remaining:
            seed = remaining.pop(0)
            group = [seed]
            i = 0
            while i < len(remaining):
                q, _, _, _ = remaining[i]
                if jaccard_similarity(q, seed[0]) >= self.jaccard_threshold:
                    group.append(remaining.pop(i))
                else:
                    i += 1
            clusters.append(group)
        out: list[tuple[list[tuple[str, str, float, list[str]]], str]] = []
        for group in clusters:
            rep = min((g[0] for g in group), key=len)
            out.append((group, rep))
        return out

    def _cluster_hdbscan(
        self,
        instances: list[tuple[str, str, float, list[str]]],
    ) -> list[tuple[list[tuple[str, str, float, list[str]]], str]] | None:
        try:
            import hdbscan  # type: ignore[import-not-found]
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return None
        return _run_hdbscan_clustering(instances, SentenceTransformer, hdbscan)

    def _prioritize_gaps(
        self,
        store: _GapSearchStore,
        clustered: list[tuple[list[tuple[str, str, float, list[str]]], str]],
    ) -> list[KnowledgeGap]:
        now = datetime.now(tz=UTC)
        window_recent = now - timedelta(days=30)
        window_prev = now - timedelta(days=60)
        results: list[KnowledgeGap] = []
        for group, rep in clustered:
            count = sum(g[2] for g in group)
            ts_list = sorted(g[1] for g in group)
            first, last = ts_list[0], ts_list[-1]
            descs: list[str] = []
            seen: set[str] = set()
            for g in group:
                for d in g[3]:
                    if d not in seen:
                        seen.add(d)
                        descs.append(d)
            recent = sum(1 for g in group if _parse_iso(g[1]) >= window_recent)
            prev = sum(1 for g in group if window_prev <= _parse_iso(g[1]) < window_recent)
            if recent > prev:
                trend = 1.5
            elif recent < prev:
                trend = 0.7
            else:
                trend = 1.0
            tier_w = _estimate_tier_weight(store, rep)
            priority = count * tier_w * trend
            results.append(
                KnowledgeGap(
                    query_pattern=rep,
                    count=round(count, 4),
                    first_reported=first,
                    last_reported=last,
                    descriptions=descs,
                    priority_score=round(priority, 6),
                )
            )
        results.sort(key=lambda g: g.priority_score, reverse=True)
        return results

    def top_gaps(self, store: MemoryStore, *, limit: int = 10) -> list[KnowledgeGap]:
        return self.analyze_gaps(store)[:limit]


def _run_hdbscan_clustering(  # pragma: no cover
    instances: list[tuple[str, str, float, list[str]]],
    sentence_transformer_cls: type,
    hdbscan_mod: Any,
) -> list[tuple[list[tuple[str, str, float, list[str]]], str]]:
    """Embedding + HDBSCAN path (optional vector extra; not exercised in default CI)."""
    texts = [t[0] for t in instances]
    model = sentence_transformer_cls("all-MiniLM-L6-v2")
    emb = model.encode(texts, convert_to_numpy=True)
    clusterer = hdbscan_mod.HDBSCAN(min_cluster_size=3, metric="euclidean")
    labels = clusterer.fit_predict(emb)
    by_label: dict[int, list[tuple[str, str, float, list[str]]]] = defaultdict(list)
    for inst, lab in zip(instances, labels, strict=True):
        by_label[int(lab)].append(inst)
    out: list[tuple[list[tuple[str, str, float, list[str]]], str]] = []
    for lab, group in by_label.items():
        if lab == -1:
            for g in group:
                out.append(([g], g[0]))
        else:
            rep = min((g[0] for g in group), key=len)
            out.append((group, rep))
    return out


def _parse_iso(ts: str) -> datetime:
    try:
        raw = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return datetime.now(tz=UTC)


def _estimate_tier_weight(store: _GapSearchStore, query: str) -> float:
    try:
        hits = store.search(query)[:3]
    except Exception:
        hits = []
    if not hits:
        return 1.0
    best = 1.0
    for e in hits:
        t = _tier_str(e.tier)
        best = max(best, _TIER_GAP_WEIGHT.get(t, 1.0))
    return best


def knowledge_gap_summary_for_diagnostics(store: MemoryStore) -> str | None:
    """One-line gap summary for diagnostics recommendations."""
    gaps = GapTracker().top_gaps(store, limit=1)
    if not gaps:
        return None
    try:
        n = len(store.query_feedback(event_type="gap_reported", limit=5000))
    except Exception:
        n = 0
    top = gaps[0]
    return (
        f"{n} knowledge gaps reported. Top gap: {top.query_pattern!r} "
        f"(priority: {top.priority_score}, weighted count: {top.count})"
    )


# ---------------------------------------------------------------------------
# Quality reports
# ---------------------------------------------------------------------------


class ReportData(BaseModel):
    """Inputs for report sections."""

    model_config = {"arbitrary_types_allowed": True}

    store: Any = None
    diagnostics_history: list[dict[str, Any]] = Field(default_factory=list)
    feedback_summary: dict[str, Any] = Field(default_factory=dict)
    knowledge_gaps: list[KnowledgeGap] = Field(default_factory=list)
    eval_results: Any = None
    custom_data: dict[str, Any] = Field(default_factory=dict)
    diagnostics_report: dict[str, Any] = Field(default_factory=dict)
    period_start: str = ""
    period_end: str = ""


class QualityReport(BaseModel):
    """Rendered self-report."""

    period_start: str
    period_end: str
    sections: list[str] = Field(default_factory=list)
    rendered_text: str = ""
    structured_data: dict[str, Any] = Field(default_factory=dict)


class ReportRegistry:
    """Ordered report sections."""

    def __init__(self, sections: list[ReportSection] | None = None) -> None:
        self._sections: list[ReportSection] = list(sections) if sections else []

    def register(self, section: ReportSection) -> None:
        self._sections.append(section)

    def unregister(self, name: str) -> None:
        self._sections = [s for s in self._sections if s.name != name]

    def sections_sorted(self) -> list[ReportSection]:
        return sorted(self._sections, key=lambda s: (s.priority, s.name))


def _default_builtin_sections() -> list[ReportSection]:
    return [
        _HealthSummarySection(),
        _DimensionBreakdownSection(),
        _AnomalyAlertsSection(),
        _FeedbackSummarySection(),
        _KnowledgeGapsSection(),
        _RecommendationsSection(),
    ]


def default_report_registry() -> ReportRegistry:
    return ReportRegistry(_default_builtin_sections())


class _HealthSummarySection:
    name = "health_summary"
    priority = 10

    def should_include(self, data: ReportData) -> bool:
        return bool(data.diagnostics_report)

    def render(self, data: ReportData) -> str:
        dr = data.diagnostics_report
        comp = dr.get("composite_score", 0.0)
        cs = dr.get("circuit_state", "closed")
        return f"## Summary\n- Composite score: {comp:.3f}\n- Circuit: {cs}\n"


class _DimensionBreakdownSection:
    name = "dimension_breakdown"
    priority = 20

    def should_include(self, data: ReportData) -> bool:
        return bool(data.diagnostics_report.get("dimensions"))

    def render(self, data: ReportData) -> str:
        dims = data.diagnostics_report.get("dimensions") or {}
        lines = ["## Dimensions"]
        for name, d in sorted(dims.items()):
            if isinstance(d, dict):
                sc = d.get("score", 0.0)
            else:
                sc = getattr(d, "score", 0.0)
            lines.append(f"- {name}: {float(sc):.3f}")
        return "\n".join(lines) + "\n"


class _AnomalyAlertsSection:
    name = "anomaly_alerts"
    priority = 30

    def should_include(self, data: ReportData) -> bool:
        an = data.diagnostics_report.get("anomalies") or []
        return len(an) > 0

    def render(self, data: ReportData) -> str:
        an = data.diagnostics_report.get("anomalies") or []
        lines = ["## Anomalies"]
        for a in an[:10]:
            lines.append(f"- {a!s}")
        return "\n".join(lines) + "\n"


class _FeedbackSummarySection:
    name = "feedback_summary"
    priority = 40

    def should_include(self, data: ReportData) -> bool:
        return bool(data.feedback_summary)

    def render(self, data: ReportData) -> str:
        fs = data.feedback_summary
        lines = ["## Feedback"]
        for k, v in sorted(fs.items()):
            lines.append(f"- {k}: {v}")
        return "\n".join(lines) + "\n"


class _KnowledgeGapsSection:
    name = "knowledge_gaps"
    priority = 50

    def should_include(self, data: ReportData) -> bool:
        return len(data.knowledge_gaps) > 0

    def render(self, data: ReportData) -> str:
        lines = ["## Knowledge gaps"]
        for g in data.knowledge_gaps[:5]:
            lines.append(f"- {g.query_pattern!r} (priority {g.priority_score}, count {g.count})")
        return "\n".join(lines) + "\n"


class _RecommendationsSection:
    name = "recommendations"
    priority = 100

    def should_include(self, data: ReportData) -> bool:
        recs = data.diagnostics_report.get("recommendations") or []
        return len(recs) > 0

    def render(self, data: ReportData) -> str:
        recs = data.diagnostics_report.get("recommendations") or []
        lines = ["## Actions"]
        for r in recs:
            lines.append(f"- {r}")
        return "\n".join(lines) + "\n"


def _feedback_summary_counts(store: MemoryStore) -> dict[str, int]:
    try:
        events = store.query_feedback(limit=5000)
    except Exception:
        return {}
    counts: dict[str, int] = defaultdict(int)
    for e in events:
        counts[e.event_type] += 1
    return dict(counts)


def generate_report(
    store: MemoryStore,
    *,
    period_days: int = 7,
    extra_sections: list[ReportSection] | None = None,
    custom_data: dict[str, Any] | None = None,
    registry: ReportRegistry | None = None,
    eval_results: Any = None,
    config: FlywheelConfig | None = None,
) -> QualityReport:
    """Build a markdown quality report from diagnostics + flywheel signals."""
    cfg = config or FlywheelConfig()
    now = datetime.now(tz=UTC)
    start = (now - timedelta(days=period_days)).isoformat()
    end = now.isoformat()
    rep = store.diagnostics(record_history=False)
    hist = store.diagnostics_history(limit=30)
    gaps = GapTracker().top_gaps(store, limit=10)
    fb = _feedback_summary_counts(store)

    rd = ReportData(
        store=store,
        diagnostics_history=hist,
        feedback_summary=fb,
        knowledge_gaps=gaps,
        eval_results=eval_results,
        custom_data=dict(custom_data or {}),
        diagnostics_report=rep.model_dump(mode="json"),
        period_start=start,
        period_end=end,
    )

    reg = registry or default_report_registry()
    for s in cfg.custom_report_sections:
        reg.register(s)
    if extra_sections:
        for s in extra_sections:
            reg.register(s)

    parts: list[str] = [
        f"# Quality report\n_Period_: {start[:10]} → {end[:10]} (UTC)\n",
        "## Impact\nOperational view of memory quality, feedback, and gaps.\n",
    ]
    structured: dict[str, Any] = {
        "period_start": start,
        "period_end": end,
        "composite_score": rep.composite_score,
        "gap_count": rep.gap_count,
        "top_gap": gaps[0].model_dump(mode="json") if gaps else None,
    }

    for sec in reg.sections_sorted():
        try:
            if sec.should_include(rd):
                parts.append(sec.render(rd))
        except Exception:
            logger.debug("report.section_failed", section=sec.name, exc_info=True)

    text = "\n".join(parts).strip() + "\n"
    qr = QualityReport(
        period_start=start,
        period_end=end,
        sections=parts,
        rendered_text=text,
        structured_data=structured,
    )

    if cfg.store_self_report_memory:
        import uuid

        body = text[:4096]
        store.save(
            f"self-report-{uuid.uuid4().hex[:16]}",
            body,
            tier="context",
            source="system",
            tags=["self-report"],
        )
    return qr


# ---------------------------------------------------------------------------
# Hive cross-project aggregation (STORY-031.8)
# ---------------------------------------------------------------------------


class HiveFeedbackReport(BaseModel):
    """Aggregated Hive feedback signals across projects."""

    entry_feedback: dict[str, Any] = Field(default_factory=dict)
    cross_project_gaps: list[KnowledgeGap] = Field(default_factory=list)
    issue_hotspots: list[dict[str, Any]] = Field(default_factory=list)
    total_projects_reporting: int = 0


def aggregate_hive_feedback(
    hive_store: Any,
    *,
    since: str | None = None,
) -> HiveFeedbackReport | None:
    """Scan Hive feedback events and aggregate cross-project patterns."""
    if hive_store is None:
        return None
    rows = hive_store.query_feedback_events(limit=50_000)
    chronological = list(reversed(rows))
    projects: set[str] = set()
    neg_projects: dict[tuple[str, str], set[str]] = defaultdict(set)
    rating_hist: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    gap_instances: list[tuple[str, str, float, list[str]]] = []
    issue_projects: dict[str, set[str]] = defaultdict(set)

    for r in chronological:
        ts = str(r.get("timestamp", ""))
        if since is not None and ts < since:
            continue
        sp = str(r.get("source_project", "") or "")
        if sp:
            projects.add(sp)
        ns = str(r.get("namespace", "universal"))
        ek = r.get("entry_key")
        et = str(r.get("event_type", ""))
        details = r.get("details") if isinstance(r.get("details"), dict) else {}
        if et == "recall_rated" and ek:
            key = (ns, str(ek))
            us = r.get("utility_score")
            rating = str(details.get("rating", ""))
            rating_hist[key][rating or "unknown"] += 1
            if us is not None and float(us) <= 0.0:
                if sp:
                    neg_projects[key].add(sp)
        elif et == "gap_reported":
            q = str(details.get("query", "")).strip()
            if q:
                gap_instances.append((q, ts, 1.0, []))
        elif et == "issue_flagged" and ek and sp:
            issue_projects[str(ek)].add(sp)

    entry_feedback: dict[str, Any] = {}
    for (ns, k), hist in rating_hist.items():
        nk = f"{ns}:{k}"
        entry_feedback[nk] = {
            "ratings": dict(hist),
            "negative_project_count": len(neg_projects.get((ns, k), set())),
        }

    clusters = GapTracker()._cluster_jaccard(gap_instances) if gap_instances else []
    cross_gaps = GapTracker()._prioritize_gaps(
        _HiveGapStoreStub(),
        clusters,
    )

    hotspots = [
        {"entry_key": k, "project_count": len(v)} for k, v in issue_projects.items() if len(v) >= 2
    ]

    return HiveFeedbackReport(
        entry_feedback=entry_feedback,
        cross_project_gaps=cross_gaps,
        issue_hotspots=hotspots,
        total_projects_reporting=len(projects),
    )


class _HiveGapStoreStub:
    """Minimal store stand-in for tier weighting (no local search)."""

    def search(self, _query: str) -> list[MemoryEntry]:
        return []


def process_hive_feedback(
    hive_store: Any,
    *,
    threshold: int = 3,
    penalty_factor: float = 0.85,
) -> dict[str, Any]:
    """Lower Hive entry confidence when many projects report negative ratings."""
    if hive_store is None:
        return {"updated": 0, "skipped": True}
    rep = aggregate_hive_feedback(hive_store)
    if rep is None:
        return {"updated": 0, "skipped": True}
    updated = 0
    for key, info in rep.entry_feedback.items():
        if info.get("negative_project_count", 0) < threshold:
            continue
        if ":" not in key:
            continue
        ns, ek = key.split(":", 1)
        old = hive_store.get_confidence(namespace=ns, key=ek)
        if old is None:
            continue
        new = max(0.05, old * penalty_factor)
        if hive_store.patch_confidence(namespace=ns, key=ek, confidence=new):
            updated += 1
    return {"updated": updated, "skipped": False}
