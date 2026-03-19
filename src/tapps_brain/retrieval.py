"""Ranked memory retrieval with composite scoring.

Upgrades memory search from simple keyword matching to scored,
ranked retrieval combining text relevance with memory-specific
signals (confidence, recency, access frequency).

Uses BM25 (Okapi) for text relevance scoring with automatic
index building and invalidation. Epic 65.8: hybrid BM25 + vector
search with RRF when semantic_search.enabled.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

from tapps_brain.bm25 import BM25Scorer
from tapps_brain.decay import DecayConfig, calculate_decayed_confidence, is_stale
from tapps_brain.models import MemoryEntry  # noqa: TC001
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

# BM25 relevance normalization constant: score / (score + K)
# Chosen so that a BM25 score of 5.0 maps to ~0.5 normalized.
_BM25_NORM_K = 5.0


class ScoredMemory(BaseModel):
    """A memory entry with retrieval scoring metadata."""

    entry: MemoryEntry
    score: float = Field(ge=0.0, description="Composite retrieval score.")
    effective_confidence: float = Field(ge=0.0, le=1.0, description="Time-decayed confidence.")
    bm25_relevance: float = Field(ge=0.0, description="Normalized text relevance.")
    stale: bool = Field(default=False, description="Whether the memory is stale.")


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

_W_RELEVANCE = 0.40
_W_CONFIDENCE = 0.30
_W_RECENCY = 0.15
_W_FREQUENCY = 0.15

_FREQUENCY_CAP = 20.0

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


# ---------------------------------------------------------------------------
# MemoryRetriever
# ---------------------------------------------------------------------------


class MemoryRetriever:
    """Ranked retrieval engine for memory entries."""

    def __init__(
        self,
        config: DecayConfig | None = None,
        *,
        semantic_enabled: bool = False,
        hybrid_config: object = None,
        reranker: Reranker | None = None,
        reranker_enabled: bool = False,
        retrieval_policy: object | None = None,
        relations_enabled: bool = False,
        expand_queries: bool = True,
    ) -> None:
        self._config = config or DecayConfig()
        self._bm25 = BM25Scorer()
        self._bm25_entries: list[MemoryEntry] = []
        self._bm25_corpus_size: int = 0
        self._bm25_fingerprint: int = 0
        self._semantic_enabled = semantic_enabled
        self._hybrid_config = hybrid_config
        self._reranker = reranker
        self._reranker_enabled = reranker_enabled
        self._retrieval_policy = retrieval_policy
        self._relations_enabled = relations_enabled
        self._expand_queries = expand_queries

    def search(
        self,
        query: str,
        store: MemoryStore,
        *,
        limit: int = _DEFAULT_RESULTS,
        include_contradicted: bool = False,
        include_sources: bool = False,
        min_confidence: float = _MIN_CONFIDENCE_FLOOR,
    ) -> list[ScoredMemory]:
        """Search memories with ranked scoring.

        Uses the store's FTS5-backed search for candidate retrieval,
        then applies composite scoring with confidence, recency, and
        frequency signals.

        Args:
            query: Search query string.
            store: Memory store to search.
            limit: Max results (default 10, max 50).
            include_contradicted: Include contradicted memories.
            include_sources: Include source entries of consolidated memories
                (Epic 58, Story 58.5). When False (default), entries that were
                consolidated into other entries are filtered out. When True,
                source entries are included alongside consolidated entries.
            min_confidence: Minimum confidence filter.

        Returns:
            Scored memories sorted by composite score (descending).
        """
        if not query or not query.strip():
            return []

        limit = max(1, min(limit, _MAX_RESULTS))
        now = datetime.now(tz=UTC)

        # Epic 65.13: expand query via relations when enabled
        effective_query = query
        if self._relations_enabled and self._expand_queries:
            effective_query = self._expand_query_via_relations(query, store)

        # Epic 65.8: hybrid path when semantic enabled
        if self._semantic_enabled:
            candidates = self._get_hybrid_candidates(effective_query, store)
        else:
            candidates = self._get_candidates(effective_query, store)

        # Score and filter
        scored: list[ScoredMemory] = []
        for entry, relevance_raw in candidates:
            # Filter source entries of consolidated memories (Epic 58.5)
            if not include_sources and _is_consolidated_source(entry):
                continue

            # Filter contradicted entries (sources already handled above)
            is_included_source = include_sources and _is_consolidated_source(entry)
            if entry.contradicted and not include_contradicted and not is_included_source:
                continue

            # Calculate effective confidence
            eff_conf = calculate_decayed_confidence(entry, self._config, now=now)

            # Filter low confidence
            if eff_conf < min_confidence:
                continue

            stale_flag = is_stale(entry, self._config, now=now)

            # Compute composite score
            relevance_norm = self._normalize_relevance(relevance_raw)
            recency = self._recency_score(entry, now)
            frequency = self._frequency_score(entry)

            composite = (
                _W_RELEVANCE * relevance_norm
                + _W_CONFIDENCE * eff_conf
                + _W_RECENCY * recency
                + _W_FREQUENCY * frequency
            )

            # Bonus for exact key match
            if entry.key == query.lower().replace(" ", "-"):
                composite += 0.1

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
        assert self._reranker is not None  # caller checks before calling
        top_candidates = scored[:RERANKER_TOP_CANDIDATES]
        candidates = [(sm.entry.key, sm.entry.value) for sm in top_candidates]
        effective_top_k = min(limit, len(candidates))

        try:
            reranked = self._reranker.rerank(query, candidates, top_k=effective_top_k)
        except Exception as e:
            logger.warning(
                "reranker_failed_fallback_to_original",
                reason=str(e),
            )
            return scored

        if not reranked:
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
        return result[:limit]

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
            logger.debug("relation_expansion_failed", query=query)

        return query

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
    ) -> list[tuple[MemoryEntry, float]]:
        """Retrieve candidate entries and compute BM25 relevance scores.

        Tries the store's FTS5-backed search first for candidate
        filtering, then scores them using BM25. Falls back to full
        in-memory BM25 scan if FTS5 returns no results, and to word
        overlap if BM25 scoring fails entirely.
        """
        # Try FTS5 via store.search() for candidate filtering
        try:
            fts_results = store.search(query)
            if fts_results:
                return self._bm25_score_entries(query, fts_results, store)
        except Exception:
            logger.debug("fts5_search_failed", query=query)

        # Fallback: full corpus BM25 scan
        return self._bm25_full_scan(query, store)

    def _get_hybrid_candidates(
        self,
        query: str,
        store: MemoryStore,
    ) -> list[tuple[MemoryEntry, float]]:
        """Epic 65.8: Run BM25 + vector search in parallel, merge with RRF."""
        top_bm25 = 20
        top_vector = 20
        rrf_k = 60
        if self._hybrid_config is not None:
            top_bm25 = getattr(self._hybrid_config, "top_bm25", 20)
            top_vector = getattr(self._hybrid_config, "top_vector", 20)
            rrf_k = getattr(self._hybrid_config, "rrf_k", 60)

        bm25_keys: list[str] = []
        vector_keys: list[str] = []

        def run_bm25() -> None:
            nonlocal bm25_keys
            candidates = self._get_candidates(query, store)
            # Take top top_bm25 by score
            sorted_cands = sorted(
                candidates,
                key=lambda x: x[1],
                reverse=True,
            )[:top_bm25]
            bm25_keys = [e.key for e, _ in sorted_cands]

        def run_vector() -> None:
            nonlocal vector_keys
            vector_results = self._vector_search(query, store, limit=top_vector)
            vector_keys = [k for k, _ in vector_results]

        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(run_bm25)
            f2 = ex.submit(run_vector)
            for f in as_completed([f1, f2]):
                f.result()

        from tapps_brain.fusion import reciprocal_rank_fusion

        fused = reciprocal_rank_fusion(bm25_keys, vector_keys, k=rrf_k)

        if not fused:
            return self._get_candidates(query, store)

        entry_by_key = {e.key: e for e in store.list_all()}
        max_rrf = fused[0][1] if fused else 1.0

        results: list[tuple[MemoryEntry, float]] = []
        for key, rrf_score in fused:
            entry = entry_by_key.get(key)
            if entry is None:
                continue
            relevance_raw = rrf_score / max_rrf if max_rrf > 0 else 0.0
            results.append((entry, relevance_raw))

        return results

    def _vector_search(
        self,
        query: str,
        store: MemoryStore,
        limit: int = 20,
    ) -> list[tuple[str, float]]:
        """Epic 65.8: Embed query, cosine similarity with entry embeddings.

        Uses on-the-fly embedding when stored embeddings are unavailable.
        Returns [(entry_key, score), ...] sorted by score descending.
        """
        empty: list[tuple[str, float]] = []

        try:
            from tapps_brain.embeddings import get_embedding_provider

            embedder = get_embedding_provider(semantic_search_enabled=True)
        except ImportError:
            logger.debug("vector_search_embedder_unavailable")
            return empty
        if embedder is None or not (all_entries := store.list_all()):
            return empty

        texts = [self._entry_to_document(e) for e in all_entries]
        try:
            q = embedder.embed(query)
            entry_embs = embedder.embed_batch(texts)
        except Exception as e:
            logger.debug("vector_search_embed_failed", error=str(e))
            return empty

        if not q or len(entry_embs) != len(all_entries):
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

        Builds the BM25 index over the full corpus (for proper IDF),
        then looks up scores for the given entries.
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
            logger.debug("bm25_scoring_failed_using_word_overlap", query=query)
            return [(entry, self._word_overlap_score(query, entry)) for entry in entries]

    def _bm25_full_scan(
        self,
        query: str,
        store: MemoryStore,
    ) -> list[tuple[MemoryEntry, float]]:
        """Full corpus BM25 scan as fallback.

        Falls back to word overlap if BM25 fails.
        """
        all_entries = store.list_all()
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
            logger.debug("bm25_full_scan_failed_using_word_overlap", query=query)
            return self._like_search(query, store)

    def _like_search(
        self,
        query: str,
        store: MemoryStore,
    ) -> list[tuple[MemoryEntry, float]]:
        """Fallback LIKE-based search with simple word overlap scoring."""
        query_words = set(query.lower().split())
        if not query_words:
            return []

        all_entries = store.list_all()
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

    @staticmethod
    def _normalize_relevance(raw_score: float) -> float:
        """Normalize relevance score to 0.0-1.0 range.

        Uses sigmoid normalization: ``score / (score + K)`` where
        K=5.0, tuned for BM25 scores (typical range 0-15+).
        A BM25 score of 5.0 maps to 0.5 normalized.
        """
        if raw_score <= 0:
            return 0.0
        return raw_score / (raw_score + _BM25_NORM_K)

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

    @staticmethod
    def _frequency_score(entry: MemoryEntry) -> float:
        """Compute access frequency score: ``min(1.0, access_count / 20)``."""
        return min(1.0, entry.access_count / _FREQUENCY_CAP)
