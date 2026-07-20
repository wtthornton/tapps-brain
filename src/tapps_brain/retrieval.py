"""Ranked memory retrieval with composite scoring.

Upgrades memory search from simple keyword matching to scored,
ranked retrieval combining text relevance with memory-specific
signals (confidence, recency, access frequency).

Uses BM25 (BM25+ variant — Okapi with a lower-bound delta, see
``bm25.BM25Scorer``) for text relevance scoring with automatic
index building and invalidation. Epic 65.8: hybrid BM25 + vector
search with RRF when semantic_search.enabled.

EPIC-042.5: Composite weights come from ``profile.ScoringConfig`` (YAML ``scoring:``);
see ``SCORING_WEIGHT_SUM_MIN`` / ``SCORING_WEIGHT_SUM_MAX`` in ``profile.py``.
EPIC-042.4: Hybrid RRF pool sizes and ``k`` come from ``profile.HybridFusionConfig``
(YAML ``hybrid_fusion:``) when the retriever is constructed with ``hybrid_config``
(``inject_memories`` passes the store profile). See ``fusion.py`` for the RRF formula.
Raw relevance is normalized via per-query min-max over surviving candidates.
EPIC-042.6: After hybrid scoring, optional rerank emits structured logs
(``memory_rerank`` / ``reranker_failed_fallback_to_original``) and
``last_rerank_stats`` for callers (e.g. ``inject_memories`` telemetry).
Default ``search()`` excludes: contradicted entries (unless ``include_contradicted``),
consolidated source rows (unless ``include_sources``), temporally invalid /
superseded entries (unless ``include_superseded`` / ``include_historical``),
lifecycle ``stale`` / ``superseded`` / ``archived`` statuses (unless
``include_stale``), and entries below ``min_confidence`` after decay. BM25/FTS
may still index the full corpus for IDF; ranking applies the filters above.
"""

from __future__ import annotations

import contextlib
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, Field

from tapps_brain.bm25 import BM25Scorer, preprocess
from tapps_brain.decay import DecayConfig, calculate_decayed_confidence, is_stale
from tapps_brain.fusion import hybrid_rrf_weights_for_query, reciprocal_rank_fusion_weighted
from tapps_brain.lexical import LexicalRetrievalConfig
from tapps_brain.models import MemoryEntry, MemorySource, MemoryStatus, MemoryTier, tier_str
from tapps_brain.otel_tracer import (
    rm_add_bm25_candidates,
    rm_add_vector_candidates,
    rm_increment_rrf_fusions,
)
from tapps_brain.profile import (
    SCORING_WEIGHT_SUM_MAX,
    SCORING_WEIGHT_SUM_MIN,
    composite_scoring_weight_total,
)
from tapps_brain.relations import RelationEntry, expand_via_relations
from tapps_brain.reranker import RERANKER_TOP_CANDIDATES, Reranker

if TYPE_CHECKING:
    from tapps_brain._protocols import KnowledgeGraphBackend
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

_MAX_RESULTS = 50
_DEFAULT_RESULTS = 10
_MIN_CONFIDENCE_FLOOR = 0.1
_EXCLUDED_LIFECYCLE_STATUSES = frozenset(
    {MemoryStatus.stale, MemoryStatus.superseded, MemoryStatus.archived}
)


class ScoredMemory(BaseModel):
    """A memory entry with retrieval scoring metadata."""

    entry: MemoryEntry
    score: float = Field(ge=0.0, description="Composite retrieval score.")
    effective_confidence: float = Field(ge=0.0, le=1.0, description="Time-decayed confidence.")
    bm25_relevance: float = Field(ge=0.0, description="Normalized text relevance.")
    stale: bool = Field(default=False, description="Whether the memory is stale.")


class ScoredEdge(BaseModel):
    """A KG edge row with composite neighbourhood scoring (STORY-076.2).

    Returned by :meth:`MemoryRetriever.search_neighborhood`.
    The ``score`` is the raw edge composite score **before** multiplication
    by ``scoring_config.graph_weight``.  Callers that blend edges with memory
    results should apply the weight themselves.
    """

    edge_id: str = Field(description="UUID string of the KG edge.")
    predicate: str = Field(description="Predicate label (e.g. 'uses', 'depends_on').")
    neighbor_id: str = Field(description="UUID string of the neighbouring entity.")
    entity_type: str = Field(description="Type of the neighbouring entity.")
    canonical_name: str = Field(description="Canonical name of the neighbouring entity.")
    hop: int = Field(ge=1, description="Distance from focal entity (1 or 2).")
    score: float = Field(ge=0.0, le=1.0, description="Composite edge score (0-1).")
    edge_confidence: float = Field(ge=0.0, le=1.0, description="Edge confidence signal.")
    evidence_count: int = Field(ge=0, default=0, description="Attached evidence rows.")
    blended_score: float = Field(
        ge=0.0,
        description="score * graph_weight — ready for merging into a ranked list.",
    )


# ---------------------------------------------------------------------------
# TAP-733: Structured pre-filters (applied before BM25/vector scoring)
# ---------------------------------------------------------------------------


@dataclass
class MemoryFilter:
    """Hard pre-filters applied to the candidate pool *before* BM25/vector scoring.

    All active filter conditions are AND-combined — each narrows the pool further.
    An empty / all-None ``MemoryFilter`` is a no-op (preserves current behaviour).

    Attributes:
        tier: Restrict to entries whose ``tier`` matches this value (string or
            :class:`~tapps_brain.models.MemoryTier` enum).
        memory_class: Restrict to entries with this ``memory_class`` value
            (``"incident"`` | ``"guidance"`` | ``"decision"`` | ``"convention"``).
        tags: ALL of these tags must appear on every matching entry (AND).
        tags_any: ANY one of these tags must appear (OR).
        memory_group: Restrict to a project-local group.
        min_confidence: Exclude entries whose ``confidence`` is below this floor.
    """

    tier: MemoryTier | str | None = None
    memory_class: str | None = None
    tags: list[str] = field(default_factory=list)
    tags_any: list[str] = field(default_factory=list)
    memory_group: str | None = None
    min_confidence: float | None = None


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

_W_RELEVANCE = 0.40
_W_CONFIDENCE = 0.30
_W_RECENCY = 0.15
_W_FREQUENCY = 0.10
_W_GRAPH = 0.05  # graph centrality (TAP-734); weights sum to 1.0

_FREQUENCY_CAP = 20.0

# Edge scoring weights (STORY-076.2).  These are the *intra-edge* weights
# for the composite edge score; they are independent of the memory scoring
# weights above.  The profile's ``graph_weight`` then scales the entire
# edge score when blending with memory scores.
_EW_CONFIDENCE = 0.35
_EW_RECENCY = 0.20
_EW_USEFULNESS = 0.15
_EW_SOURCE_TRUST = 0.15
_EW_EVIDENCE = 0.10
_EW_TEMPORAL = 0.05

# Source-trust multipliers for edge scoring (mirrors the memory trust map).
_EDGE_SOURCE_TRUST: dict[str, float] = {
    "human": 1.0,
    "system": 0.9,
    "agent": 0.7,
    "inferred": 0.5,
}

_SECONDS_PER_DAY = 86_400.0

# pgvector column dimension — vector(384), migration 001 (ADR-007).
_PGVECTOR_DIM = 384

# Per-source trust multipliers applied to composite score (M2).
# These are post-composite multipliers, not additive weights.
_DEFAULT_SOURCE_TRUST: dict[str, float] = {
    "human": 1.0,
    "system": 0.9,
    "agent": 0.7,
    "inferred": 0.5,
}

# Marker text for consolidated source entries
_CONSOLIDATED_MARKER = "consolidated into"


def score_edge(edge: dict[str, Any]) -> float:
    """Compute a composite score (0-1) for a KG edge row (STORY-076.2).

    Formula::

        score = (
            _EW_CONFIDENCE  * confidence
            + _EW_RECENCY   * recency_signal
            + _EW_USEFULNESS * usefulness_ratio
            + _EW_SOURCE_TRUST * source_trust
            + _EW_EVIDENCE  * log(1 + evidence_count) / log(1 + 10)
            + _EW_TEMPORAL  * temporal_validity
        )

    All components are clamped to [0, 1] before weighting.

    Args:
        edge: Dict row returned by :meth:`get_neighbors_multi`.

    Returns:
        Composite score in [0, 1].
    """
    # Confidence component (already 0-1 from DB)
    confidence = float(edge.get("edge_confidence") or 0.0)
    confidence = max(0.0, min(1.0, confidence))

    # Recency: exp(-age/30) — a 30-day 1/e lifetime (half-life ≈ 21 days);
    # 1.0 = just reinforced, → 0.0 as the edge goes stale.
    last_reinforced = edge.get("last_reinforced")
    if last_reinforced is not None:
        try:
            if hasattr(last_reinforced, "timestamp"):
                # Normalize naive datetimes that some psycopg drivers return.
                if last_reinforced.tzinfo is None:
                    last_reinforced = last_reinforced.replace(tzinfo=UTC)
                secs = (datetime.now(tz=UTC) - last_reinforced).total_seconds()
            else:
                parsed = datetime.fromisoformat(str(last_reinforced))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                secs = (datetime.now(tz=UTC) - parsed).total_seconds()
            age_days = secs / _SECONDS_PER_DAY
            recency = max(0.0, min(1.0, math.exp(-age_days / 30.0)))
        except Exception:
            recency = 0.5
    else:
        recency = 0.5  # Never reinforced; neutral signal

    # Usefulness ratio: useful_access_count / max(access_count, 1)
    useful = float(edge.get("useful_access_count") or 0)
    total = float(edge.get("access_count") or 0)
    usefulness = useful / max(total, 1.0)
    usefulness = max(0.0, min(1.0, usefulness))

    # Source trust (from edge source field or default)
    source = str(edge.get("source") or "agent").lower()
    source_trust = _EDGE_SOURCE_TRUST.get(source, 0.7)
    source_trust = max(0.0, min(1.0, source_trust))

    # Evidence count: log-scaled, capped at 10 for normalization
    ev_count = int(edge.get("evidence_count") or 0)
    evidence_sig = math.log(1 + ev_count) / math.log(1 + 10)
    evidence_sig = max(0.0, min(1.0, evidence_sig))

    # Temporal validity: 1.0 unless edge has been superseded / contradicted
    status = str(edge.get("edge_status") or "active")
    contradicted = bool(edge.get("contradicted") or False)
    temporal = 1.0 if status == "active" and not contradicted else 0.0

    raw = (
        _EW_CONFIDENCE * confidence
        + _EW_RECENCY * recency
        + _EW_USEFULNESS * usefulness
        + _EW_SOURCE_TRUST * source_trust
        + _EW_EVIDENCE * evidence_sig
        + _EW_TEMPORAL * temporal
    )
    return max(0.0, min(1.0, raw))


def _is_consolidated_source(entry: MemoryEntry) -> bool:
    """Check if an entry is a source of a consolidated entry.

    Source entries are marked with contradicted=True and a
    contradiction_reason containing "consolidated into".

    Args:
        entry: The memory entry to check.

    Returns:
        True if this entry was consolidated into another entry.
    """
    if not entry.contradicted:
        return False
    reason = entry.contradiction_reason or ""
    return _CONSOLIDATED_MARKER in reason.lower()


def _hybrid_adaptive_fusion_enabled(hybrid_config: object | None) -> bool:
    """Whether to apply query-aware BM25/vector RRF weights (EPIC-040 / #40).

    ``hybrid_config.adaptive_fusion`` may be set to ``False`` for legacy 1:1 RRF.
    Missing attribute defaults to enabled. Non-boolean values (e.g. test mocks)
    are treated as enabled unless explicitly ``False``.
    """
    if hybrid_config is None:
        return True
    raw = getattr(hybrid_config, "adaptive_fusion", True)
    return raw is not False


def _build_temporal_kwargs(
    *,
    since: str | None,
    until: str | None,
    time_field: str,
    as_of: str | None,
    include_superseded: bool,
) -> dict[str, Any]:
    """Assemble the temporal kwargs forwarded to candidate retrieval."""
    kw: dict[str, Any] = {}
    if since is not None:
        kw["since"] = since
    if until is not None:
        kw["until"] = until
    if time_field != "created_at":
        kw["time_field"] = time_field
    if as_of is not None:
        kw["as_of"] = as_of
    if include_superseded:
        kw["include_superseded"] = True
    return kw


def _entry_matches_temporal_window(
    entry: MemoryEntry,
    *,
    as_of: str | None,
    since: str | None,
    until: str | None,
    time_field: str,
    include_superseded: bool,
) -> bool:
    """Return True when *entry* belongs in a BM25/LIKE fallback corpus.

    Mirrors the FTS live-row contract: exclude temporally invalid rows unless
    ``include_superseded``, honour ``as_of``, and apply an optional
    ``[since, until)`` window on *time_field*.
    """
    if not include_superseded and not entry.is_temporally_valid(as_of):
        return False
    if since is None and until is None:
        return True

    field_name = time_field if time_field in {"updated_at", "last_accessed"} else "created_at"
    raw_ts = getattr(entry, field_name, None)
    ts: datetime | None = None
    if isinstance(raw_ts, str) and raw_ts:
        try:
            ts = datetime.fromisoformat(raw_ts)
        except (ValueError, TypeError):
            ts = None
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    if ts is None:
        return True

    def _bound(value: str | None) -> datetime | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed

    lo = _bound(since)
    hi = _bound(until)
    return not ((lo is not None and ts < lo) or (hi is not None and ts >= hi))


# ---------------------------------------------------------------------------
# MemoryRetriever
# ---------------------------------------------------------------------------


class MemoryRetriever:
    """Ranked retrieval engine for memory entries."""

    def __init__(
        self,
        config: DecayConfig | None = None,
        *,
        scoring_config: object | None = None,  # ScoringConfig from profile (EPIC-010)
        semantic_enabled: bool = False,
        hybrid_config: object | None = None,
        reranker: Reranker | None = None,
        reranker_enabled: bool = False,
        reranker_provider: str | None = None,
        retrieval_policy: object | None = None,
        relations_enabled: bool = False,
        expand_queries: bool = True,
        lexical_config: LexicalRetrievalConfig | None = None,
    ) -> None:
        self._config = config or DecayConfig()
        _lex = lexical_config or LexicalRetrievalConfig()
        self._bm25 = BM25Scorer(
            apply_stem=_lex.apply_stem,
            ascii_fold=_lex.ascii_fold,
            camel_case_tokenization=_lex.camel_case_tokenization,
        )
        self._bm25_entries: list[MemoryEntry] = []
        # Per-group index cache: group_key -> (scorer, entries, fingerprint).
        # Keying by group prevents full re-tokenization thrash when grouped and
        # ungrouped queries alternate (they index different corpora).
        self._bm25_cache: dict[str | None, tuple[BM25Scorer, list[MemoryEntry], int]] = {}
        # Fallback corpus-embedding cache: (group_key, fingerprint) -> embeddings.
        self._emb_cache_key: tuple[str | None, int] | None = None
        self._emb_cache: list[list[float]] = []
        self._semantic_enabled = semantic_enabled
        self._hybrid_config = hybrid_config
        self._reranker = reranker
        self._reranker_enabled = reranker_enabled
        self._reranker_provider: str = (reranker_provider or "noop").strip().lower() or "noop"
        self._retrieval_policy = retrieval_policy
        self._relations_enabled = relations_enabled
        self._expand_queries = expand_queries

        # EPIC-010: use configurable scoring weights if provided
        self._scoring_config = scoring_config
        if scoring_config is not None:
            self._w_relevance = getattr(scoring_config, "relevance", _W_RELEVANCE)
            self._w_confidence = getattr(scoring_config, "confidence", _W_CONFIDENCE)
            self._w_recency = getattr(scoring_config, "recency", _W_RECENCY)
            self._w_frequency = getattr(scoring_config, "frequency", _W_FREQUENCY)
            self._frequency_cap = max(
                float(getattr(scoring_config, "frequency_cap", _FREQUENCY_CAP)), 1.0
            )
            self._w_graph = float(getattr(scoring_config, "graph_centrality", 0.0))
            self._w_provenance = float(getattr(scoring_config, "provenance_trust", 0.0))
            raw_trust = getattr(scoring_config, "source_trust", None)
            self._source_trust: dict[str, float] = (
                dict(raw_trust) if isinstance(raw_trust, dict) else dict(_DEFAULT_SOURCE_TRUST)
            )
            # Warn if duck-typed scoring_config weights fall outside the same band as
            # ``ScoringConfig`` (YAML-loaded profiles are already validated there).
            weight_sum = composite_scoring_weight_total(
                self._w_relevance,
                self._w_confidence,
                self._w_recency,
                self._w_frequency,
                graph_centrality=self._w_graph,
                provenance_trust=self._w_provenance,
            )
            if not (SCORING_WEIGHT_SUM_MIN <= weight_sum <= SCORING_WEIGHT_SUM_MAX):
                logger.warning(
                    "scoring_weights_do_not_sum_to_one",
                    weight_sum=round(weight_sum, 4),
                    relevance=self._w_relevance,
                    confidence=self._w_confidence,
                    recency=self._w_recency,
                    frequency=self._w_frequency,
                    graph_centrality=self._w_graph,
                    provenance_trust=self._w_provenance,
                )
        else:
            self._w_relevance = _W_RELEVANCE
            self._w_confidence = _W_CONFIDENCE
            self._w_recency = _W_RECENCY
            self._w_frequency = _W_FREQUENCY
            self._w_graph = _W_GRAPH
            self._w_provenance = 0.0
            self._frequency_cap = _FREQUENCY_CAP
            self._source_trust = dict(_DEFAULT_SOURCE_TRUST)

        # EPIC-042.6: set by ``search()`` when rerank runs; read by injection/recall telemetry.
        self.last_rerank_stats: dict[str, Any] | None = None
        # Set by ``search()``: counts of candidates dropped by lifecycle status /
        # decayed confidence, read by injection recall diagnostics.
        self.last_filter_stats: dict[str, int] | None = None

    # ------------------------------------------------------------------
    # Private helpers for search()
    # ------------------------------------------------------------------

    def _filter_candidates_to_pending(
        self,
        candidates: list[tuple[MemoryEntry, float]],
        *,
        include_sources: bool,
        include_contradicted: bool,
        include_superseded: bool,
        include_stale: bool,
        as_of: str | None,
        min_confidence: float,
        now: datetime,
    ) -> list[tuple[MemoryEntry, float, float, bool, bool]]:
        """Apply entry-level filters and compute effective confidence.

        Returns a list of ``(entry, relevance_raw, eff_conf, stale_flag,
        temporally_valid)`` tuples for candidates that pass all filters.
        Extracted from ``search()`` to reduce its cyclomatic complexity.

        Side effect: sets ``self.last_filter_stats`` with ``dropped_stale``
        (lifecycle-status exclusions) and ``dropped_low_confidence`` (decayed
        confidence below the floor) so recall diagnostics can report them.
        """
        dropped_stale = 0
        dropped_low_confidence = 0
        pending: list[tuple[MemoryEntry, float, float, bool, bool]] = []
        for entry, relevance_raw in candidates:
            # Filter source entries of consolidated memories (Epic 58.5)
            if not include_sources and _is_consolidated_source(entry):
                continue

            # TAP-732: exclude lifecycle stale/superseded/archived by default
            if not include_stale:
                entry_status = getattr(entry, "status", MemoryStatus.active)
                if entry_status in _EXCLUDED_LIFECYCLE_STATUSES:
                    dropped_stale += 1
                    continue

            # Filter contradicted entries (sources already handled above)
            is_included_source = include_sources and _is_consolidated_source(entry)
            if entry.contradicted and not include_contradicted and not is_included_source:
                continue

            # Temporal filtering (EPIC-004)
            temporally_valid = entry.is_temporally_valid(as_of)
            if not temporally_valid and not include_superseded:
                continue

            # Calculate effective confidence
            eff_conf = calculate_decayed_confidence(entry, self._config, now=now)
            if eff_conf < min_confidence:
                dropped_low_confidence += 1
                continue

            stale_flag = is_stale(entry, self._config, now=now)
            # Mark temporally invalid entries as stale (EPIC-004)
            if not temporally_valid:
                stale_flag = True

            pending.append((entry, relevance_raw, eff_conf, stale_flag, temporally_valid))
        self.last_filter_stats = {
            "dropped_stale": dropped_stale,
            "dropped_low_confidence": dropped_low_confidence,
        }
        return pending

    def _build_scored_memory_item(
        self,
        entry: MemoryEntry,
        relevance_norm: float,
        eff_conf: float,
        stale_flag: bool,
        temporally_valid: bool,
        query: str,
        entity_index: dict[str, set[str]],
        entity_total: int,
        now: datetime,
    ) -> ScoredMemory:
        """Compute the composite score for one entry and return a ScoredMemory.

        Handles graph centrality, provenance trust, per-source multiplier,
        superseded penalty, and exact-key bonus.
        Extracted from ``search()`` to reduce its cyclomatic complexity.
        """
        recency = self._recency_score(entry, now)
        frequency = self._frequency_score(entry)

        # Graph centrality: degree centrality via entity co-occurrence (TAP-734).
        graph_centrality = (
            self._compute_graph_centrality(entry, entity_index, entity_total)
            if self._w_graph > 0.0
            else 0.0
        )

        # Provenance trust: source_trust * channel_trust (channel_trust=1.0 for now)
        source_key = (
            entry.source.value if isinstance(entry.source, MemorySource) else str(entry.source)
        )
        provenance_trust = self._source_trust.get(source_key, 1.0)

        composite = (
            self._w_relevance * relevance_norm
            + self._w_confidence * eff_conf
            + self._w_recency * recency
            + self._w_frequency * frequency
            + self._w_graph * graph_centrality
            + self._w_provenance * provenance_trust
        )

        # M2: Apply per-source trust multiplier — but only when the profile does
        # not already blend provenance as a weighted signal, otherwise the same
        # trust value would be counted twice (added and then multiplied).
        if self._w_provenance == 0.0:
            composite *= self._source_trust.get(source_key, 1.0)

        # Penalty for superseded entries included via include_superseded
        if not temporally_valid:
            composite *= 0.5

        # Bonus for exact key match. Capped at 1.0, but never *reduces* a score
        # that legitimately exceeds 1.0 (profile weight sums may reach 1.05).
        if entry.key == query.lower().replace(" ", "-"):
            composite = max(composite, min(composite + 0.1, 1.0))

        return ScoredMemory(
            entry=entry,
            score=round(composite, 4),
            effective_confidence=round(eff_conf, 4),
            bm25_relevance=round(relevance_norm, 4),
            stale=stale_flag,
        )

    def search(
        self,
        query: str,
        store: MemoryStore,
        *,
        limit: int = _DEFAULT_RESULTS,
        include_contradicted: bool = False,
        include_sources: bool = False,
        min_confidence: float = _MIN_CONFIDENCE_FLOOR,
        as_of: str | None = None,
        include_superseded: bool = False,
        include_historical: bool = False,
        include_stale: bool = False,
        memory_group: str | None = None,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
        memory_filter: MemoryFilter | None = None,
    ) -> list[ScoredMemory]:
        """Search memories with ranked scoring.

        Uses the store's FTS-backed search (Postgres tsvector) for candidate
        retrieval, then applies composite scoring with confidence, recency,
        and frequency signals.

        Args:
            query: Search query string.
            store: Memory store to search.
            memory_group: When set, restrict to this project-local group (GitHub #49).
            limit: Max results (default 10, max 50).
            include_contradicted: Include contradicted memories.
            include_sources: Include source entries of consolidated memories
                (Epic 58, Story 58.5). When False (default), entries that were
                consolidated into other entries are filtered out. When True,
                source entries are included alongside consolidated entries.
            min_confidence: Minimum confidence filter.
            as_of: ISO-8601 timestamp for point-in-time queries. When set,
                only entries valid at that time are returned.
            include_superseded: When True, include temporally invalid entries
                (marked with ``stale=True`` and a 0.5x composite-score penalty).
            include_historical: Alias for ``include_superseded`` (GitHub #29, task 040.3).
                When True, include expired/superseded entries in results.
            include_stale: When True, include entries whose lifecycle ``status``
                is ``stale``, ``superseded``, or ``archived`` (TAP-732). Default
                excludes them so inject/recall match ``brain_recall``.
            since: ISO-8601 UTC lower bound (inclusive) on *time_field*,
                forwarded to the store's candidate search (Issue #70).
            until: ISO-8601 UTC upper bound (exclusive) on *time_field*,
                forwarded to the store's candidate search (Issue #70).
            time_field: Column the ``since``/``until`` window filters on —
                ``created_at`` (default), ``updated_at``, or ``last_accessed``.
            memory_filter: Optional structured filter (TAP-733). When ``None`` or all
                fields are unset, no filtering is done (preserves existing behaviour).
                Filters are applied as hard AND conditions over the retrieved candidate
                pool — after BM25/vector candidate scoring but before composite ranking.
                Note: in hybrid mode the pool is already truncated to the fusion top-k,
                so a narrow filter can return fewer results than exist in the store.

        Returns:
            Scored memories sorted by composite score (descending).
        """
        self.last_rerank_stats = None
        self.last_filter_stats = None
        if not query or not query.strip():
            return []
        # include_historical is an alias for include_superseded (GitHub #29)
        include_superseded = include_superseded or include_historical

        limit = max(1, min(limit, _MAX_RESULTS))
        now = datetime.now(tz=UTC)

        # Resolve effective memory_group: memory_filter.memory_group takes precedence when set
        effective_group = memory_group
        if memory_filter is not None and memory_filter.memory_group is not None:
            effective_group = memory_filter.memory_group

        # Epic 65.13: expand query via relations when enabled
        effective_query = query
        if self._relations_enabled and self._expand_queries:
            effective_query = self._expand_query_via_relations(query, store)

        # Epic 65.8: hybrid path when semantic enabled.
        # as_of / include_superseded are forwarded so the FTS path honours
        # point-in-time and historical queries the same way the ranking
        # filters below do.
        _temporal_kw = _build_temporal_kwargs(
            since=since,
            until=until,
            time_field=time_field,
            as_of=as_of,
            include_superseded=include_superseded,
        )

        if self._semantic_enabled:
            candidates = self._get_hybrid_candidates(
                effective_query, store, memory_group=effective_group, **_temporal_kw
            )
        else:
            candidates = self._get_candidates(
                effective_query, store, memory_group=effective_group, **_temporal_kw
            )

        # Enforce the since/until window on every channel. The FTS path applies
        # it in SQL, but the vector channel and the full-scan fallback do not —
        # re-checking here keeps the documented contract on all paths.
        if since is not None or until is not None:
            candidates = self._filter_time_window(
                candidates, store, since=since, until=until, time_field=time_field
            )

        # TAP-733: Apply structured filters to the candidate pool using cheap
        # equality checks on structured fields (tier, memory_class, tags,
        # min_confidence) before composite ranking.
        if memory_filter is not None:
            filtered_entries = self._apply_filters([e for e, _ in candidates], memory_filter)
            filtered_keys = {e.key for e in filtered_entries}
            candidates = [(e, s) for e, s in candidates if e.key in filtered_keys]

        # Phase 1: filter candidates and compute per-entry effective confidence.
        pending = self._filter_candidates_to_pending(
            candidates,
            include_sources=include_sources,
            include_contradicted=include_contradicted,
            include_superseded=include_superseded,
            include_stale=include_stale,
            as_of=as_of,
            min_confidence=min_confidence,
            now=now,
        )

        # Phase 2: min-max normalize raw relevance across surviving candidates.
        rmin: float | None = None
        rmax: float | None = None
        if pending:
            rels = [p[1] for p in pending]
            rmin = min(rels)
            rmax = max(rels)

        # Graph centrality: read entity index from store (TAP-734).
        # Snapshot outside the loop — O(1) attribute access, not per-entry.
        entity_index: dict[str, set[str]] = getattr(store, "_entity_index", {})
        entity_total: int = len(getattr(store, "_entries", {}))

        # Phase 3: build scored memories.
        scored: list[ScoredMemory] = [
            self._build_scored_memory_item(
                entry,
                self._normalize_relevance(relevance_raw, rmin=rmin, rmax=rmax),
                eff_conf,
                stale_flag,
                temporally_valid,
                query,
                entity_index,
                entity_total,
                now,
            )
            for entry, relevance_raw, eff_conf, stale_flag, temporally_valid in pending
        ]

        # Epic 65.14: Apply retrieval policy tag filtering
        if self._retrieval_policy is not None:
            blocked_tags = set(getattr(self._retrieval_policy, "block_sensitive_tags", []))
            if blocked_tags:
                scored = [s for s in scored if not blocked_tags.intersection(s.entry.tags)]

        # Sort by score descending
        scored.sort(key=lambda s: s.score, reverse=True)

        # Epic 65.9: optional reranking of top-20 -> top_k
        if self._reranker_enabled and self._reranker is not None and scored:
            scored = self._apply_reranker(query, scored, limit)

        return scored[:limit]

    def _apply_reranker(
        self,
        query: str,
        scored: list[ScoredMemory],
        limit: int,
    ) -> list[ScoredMemory]:
        """Apply reranker to top candidates; fallback to original order on failure."""
        if self._reranker is None:  # pragma: no cover — caller guards but assert is unsafe with -O
            return scored
        top_candidates = scored[:RERANKER_TOP_CANDIDATES]
        candidates = [(sm.entry.key, sm.entry.value) for sm in top_candidates]
        effective_top_k = min(limit, len(candidates))
        candidates_in = len(candidates)
        t0 = time.perf_counter()

        try:
            reranked = self._reranker.rerank(query, candidates, top_k=effective_top_k)
        except Exception as e:
            latency_ms = round((time.perf_counter() - t0) * 1000, 3)
            self.last_rerank_stats = {
                "applied": False,
                "provider": self._reranker_provider,
                "candidates_in": candidates_in,
                "top_k": effective_top_k,
                "latency_ms": latency_ms,
                "results_out": None,
                "error": type(e).__name__,
            }
            logger.warning(
                "reranker_failed_fallback_to_original",
                reason=str(e),
                provider=self._reranker_provider,
                candidates_in=candidates_in,
                top_k=effective_top_k,
                latency_ms=latency_ms,
            )
            return scored

        latency_ms = round((time.perf_counter() - t0) * 1000, 3)
        log_event = logger.info if self._reranker_provider != "noop" else logger.debug
        log_event(
            "memory_rerank",
            provider=self._reranker_provider,
            candidates_in=candidates_in,
            top_k=effective_top_k,
            latency_ms=latency_ms,
            results_out=len(reranked),
        )

        if not reranked:
            self.last_rerank_stats = {
                "applied": True,
                "provider": self._reranker_provider,
                "candidates_in": candidates_in,
                "top_k": effective_top_k,
                "latency_ms": latency_ms,
                "results_out": 0,
                "error": None,
            }
            return scored

        key_to_scored = {sm.entry.key: sm for sm in scored}
        # The noop provider is a pure passthrough: it preserves order but its
        # "scores" are just positions (1.0, 1-1/n, …). Overwriting the composite
        # score with those would make downstream min-score gates vacuous and
        # inflate top_score telemetry, so keep the original scores for noop.
        keep_original_scores = self._reranker_provider == "noop"
        result: list[ScoredMemory] = []
        for key, rerank_score in reranked:
            sm = key_to_scored.get(key)
            if sm is not None:
                if keep_original_scores:
                    result.append(sm)
                else:
                    # Use reranker score as primary relevance; preserve other fields
                    result.append(
                        ScoredMemory(
                            entry=sm.entry,
                            score=round(rerank_score, 4),
                            effective_confidence=sm.effective_confidence,
                            bm25_relevance=sm.bm25_relevance,
                            stale=sm.stale,
                        )
                    )
        # Append any from original not in reranker result (e.g. API dropped some)
        seen = {sm.entry.key for sm in result}
        for sm in scored:
            if sm.entry.key not in seen:
                result.append(sm)
                if len(result) >= limit:
                    break
        out = result[:limit]
        self.last_rerank_stats = {
            "applied": True,
            "provider": self._reranker_provider,
            "candidates_in": candidates_in,
            "top_k": effective_top_k,
            "latency_ms": latency_ms,
            "results_out": len(reranked),
            "error": None,
        }
        return out

    # -----------------------------------------------------------------------
    # Relation expansion (Epic 65.13)
    # -----------------------------------------------------------------------

    def _expand_query_via_relations(
        self,
        query: str,
        store: MemoryStore,
    ) -> str:
        """Expand a query using entity/relationship graph traversal.

        If the query matches a relationship pattern (e.g. "who handles API"),
        load relations from the persistence layer and expand with connected
        entities. Falls back to the original query on any error.
        """
        # Load relations from persistence if available
        try:
            persistence = getattr(store, "_persistence", None)
            if persistence is None:
                return query
            raw_relations = persistence.list_relations()
            if not raw_relations:
                return query

            relations = [
                RelationEntry(
                    subject=r["subject"],
                    predicate=r["predicate"],
                    object_entity=r["object_entity"],
                    source_entry_keys=r.get("source_entry_keys", []),
                    confidence=r.get("confidence", 0.8),
                )
                for r in raw_relations
            ]

            expanded_terms = expand_via_relations(query, relations)
            if expanded_terms:
                expanded_query = f"{query} {' '.join(expanded_terms)}"
                logger.debug(
                    "query_expanded_via_relations",
                    original=query,
                    expanded=expanded_query,
                    terms_added=len(expanded_terms),
                )
                return expanded_query
        except Exception:
            logger.warning("relation_expansion_failed", query=query, exc_info=True)

        return query

    # -----------------------------------------------------------------------
    # Temporal window enforcement (Issue #70)
    # -----------------------------------------------------------------------

    @staticmethod
    def _filter_time_window(
        candidates: list[tuple[MemoryEntry, float]],
        store: MemoryStore,
        *,
        since: str | None,
        until: str | None,
        time_field: str,
    ) -> list[tuple[MemoryEntry, float]]:
        """Drop candidates whose *time_field* falls outside ``[since, until)``.

        Accepts the same values as ``store.search`` — ISO-8601 strings or
        relative shorthands (``7d`` / ``2w`` / ``1m``) when the store exposes
        ``_parse_relative_time``. Entries with a missing or unparseable
        timestamp are kept (defensive — never silently lose data on a
        malformed row).
        """

        def _parse(value: str | None) -> datetime | None:
            if value is None:
                return None
            expand = getattr(store, "_parse_relative_time", None)
            raw = expand(value) if callable(expand) else value
            try:
                parsed = datetime.fromisoformat(str(raw))
            except (ValueError, TypeError):
                return None
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed

        lo = _parse(since)
        hi = _parse(until)
        if lo is None and hi is None:
            return candidates

        field_name = time_field if time_field in {"updated_at", "last_accessed"} else "created_at"
        kept: list[tuple[MemoryEntry, float]] = []
        for entry, score in candidates:
            raw_ts = getattr(entry, field_name, None)
            ts: datetime | None = None
            if isinstance(raw_ts, str) and raw_ts:
                try:
                    ts = datetime.fromisoformat(raw_ts)
                except (ValueError, TypeError):
                    ts = None
                if ts is not None and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
            if ts is None:
                kept.append((entry, score))
                continue
            if lo is not None and ts < lo:
                continue
            if hi is not None and ts >= hi:
                continue
            kept.append((entry, score))
        return kept

    # -----------------------------------------------------------------------
    # TAP-733: Structured pre-filter
    # -----------------------------------------------------------------------

    @staticmethod
    def _apply_filters(entries: list[MemoryEntry], f: MemoryFilter) -> list[MemoryEntry]:
        """Apply hard pre-filters to narrow the candidate pool (TAP-733).

        Each active filter condition is applied as a strict AND — multiple
        conditions narrow the pool further.  An all-None / empty filter is a
        no-op and returns the original list unchanged.

        Args:
            entries: Candidate memory entries to filter.
            f: Pre-filter specification.  ``None`` fields / empty lists are ignored.

        Returns:
            Filtered list (may be shorter than *entries*; never longer).
        """
        result = entries
        if f.tier is not None:
            target = tier_str(f.tier)
            result = [e for e in result if tier_str(e.tier) == target]
        if f.memory_class is not None:
            result = [e for e in result if getattr(e, "memory_class", None) == f.memory_class]
        if f.tags:
            result = [e for e in result if all(t in e.tags for t in f.tags)]
        if f.tags_any:
            result = [e for e in result if any(t in e.tags for t in f.tags_any)]
        if f.memory_group is not None:
            result = [e for e in result if e.memory_group == f.memory_group]
        if f.min_confidence is not None:
            result = [e for e in result if e.confidence >= f.min_confidence]
        return result

    # -----------------------------------------------------------------------
    # BM25 index management
    # -----------------------------------------------------------------------

    @staticmethod
    def _entry_to_document(entry: MemoryEntry) -> str:
        """Convert a memory entry to a BM25-indexable document string."""
        return f"{entry.key} {entry.value} {' '.join(entry.tags)}"

    @staticmethod
    def _corpus_fingerprint(entries: list[MemoryEntry]) -> int:
        """Compute a fingerprint that changes when any entry is added, removed, or updated."""
        return hash(tuple((e.key, e.updated_at or "") for e in entries))

    def _ensure_bm25_index(
        self, entries: list[MemoryEntry], *, group_key: str | None = None
    ) -> None:
        """Build or rebuild the BM25 index for *group_key* when its corpus changes.

        Indexes are cached per ``group_key`` (``None`` = full corpus) so that
        alternating grouped and ungrouped queries do not thrash a single
        shared index with full re-tokenization on every call.
        """
        fingerprint = self._corpus_fingerprint(entries)
        cached = self._bm25_cache.get(group_key)
        if cached is not None:
            scorer, cached_entries, cached_fp = cached
            if len(cached_entries) == len(entries) and cached_fp == fingerprint:
                self._bm25 = scorer
                self._bm25_entries = cached_entries
                return
            scorer_to_use = scorer
        elif not self._bm25_cache:
            # First corpus indexed by this retriever: reuse the constructor-built
            # scorer so callers/tests holding a reference to ``_bm25`` see it.
            scorer_to_use = self._bm25
        else:
            scorer_to_use = BM25Scorer(
                apply_stem=self._bm25.apply_stem,
                ascii_fold=self._bm25.ascii_fold,
                camel_case_tokenization=self._bm25.camel_case_tokenization,
            )
        documents = [self._entry_to_document(e) for e in entries]
        scorer_to_use.build_index(documents)
        self._bm25 = scorer_to_use
        self._bm25_entries = list(entries)
        self._bm25_cache[group_key] = (scorer_to_use, self._bm25_entries, fingerprint)
        logger.debug("bm25_index_rebuilt", corpus_size=len(entries), group_key=group_key)

    # -----------------------------------------------------------------------
    # Candidate retrieval
    # -----------------------------------------------------------------------

    def _get_candidates(
        self,
        query: str,
        store: MemoryStore,
        *,
        memory_group: str | None = None,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
        as_of: str | None = None,
        include_superseded: bool = False,
    ) -> list[tuple[MemoryEntry, float]]:
        """Retrieve candidate entries and compute BM25 relevance scores.

        Tries the store's FTS-backed search (Postgres tsvector, ADR-007)
        first for candidate filtering, then scores them using BM25. Falls
        back to full in-memory BM25 scan if FTS returns no results, and to
        word overlap if BM25 scoring fails entirely.

        ``as_of`` / ``include_superseded`` are forwarded to the store so the
        FTS channel honours point-in-time and historical queries — otherwise
        entries valid at *as_of* but expired now would be filtered at the DB
        before ranking ever sees them.
        """
        # Try FTS via store.search() for candidate filtering
        try:
            fts_results = store.search(
                query,
                memory_group=memory_group,
                since=since,
                until=until,
                time_field=time_field,
                as_of=as_of,
                include_historical=include_superseded,
            )
            if fts_results:
                results = self._bm25_score_entries(query, fts_results, store)
                rm_add_bm25_candidates(len(results))
                return results
        except Exception:
            logger.warning("fts_search_failed", query=query, exc_info=True)

        # Fallback: full corpus BM25 scan (honour the same live-row / temporal
        # contract as the FTS path — do not pull superseded/expired rows).
        results = self._bm25_full_scan(
            query,
            store,
            memory_group=memory_group,
            since=since,
            until=until,
            time_field=time_field,
            as_of=as_of,
            include_superseded=include_superseded,
        )
        rm_add_bm25_candidates(len(results))
        return results

    def _get_hybrid_candidates(  # noqa: PLR0915
        self,
        query: str,
        store: MemoryStore,
        *,
        memory_group: str | None = None,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
        as_of: str | None = None,
        include_superseded: bool = False,
    ) -> list[tuple[MemoryEntry, float]]:
        """Epic 65.8: Run BM25 + vector search in parallel, merge with RRF.

        EPIC-040: By default uses query-aware weights on BM25 vs vector RRF terms
        (``hybrid_rrf_weights_for_query``). Set ``hybrid_config.adaptive_fusion``
        to ``False`` for equal 1:1 weighting (legacy behavior).

        Pool sizes and ``rrf_k`` default to 20/20/60; override via
        ``profile.hybrid_fusion`` (``top_k_lexical`` / ``top_k_dense`` / ``rrf_k``)
        on the object passed as ``hybrid_config``.
        """
        top_k_lexical = 20
        top_k_dense = 20
        rrf_k = 60
        if self._hybrid_config is not None:
            top_k_lexical = getattr(self._hybrid_config, "top_k_lexical", 20)
            top_k_dense = getattr(self._hybrid_config, "top_k_dense", 20)
            rrf_k = getattr(self._hybrid_config, "rrf_k", 60)

        adaptive_fusion = _hybrid_adaptive_fusion_enabled(self._hybrid_config)
        bm25_w, vector_w = (1.0, 1.0)
        if adaptive_fusion:
            bm25_w, vector_w = hybrid_rrf_weights_for_query(query)

        bm25_keys: list[str] = []
        bm25_candidates: list[tuple[MemoryEntry, float]] = []
        vector_keys: list[str] = []

        def run_bm25() -> None:
            nonlocal bm25_keys, bm25_candidates
            bm25_candidates = self._get_candidates(
                query,
                store,
                memory_group=memory_group,
                since=since,
                until=until,
                time_field=time_field,
                as_of=as_of,
                include_superseded=include_superseded,
            )
            # Take top top_k_lexical by score
            sorted_cands = sorted(
                bm25_candidates,
                key=lambda x: x[1],
                reverse=True,
            )[:top_k_lexical]
            bm25_keys = [e.key for e, _ in sorted_cands]

        def run_vector() -> None:
            nonlocal vector_keys
            vector_results = self._vector_search(
                query,
                store,
                limit=top_k_dense,
                memory_group=memory_group,
                include_expired=include_superseded,
                as_of=as_of,
            )
            vector_keys = [k for k, _ in vector_results]

        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(run_bm25)
            f2 = ex.submit(run_vector)
            for f in as_completed([f1, f2]):
                f.result()

        rm_add_vector_candidates(len(vector_keys))
        if bm25_keys and vector_keys:
            rm_increment_rrf_fusions()

        fused = reciprocal_rank_fusion_weighted(
            bm25_keys,
            vector_keys,
            bm25_weight=bm25_w,
            vector_weight=vector_w,
            k=rrf_k,
        )

        if not fused:
            # Fusion is empty only when both channels returned nothing (or the
            # lexical pool size is 0). Reuse the BM25 candidates already fetched
            # by run_bm25 instead of re-running the identical search.
            return bm25_candidates

        entry_by_key = {e.key: e for e in store.list_all(memory_group=memory_group)}
        max_rrf = fused[0][1]

        results: list[tuple[MemoryEntry, float]] = []
        for key, rrf_score in fused:
            entry = entry_by_key.get(key)
            if entry is None:
                # Experience / out-of-band writes may be durable but not cached.
                # Read-only hydrate — do not use get() (mutates access_count).
                ensure = getattr(store, "_ensure_entry_cached", None)
                if callable(ensure):
                    try:
                        entry = ensure(key)
                    except Exception:
                        entry = None
                if entry is None:
                    continue
                entry_by_key[key] = entry
            relevance_raw = rrf_score / max_rrf if max_rrf > 0 else 0.0
            results.append((entry, relevance_raw))

        if memory_group is not None:
            results = [(e, s) for e, s in results if e.memory_group == memory_group]

        return results

    def _vector_search(  # noqa: PLR0911, PLR0915
        self,
        query: str,
        store: MemoryStore,
        limit: int = 20,
        *,
        memory_group: str | None = None,
        include_expired: bool = False,
        as_of: str | None = None,
    ) -> list[tuple[str, float]]:
        """Epic 65.8: Embed query, cosine similarity with entry embeddings.

        Uses on-the-fly embedding when stored embeddings are unavailable.
        Returns [(entry_key, score), ...] sorted by score descending.

        *include_expired* is forwarded to ``store.knn_search`` so the hybrid
        dense channel matches BM25's ``include_superseded`` historical window.
        *as_of* is forwarded so point-in-time hybrid recall uses the same
        bi-temporal window on the dense channel as FTS.
        """
        empty: list[tuple[str, float]] = []

        try:
            from tapps_brain.embeddings import get_embedding_provider

            embedder = get_embedding_provider()
        except ImportError:
            logger.debug("vector_search_embedder_unavailable")
            return empty
        if embedder is None:
            return empty

        try:
            q = embedder.embed(query)
        except Exception as e:
            logger.warning("vector_search_embed_failed", error=str(e), exc_info=True)
            return empty
        if not q:
            return empty

        # pgvector HNSW KNN (ADR-007) — schema column is vector(384) (migration 001).
        # Skip the DB round-trip when the query dim cannot match, so we do not
        # sticky-flag knn_search_degraded on expected dimension mismatches.
        # Do not gate on list_all() — cold/empty cache can still have vectors in DB.
        if len(q) == _PGVECTOR_DIM:
            try:
                knn = store.knn_search(
                    q, limit, include_expired=include_expired, as_of=as_of
                )
            except Exception as e:
                logger.warning("vector_search_knn_failed", error=str(e), exc_info=True)
                # Surface degradation so health/injection do not treat this as
                # "vector channel empty" — mirror PostgresPrivateBackend sticky flag.
                persistence = getattr(store, "_persistence", None)
                if persistence is not None:
                    with contextlib.suppress(Exception):
                        persistence.knn_search_degraded = True
                knn = []
            if knn:
                scored_knn: list[tuple[str, float]] = []
                for key, dist in knn:
                    sim = 1.0 / (1.0 + max(0.0, float(dist)))
                    scored_knn.append((key, sim))
                scored_knn.sort(key=lambda x: x[1], reverse=True)
                if memory_group is not None:
                    filtered: list[tuple[str, float]] = []
                    for k, s in scored_knn:
                        # Read-only hydrate — do not use get() (mutates access_count).
                        # Guarded: one failed row load must degrade, not abort search()
                        # (matches the hydrate in _get_hybrid_candidates).
                        entry = None
                        ensure = getattr(store, "_ensure_entry_cached", None)
                        if callable(ensure):
                            try:
                                entry = ensure(k)
                            except Exception:
                                entry = None
                        elif hasattr(store, "_entries"):
                            entry = store._entries.get(k)
                        if entry is not None and entry.memory_group == memory_group:
                            filtered.append((k, s))
                    scored_knn = filtered
                return scored_knn[:limit]
        else:
            logger.info(
                "vector_search_knn_skipped_dim",
                query_dim=len(q),
                expected_dim=_PGVECTOR_DIM,
                hint="pgvector column is vector(384); falling back to corpus scoring",
            )

        # Match KNN's include_expired contract: default list_all returns
        # superseded/expired rows, which then steal hybrid RRF slots.
        all_entries = store.list_all(
            memory_group=memory_group,
            include_superseded=include_expired,
        )
        if not all_entries:
            return empty
        # Corpus embeddings are the most expensive computation in this module —
        # cache them by (group, corpus fingerprint) so repeated fallback queries
        # against an unchanged corpus do not re-embed every entry.
        cache_key = (memory_group, include_expired, self._corpus_fingerprint(all_entries))
        if cache_key == self._emb_cache_key and len(self._emb_cache) == len(all_entries):
            entry_embs = self._emb_cache
        else:
            texts = [self._entry_to_document(e) for e in all_entries]
            try:
                entry_embs = embedder.embed_batch(texts)
            except Exception as e:
                logger.warning("vector_search_embed_failed_batch", error=str(e), exc_info=True)
                return empty
            self._emb_cache_key = cache_key
            self._emb_cache = entry_embs

        if len(entry_embs) != len(all_entries):
            return empty
        scored: list[tuple[str, float]] = []
        for i, entry in enumerate(all_entries):
            emb = entry_embs[i]
            if len(emb) == len(q):
                sim = sum(a * b for a, b in zip(q, emb, strict=True))
                scored.append((entry.key, max(0.0, sim)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def _bm25_score_entries(
        self,
        query: str,
        entries: list[MemoryEntry],
        store: MemoryStore,
    ) -> list[tuple[MemoryEntry, float]]:
        """Score a set of entries using BM25.

        Builds the BM25 index over the **full project corpus** (``store.list_all()``
        without ``memory_group``) so IDF is consistent with the whole store, then
        assigns scores to the **FTS candidate subset** only. Callers typically
        pass FTS hits from ``store.search``; see ``_get_candidates`` for when
        FTS is skipped in favor of ``_bm25_full_scan``.
        """
        try:
            all_entries = store.list_all()
            self._ensure_bm25_index(all_entries, group_key=None)

            # Build a lookup: entry key -> index in corpus
            key_to_idx = {e.key: i for i, e in enumerate(self._bm25_entries)}
            all_scores = self._bm25.score(query)

            results: list[tuple[MemoryEntry, float]] = []
            for entry in entries:
                idx = key_to_idx.get(entry.key)
                if idx is not None and idx < len(all_scores):
                    results.append((entry, all_scores[idx]))
                else:
                    # Entry not in index (new entry?), use word overlap
                    results.append((entry, self._word_overlap_score(query, entry)))
        except Exception:
            logger.warning("bm25_scoring_failed_using_word_overlap", query=query, exc_info=True)
            return [(entry, self._word_overlap_score(query, entry)) for entry in entries]
        return results

    def _bm25_full_scan(
        self,
        query: str,
        store: MemoryStore,
        *,
        memory_group: str | None = None,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
        as_of: str | None = None,
        include_superseded: bool = False,
    ) -> list[tuple[MemoryEntry, float]]:
        """Full corpus BM25 scan as fallback.

        Falls back to word overlap if BM25 fails.
        """
        all_entries = store.list_all(
            memory_group=memory_group,
            include_superseded=include_superseded,
        )
        if not include_superseded or as_of or since or until:
            all_entries = [
                e
                for e in all_entries
                if _entry_matches_temporal_window(
                    e,
                    as_of=as_of,
                    since=since,
                    until=until,
                    time_field=time_field,
                    include_superseded=include_superseded,
                )
            ]
        if not all_entries:
            return []

        try:
            self._ensure_bm25_index(all_entries, group_key=memory_group)
            scores = self._bm25.score(query)
            return [
                (entry, score)
                for entry, score in zip(all_entries, scores, strict=True)
                if score > 0
            ]
        except Exception:
            logger.warning("bm25_full_scan_failed_using_word_overlap", query=query, exc_info=True)
            return self._like_search(
                query,
                store,
                memory_group=memory_group,
                since=since,
                until=until,
                time_field=time_field,
                as_of=as_of,
                include_superseded=include_superseded,
            )

    def _like_search(
        self,
        query: str,
        store: MemoryStore,
        *,
        memory_group: str | None = None,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
        as_of: str | None = None,
        include_superseded: bool = False,
    ) -> list[tuple[MemoryEntry, float]]:
        """Fallback LIKE-based search with simple word overlap scoring."""
        query_words = set(query.lower().split())
        if not query_words:
            return []

        all_entries = store.list_all(
            memory_group=memory_group,
            include_superseded=include_superseded,
        )
        if not include_superseded or as_of or since or until:
            all_entries = [
                e
                for e in all_entries
                if _entry_matches_temporal_window(
                    e,
                    as_of=as_of,
                    since=since,
                    until=until,
                    time_field=time_field,
                    include_superseded=include_superseded,
                )
            ]
        results: list[tuple[MemoryEntry, float]] = []

        for entry in all_entries:
            relevance = self._word_overlap_score(query, entry)
            if relevance > 0:
                results.append((entry, relevance))

        return results

    # -----------------------------------------------------------------------
    # Scoring helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _word_overlap_score(query: str, entry: MemoryEntry) -> float:
        """Compute word overlap between query and entry text."""
        query_words = set(query.lower().split())
        if not query_words:
            return 0.0
        entry_text = f"{entry.key} {entry.value} {' '.join(entry.tags)}".lower()
        entry_words = set(entry_text.split())
        overlap = len(query_words & entry_words)
        return overlap / len(query_words)

    def _normalize_relevance(
        self,
        raw_score: float,
        *,
        rmin: float | None = None,
        rmax: float | None = None,
    ) -> float:
        """Normalize relevance score to 0.0-1.0 range using per-query min-max.

        Returns ``(raw - rmin) / (rmax - rmin)`` clamped to [0, 1]. In the
        degenerate equal-spread case (``rmax <= rmin``) returns ``1.0`` only
        when the shared raw score is positive — an all-zero pool means no
        candidate had any text relevance, and awarding 1.0 would grant every
        one of them the full relevance weight.
        """
        if rmin is not None and rmax is not None:
            if rmax > rmin:
                scaled = (raw_score - rmin) / (rmax - rmin)
                return min(1.0, max(0.0, scaled))
            return 1.0 if rmax > 0 else 0.0
        if raw_score <= 0:
            return 0.0
        return 1.0

    @staticmethod
    def _recency_score(entry: MemoryEntry, now: datetime) -> float:
        """Compute recency score: ``1.0 / (1.0 + days_since_updated)``."""
        try:
            updated = datetime.fromisoformat(entry.updated_at)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return 0.5
        days = max((now - updated).total_seconds() / _SECONDS_PER_DAY, 0.0)
        return 1.0 / (1.0 + days)

    def _frequency_score(self, entry: MemoryEntry) -> float:
        """Compute access frequency score: ``min(1.0, access_count / cap)``.

        The frequency cap defaults to 20.0 and is configurable via
        ``scoring_config.frequency_cap`` (EPIC-010).

        The cap is floored at 1.0 defensively to prevent ``ZeroDivisionError``
        when a duck-typed ``scoring_config`` bypasses ``ScoringConfig`` Pydantic
        validation (TAP-635).
        """
        cap = max(self._frequency_cap, 1.0)
        return min(1.0, entry.access_count / cap)

    @staticmethod
    def _compute_graph_centrality(
        entry: MemoryEntry,
        entity_index: dict[str, set[str]],
        total_entries: int,
    ) -> float:
        """Compute degree centrality for *entry* via entity co-occurrence (TAP-734).

        Extracts BM25 tokens from the entry value, counts how many distinct memory
        keys share at least one entity token, then normalises by *total_entries*.

        The computation is O(|tokens|) per entry because each token maps to a
        pre-built set of keys; the union is built in a single pass.

        Returns 0.0 when the entity index is empty, *total_entries* is 0, or the
        entry shares no tokens with any other entry.
        """
        if not entity_index or total_entries == 0:
            return 0.0

        tokens = [t for t in preprocess(entry.value) if len(t) >= 3]
        if not tokens:
            return 0.0

        # Union of all keys that share at least one entity token with this entry.
        shared_keys: set[str] = set()
        for token in tokens:
            shared_keys.update(entity_index.get(token, set()))

        # Exclude the entry itself — centrality is about *other* entries.
        shared_keys.discard(entry.key)

        return min(1.0, len(shared_keys) / total_entries)

    # ------------------------------------------------------------------
    # KG neighbourhood retrieval (STORY-076.2)
    # ------------------------------------------------------------------

    def search_neighborhood(
        self,
        entity_ids: list[str],
        kg_backend: KnowledgeGraphBackend,
        *,
        hops: int = 1,
        limit: int = 100,
        predicate_filter: str | None = None,
        include_historical: bool = False,
    ) -> list[ScoredEdge]:
        """Retrieve and score the neighbourhood of resolved KG entities.

        Fetches 1-hop or 2-hop edges via ``kg_backend.get_neighbors_multi()``,
        scores each edge with :func:`score_edge`, applies ``graph_weight`` from
        the active scoring config, and returns a sorted list of
        :class:`ScoredEdge` objects (highest ``blended_score`` first).

        Non-active (stale / contradicted / superseded) edges are excluded by
        default *by the backend query* — this method forwards
        ``include_historical`` to ``get_neighbors_multi`` and does not filter
        edges itself; ``include_historical=True`` includes them.

        Args:
            entity_ids:       Resolved entity UUID strings (from :func:`analyze_query`).
            kg_backend:       Backend providing ``get_neighbors_multi``.
            hops:             Neighbourhood depth (1 or 2).
            limit:            Maximum edge rows returned from the backend.
            predicate_filter: Filter to edges with this predicate.
            include_historical: Include non-active edges.

        Returns:
            Scored edges sorted by ``blended_score`` descending.
            Returns ``[]`` when ``entity_ids`` is empty or the backend raises.
        """
        if not entity_ids:
            return []

        graph_weight = float(getattr(self._scoring_config, "graph_weight", 0.10))

        try:
            raw_edges = kg_backend.get_neighbors_multi(
                entity_ids,
                hops=hops,
                limit=limit,
                predicate_filter=predicate_filter,
                include_historical=include_historical,
            )
        except Exception:
            logger.warning(
                "neighborhood_retrieval_failed",
                entity_count=len(entity_ids),
                exc_info=True,
            )
            return []

        scored: list[ScoredEdge] = []
        for edge in raw_edges:
            raw_score = score_edge(edge)
            blended = raw_score * graph_weight
            scored.append(
                ScoredEdge(
                    edge_id=str(edge.get("edge_id") or ""),
                    predicate=str(edge.get("predicate") or ""),
                    neighbor_id=str(edge.get("neighbor_id") or ""),
                    entity_type=str(edge.get("entity_type") or ""),
                    canonical_name=str(edge.get("canonical_name") or ""),
                    hop=int(edge.get("hop") or 1),
                    score=raw_score,
                    edge_confidence=float(edge.get("edge_confidence") or 0.0),
                    evidence_count=int(edge.get("evidence_count") or 0),
                    blended_score=blended,
                )
            )

        scored.sort(key=lambda s: s.blended_score, reverse=True)
        logger.debug(
            "neighborhood_retrieval_complete",
            entity_count=len(entity_ids),
            edge_count=len(scored),
            graph_weight=graph_weight,
        )
        return scored
