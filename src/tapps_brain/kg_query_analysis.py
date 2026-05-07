"""Deterministic entity-mention extraction and KG resolver wiring.

STORY-076.1 — Entity mention extraction + resolver wiring.

Extracts candidate entity surface forms from a query string using a
regex-based heuristic (no LLM), then resolves each candidate against the KG
via a single batched SQL round-trip (:meth:`KnowledgeGraphBackend.batch_resolve_entities`).

Design constraints
------------------
* Deterministic, LLM-free — hot path cannot afford a model call.
* Single SQL round-trip per :func:`analyze_query` call.
* Graceful degradation: when ``kg_backend`` is ``None`` or raises, returns
  an empty :class:`QueryAnalysis` so callers never fail on the extraction path.
* Stopword filtering keeps candidate count manageable.  The list is purposely
  small — we are not doing full NLP, just noise reduction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from tapps_brain._protocols import KnowledgeGraphBackend

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Internal extraction helpers
# ---------------------------------------------------------------------------

#: Stopwords skipped during candidate extraction.  Lowercase only.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "are", "was", "were", "has", "have", "had",
        "this", "that", "with", "from", "into", "onto", "over", "under",
        "how", "what", "when", "where", "which", "who", "why",
        "can", "could", "should", "would", "will", "may", "might",
        "not", "but", "yet", "nor", "also", "then", "than",
        "some", "any", "all", "each", "every", "both", "more", "most",
        "about", "after", "before", "between", "during",
        "there", "their", "they", "them", "its", "our", "your",
        "been", "being", "does", "did", "doing",
    }
)

#: Minimum candidate length (chars).  Shorter strings produce too many false
#: positives (acronyms, prepositions) for no gain.
_MIN_CANDIDATE_LEN: int = 4

#: Maximum number of candidates to send to the DB per query.
#: Prevents pathological inputs from issuing very large ANY(%s) arrays.
_MAX_CANDIDATES: int = 64

# Match runs of Title-Case words (e.g. "Memory Retriever", "Postgres Backend").
_TITLE_CASE_RE = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+)\b")

# Match a single word (letters + digits + underscores/hyphens; no spaces).
_WORD_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_\-]{2,})\b")


def _extract_candidates(query: str) -> list[str]:
    """Return deduplicated candidate strings from *query*.

    Strategy:
    1. Extract multi-word Title Case phrases first (higher signal).
    2. Extract individual tokens ≥ ``_MIN_CANDIDATE_LEN`` chars, skipping
       stopwords, digits-only, and already-captured phrase tokens.
    3. Deduplicate preserving insertion order.
    4. Cap at ``_MAX_CANDIDATES``.
    """
    seen: set[str] = set()
    candidates: list[str] = []

    def _add(text: str) -> None:
        norm = text.strip()
        low = norm.lower()
        if low and low not in seen:
            seen.add(low)
            candidates.append(norm)

    # Pass 1: multi-word phrases.
    for m in _TITLE_CASE_RE.finditer(query):
        phrase = m.group(1)
        _add(phrase)
        # Also record individual words of the phrase so single-word lookup works.
        for word in phrase.split():
            _add(word)

    # Pass 2: individual words.
    for m in _WORD_RE.finditer(query):
        word = m.group(1)
        if (
            len(word) >= _MIN_CANDIDATE_LEN
            and word.lower() not in _STOPWORDS
            and not word.isdigit()
        ):
            _add(word)

    return candidates[:_MAX_CANDIDATES]


# ---------------------------------------------------------------------------
# Public data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityMention:
    """A resolved entity mention found in a query.

    Attributes:
        surface:    The original (un-lowercased) candidate string.
        entity_id:  UUID of the matched KG entity.
        confidence: Resolver confidence (0-1).
        reason:     One of ``"exact_match"``, ``"alias_match"``,
                    ``"ambiguous_alias"``.
    """

    surface: str
    entity_id: str
    confidence: float
    reason: str


@dataclass
class QueryAnalysis:
    """Result of :func:`analyze_query`.

    Attributes:
        mentions:  Entity mentions that were successfully resolved.
        unmatched: Candidate surface strings that could not be resolved.
    """

    mentions: list[EntityMention] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        """Number of successfully resolved mentions."""
        return len(self.mentions)

    @property
    def unmatched_count(self) -> int:
        """Number of candidates that did not resolve."""
        return len(self.unmatched)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_EMPTY_ANALYSIS = QueryAnalysis()


def analyze_query(
    query: str,
    kg_backend: KnowledgeGraphBackend | None,
) -> QueryAnalysis:
    """Extract and resolve entity mentions from *query*.

    Args:
        query:      The user query string.
        kg_backend: A :class:`~tapps_brain._protocols.KnowledgeGraphBackend`
                    instance.  When ``None``, returns an empty
                    :class:`QueryAnalysis` immediately (memory-only path).

    Returns:
        A :class:`QueryAnalysis` with resolved ``mentions`` and ``unmatched``
        candidates.  Never raises — errors are logged and swallowed so the
        recall pipeline degrades gracefully.
    """
    if kg_backend is None:
        return _EMPTY_ANALYSIS

    candidates = _extract_candidates(query)
    if not candidates:
        return _EMPTY_ANALYSIS

    try:
        resolved = kg_backend.batch_resolve_entities(candidates)
    except Exception:
        logger.warning(
            "kg_query_analysis.batch_resolve_failed",
            candidate_count=len(candidates),
            exc_info=True,
        )
        return _EMPTY_ANALYSIS

    mentions: list[EntityMention] = []
    unmatched: list[str] = []

    for surface in candidates:
        norm = surface.lower()
        if norm in resolved:
            entity_id, confidence, reason = resolved[norm]
            mentions.append(
                EntityMention(
                    surface=surface,
                    entity_id=entity_id,
                    confidence=confidence,
                    reason=reason,
                )
            )
        else:
            unmatched.append(surface)

    logger.debug(
        "kg_query_analysis.analyzed",
        candidates=len(candidates),
        matched=len(mentions),
        unmatched=len(unmatched),
    )
    return QueryAnalysis(mentions=mentions, unmatched=unmatched)
