"""Ranked memory retrieval with composite scoring.

Upgrades memory search from simple keyword matching to scored,
ranked retrieval combining text relevance with memory-specific
signals (confidence, recency, access frequency).

Uses BM25 (Okapi) for text relevance scoring with automatic
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
superseded entries (unless ``include_superseded`` / ``include_historical``), and
entries below ``min_confidence`` after decay. BM25/FTS may still index the full
corpus for IDF; ranking applies the filters above.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, Field

from tapps_brain.bm25 import BM25Scorer, preprocess
from tapps_brain.decay import DecayConfig, calculate_decayed_confidence, is_stale
from tapps_brain.lexical import LexicalRetrievalConfig
from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier, tier_str
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
from tapps_brain.reranker import RERANKER_TOP_CANDIDATES, Reranker

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

_MAX_RESULTS = 50
_DEFAULT_RESULTS = 10
_MIN_CONFIDENCE_FLOOR = 0.1


class ScoredMemory(BaseModel):
    """A memory entry with retrieval scoring metadata."""

    entry: MemoryEntry
    score: float = Field(ge=0.0, description="Composite retrieval score.")
    effective_confidence: float = Field(ge=0.0, le=1.0, description="Time-decayed confidence.")
    bm25_relevance: float = Field(ge=0.0, description="Normalized text relevance.")
    stale: bool = Field(default=False, description="Whether the memory is stale.")


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
        self._bm25_corpus_size: int = 0
        self._bm25_fingerprint: int = 0
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

    def search(  # noqa: PLR0915
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
        memory_group: str | None = None,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
        memory_filter: MemoryFilter | None = None,
    ) -> list[ScoredMemory]:
        """Search memories with ranked scoring.

        Uses the store's FTS5-backed search for candidate retrieval,
        then applies composite scoring with confidence, recency, and
        frequency signals.

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
                (marked with ``stale=True`` and a 0.5x relevance penalty).
            include_historical: Alias for ``include_superseded`` (GitHub #29, task 040.3).
                When True, include expired/superseded entries in results.
            memory_filter: Optional structured pre-filter applied before BM25/vector scoring
                (TAP-733).  When ``None`` or all fields are unset, no pre-filtering is done
                (preserves existing behaviour).  Filters are applied as hard AND conditions
                over the full candidate pool *before* IDF / vector scoring — cheaper fields
                (tier, memory_class, tags) narrow the pool first.

        Returns:
            Scored memories sorted by composite score (descending).
        """
        self.last_rerank_stats = None
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

        # Epic 65.8: hybrid path when semantic enabled
        _temporal_kw: dict[str, Any] = {}
        if since is not None:
            _temporal_kw["since"] = since
        if until is not None:
            _temporal_kw["until"] = until
        if time_field != "created_at":
            _temporal_kw["time_field"] = time_field

        if self._semantic_enabled:
            candidates = self._get_hybrid_candidates(
                effective_query, store, memory_group=effective_group, **_temporal_kw
            )
        else:
            candidates = self._get_candidates(
                effective_query, store, memory_group=effective_group, **_temporal_kw
            )

        # TAP-733: Apply structured pre-filters before BM25/vector scoring.
        # This narrows the candidate pool using cheap equality checks on structured
        # fields (tier, memory_class, tags, min_confidence) so that IDF and vector
        # scoring only run over the relevant subset.
        if memory_filter is not None:
            filtered_entries = self._apply_filters([e for e, _ in candidates], memory_filter)
            filtered_keys = {e.key for e in filtered_entries}
            candidates = [(e, s) for e, s in candidates if e.key in filtered_keys]

        # Score and filter (two phases: collect candidates, then min-max normalize)
        pending: list[
            tuple[MemoryEntry, float, float, bool, bool]
        ] = []  # entry, relevance_raw, eff_conf, stale_flag, temporally_valid

        for entry, relevance_raw in candidates:
            # Filter source entries of consolidated memories (Epic 58.5)
            if not include_sources and _is_consolidated_source(entry):
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

            # Filter low confidence
            if eff_conf < min_confidence:
                continue

            stale_flag = is_stale(entry, self._config, now=now)
            # Mark temporally invalid entries as stale (EPIC-004)
            if not temporally_valid:
                stale_flag = True

            pending.append((entry, relevance_raw, eff_conf, stale_flag, temporally_valid))

        rmin: float | None = None
        rmax: float | None = None
        if pending:
            rels = [p[1] for p in pending]
            rmin = min(rels)
            rmax = max(rels)

        # Graph centrality: read entity index from store (TAP-734).
        # Snapshot outside the loop — O(1) attribute access, not per-entry.
        _entity_index: dict[str, set[str]] = getattr(store, "_entity_index", {})
        _entity_total: int = len(getattr(store, "_entries", {}))

        scored: list[ScoredMemory] = []
        for entry, relevance_raw, eff_conf, stale_flag, temporally_valid in pending:
            relevance_norm = self._normalize_relevance(relevance_raw, rmin=rmin, rmax=rmax)
            recency = self._recency_score(entry, now)
            frequency = self._frequency_score(entry)

            # Graph centrality: degree centrality via entity co-occurrence (TAP-734).
            graph_centrality = (
                self._compute_graph_centrality(entry, _entity_index, _entity_total)
                if self._w_graph > 0.0
                else 0.0
            )

            # Provenance trust: source_trust * channel_trust (channel_trust=1.0 for now)
            source_key_pt = (
                entry.source.value if isinstance(entry.source, MemorySource) else str(entry.source)
            )
            channel_trust = 1.0
            provenance_trust = self._source_trust.get(source_key_pt, 1.0) * channel_trust

            composite = (
                self._w_relevance * relevance_norm
                + self._w_confidence * eff_conf
                + self._w_recency * recency
                + self._w_frequency * frequency
                + self._w_graph * graph_centrality
                + self._w_provenance * provenance_trust
            )

            # M2: Apply per-source trust multiplier
            source_key = (
                entry.source.value if isinstance(entry.source, MemorySource) else str(entry.source)
            )
            trust = self._source_trust.get(source_key, 1.0)
            composite *= trust

            # Penalty for superseded entries included via include_superseded
            if not temporally_valid:
                composite *= 0.5

            # Bonus for exact key match (capped at 1.0 to keep score in valid range)
            if entry.key == query.lower().replace(" ", "-"):
                composite = min(composite + 0.1, 1.0)

            scored.append(
                ScoredMemory(
                    entry=entry,
                    score=round(composite, 4),
                    effective_confidence=round(eff_conf, 4),
                    bm25_relevance=round(relevance_norm, 4),
                    stale=stale_flag,
                )
            )

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
        result: list[ScoredMemory] = []
        for key, rerank_score in reranked:
            sm = key_to_scored.get(key)
            if sm is not None:
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
        try:
            from tapps_brain.relations import (
                RelationEntry,
                expand_via_relations,
            )
        except ImportError:
            return query

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
        return hash(tuple((e.key, e.updated_at if e.updated_at else "") for e in entries))

    def _ensure_bm25_index(self, entries: list[MemoryEntry]) -> None:
        """Build or rebuild the BM25 index when the corpus changes."""
        fingerprint = self._corpus_fingerprint(entries)
        if len(entries) == self._bm25_corpus_size and fingerprint == self._bm25_fingerprint:
            return
        documents = [self._entry_to_document(e) for e in entries]
        self._bm25.build_index(documents)
        self._bm25_entries = list(entries)
        self._bm25_corpus_size = len(entries)
        self._bm25_fingerprint = fingerprint
        logger.debug("bm25_index_rebuilt", corpus_size=self._bm25_corpus_size)

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
    ) -> list[tuple[MemoryEntry, float]]:
        """Retrieve candidate entries and compute BM25 relevance scores.

        Tries the store's FTS5-backed search first for candidate
        filtering, then scores them using BM25. Falls back to full
        in-memory BM25 scan if FTS5 returns no results, and to word
        overlap if BM25 scoring fails entirely.
        """
        # Try FTS5 via store.search() for candidate filtering
        try:
            fts_results = store.search(
                query,
                memory_group=memory_group,
                since=since,
                until=until,
                time_field=time_field,
            )
            if fts_results:
                results = self._bm25_score_entries(query, fts_results, store)
                rm_add_bm25_candidates(len(results))
                return results
        except Exception:
            logger.warning("fts5_search_failed", query=query, exc_info=True)

        # Fallback: full corpus BM25 scan
        results = self._bm25_full_scan(query, store, memory_group=memory_group)
        rm_add_bm25_candidates(len(results))
        return results

    def _get_hybrid_candidates(
        self,
        query: str,
        store: MemoryStore,
        *,
        memory_group: str | None = None,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
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

        from tapps_brain.fusion import hybrid_rrf_weights_for_query, reciprocal_rank_fusion_weighted

        adaptive_fusion = _hybrid_adaptive_fusion_enabled(self._hybrid_config)
        bm25_w, vector_w = (1.0, 1.0)
        if adaptive_fusion:
            bm25_w, vector_w = hybrid_rrf_weights_for_query(query)

        bm25_keys: list[str] = []
        vector_keys: list[str] = []

        def run_bm25() -> None:
            nonlocal bm25_keys
            candidates = self._get_candidates(
                query,
                store,
                memory_group=memory_group,
                since=since,
                until=until,
                time_field=time_field,
            )
            # Take top top_k_lexical by score
            sorted_cands = sorted(
                candidates,
                key=lambda x: x[1],
                reverse=True,
            )[:top_k_lexical]
            bm25_keys = [e.key for e, _ in sorted_cands]

        def run_vector() -> None:
            nonlocal vector_keys
            vector_results = self._vector_search(
                query, store, limit=top_k_dense, memory_group=memory_group
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
            return self._get_candidates(query, store, memory_group=memory_group)

        entry_by_key = {e.key: e for e in store.list_all(memory_group=memory_group)}
        max_rrf = fused[0][1] if fused else 1.0

        results: list[tuple[MemoryEntry, float]] = []
        for key, rrf_score in fused:
            entry = entry_by_key.get(key)
            if entry is None:
                continue
            relevance_raw = rrf_score / max_rrf if max_rrf > 0 else 0.0
            results.append((entry, relevance_raw))

        if memory_group is not None:
            results = [(e, s) for e, s in results if e.memory_group == memory_group]

        return results

    def _vector_search(  # noqa: PLR0911
        self,
        query: str,
        store: MemoryStore,
        limit: int = 20,
        *,
        memory_group: str | None = None,
    ) -> list[tuple[str, float]]:
        """Epic 65.8: Embed query, cosine similarity with entry embeddings.

        Uses on-the-fly embedding when stored embeddings are unavailable.
        Returns [(entry_key, score), ...] sorted by score descending.
        """
        empty: list[tuple[str, float]] = []

        try:
            from tapps_brain.embeddings import get_embedding_provider

            embedder = get_embedding_provider()
        except ImportError:
            logger.debug("vector_search_embedder_unavailable")
            return empty
        if embedder is None or not store.list_all(memory_group=memory_group):
            return empty

        try:
            q = embedder.embed(query)
        except Exception as e:
            logger.warning("vector_search_embed_failed", error=str(e), exc_info=True)
            return empty
        if not q:
            return empty

        # pgvector HNSW KNN (ADR-007) — always available under the Postgres
        # private backend.  Returns cosine distance; map to a bounded similarity
        # score for RRF (monotonic for dist >= 0).
        if len(q) == 384:  # pgvector schema is vector(384) — see migration 001
            knn = store.knn_search(q, limit)
            if knn:
                scored_knn: list[tuple[str, float]] = []
                for key, dist in knn:
                    sim = 1.0 / (1.0 + max(0.0, float(dist)))
                    scored_knn.append((key, sim))
                scored_knn.sort(key=lambda x: x[1], reverse=True)
                if memory_group is not None:
                    with store._lock:
                        allowed = {
                            k for k, e in store._entries.items() if e.memory_group == memory_group
                        }
                    scored_knn = [(k, s) for k, s in scored_knn if k in allowed]
                return scored_knn[:limit]

        all_entries = store.list_all(memory_group=memory_group)
        texts = [self._entry_to_document(e) for e in all_entries]
        try:
            entry_embs = embedder.embed_batch(texts)
        except Exception as e:
            logger.warning("vector_search_embed_failed_batch", error=str(e), exc_info=True)
            return empty

        if len(entry_embs) != len(all_entries):
            return empty
        scored: list[tuple[str, float]] = []
        for i, entry in enumerate(all_entries):
            if i >= len(entry_embs):
                break
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
            self._ensure_bm25_index(all_entries)

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
            return results
        except Exception:
            logger.warning("bm25_scoring_failed_using_word_overlap", query=query, exc_info=True)
            return [(entry, self._word_overlap_score(query, entry)) for entry in entries]

    def _bm25_full_scan(
        self,
        query: str,
        store: MemoryStore,
        *,
        memory_group: str | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """Full corpus BM25 scan as fallback.

        Falls back to word overlap if BM25 fails.
        """
        all_entries = store.list_all(memory_group=memory_group)
        if not all_entries:
            return []

        try:
            self._ensure_bm25_index(all_entries)
            scores = self._bm25.score(query)
            return [
                (entry, score)
                for entry, score in zip(all_entries, scores, strict=True)
                if score > 0
            ]
        except Exception:
            logger.warning("bm25_full_scan_failed_using_word_overlap", query=query, exc_info=True)
            return self._like_search(query, store, memory_group=memory_group)

    def _like_search(
        self,
        query: str,
        store: MemoryStore,
        *,
        memory_group: str | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """Fallback LIKE-based search with simple word overlap scoring."""
        query_words = set(query.lower().split())
        if not query_words:
            return []

        all_entries = store.list_all(memory_group=memory_group)
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

        Returns ``(raw - rmin) / (rmax - rmin)`` clamped to [0, 1], or ``1.0``
        when ``rmax <= rmin`` (degenerate spread) or bounds are not provided.
        """
        if rmin is not None and rmax is not None:
            if rmax > rmin:
                scaled = (raw_score - rmin) / (rmax - rmin)
                return min(1.0, max(0.0, scaled))
            return 1.0
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
        days = max((now - updated).total_seconds() / 86400.0, 0.0)
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
