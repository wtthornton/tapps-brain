"""Similarity detection for memory consolidation (Epic 58).

Provides Jaccard similarity on tags and TF (term-frequency) cosine similarity
on text content to identify related memory entries that can be consolidated.
Note: the text similarity is TF-cosine (no IDF weighting), not full TF-IDF.

STORY-SC03 (TAP-559): :func:`compute_similarity_with_embeddings` uses stored
vector embeddings as the primary similarity signal when both entries carry an
``embedding`` field, with Jaccard + TF-cosine as secondary / fallback.  Use
``use_embeddings=True`` (the default) in :func:`find_similar` and
:func:`find_consolidation_groups` to activate the semantic path.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tapps_brain.bm25 import preprocess_similarity

if TYPE_CHECKING:
    from tapps_brain.models import MemoryEntry


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

DEFAULT_SIMILARITY_THRESHOLD = 0.7
DEFAULT_TAG_WEIGHT = 0.4
DEFAULT_TEXT_WEIGHT = 0.6

# When stored embeddings are available, embedding cosine is the primary signal.
# Tag Jaccard provides the secondary signal; text TF-cosine is excluded from
# the blended score (embeddings subsume it).
DEFAULT_EMBEDDING_WEIGHT = 0.7
DEFAULT_EMBEDDING_TAG_WEIGHT = 0.3

# Minimum fraction of tags that must overlap for a match.
_MIN_TAG_OVERLAP_RATIO = 0.5


# ---------------------------------------------------------------------------
# Jaccard similarity for tags
# ---------------------------------------------------------------------------


def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two sets.

    Returns intersection / union, or 0.0 if both sets are empty.
    """
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def tag_similarity(entry_a: MemoryEntry, entry_b: MemoryEntry) -> float:
    """Compute Jaccard similarity between two entries' tags."""
    tags_a = {tag.lower() for tag in entry_a.tags}
    tags_b = {tag.lower() for tag in entry_b.tags}
    return jaccard_similarity(tags_a, tags_b)


# ---------------------------------------------------------------------------
# TF-IDF text similarity
# ---------------------------------------------------------------------------


def _term_frequency(terms: list[str]) -> dict[str, float]:
    """Compute normalized term frequency for a list of terms."""
    counter = Counter(terms)
    total = len(terms)
    if total == 0:
        return {}
    return {term: count / total for term, count in counter.items()}


def _extract_text(entry: MemoryEntry) -> str:
    """Extract searchable text from a memory entry."""
    parts = [entry.key.replace("-", " ").replace("_", " ").replace(".", " ")]
    parts.append(entry.value)
    return " ".join(parts)


def cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """Compute cosine similarity between two term vectors.

    Args:
        vec_a: Term frequency dict for document A.
        vec_b: Term frequency dict for document B.

    Returns:
        Cosine similarity in range [0.0, 1.0].
    """
    if not vec_a or not vec_b:
        return 0.0

    # Compute dot product using only shared terms (non-shared terms contribute 0)
    common_terms = set(vec_a.keys()) & set(vec_b.keys())
    dot_product = sum(vec_a[t] * vec_b[t] for t in common_terms)

    # Compute magnitudes
    mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
    mag_b = math.sqrt(sum(v * v for v in vec_b.values()))

    if mag_a == 0 or mag_b == 0:
        return 0.0

    return dot_product / (mag_a * mag_b)


def text_similarity(entry_a: MemoryEntry, entry_b: MemoryEntry) -> float:
    """Compute TF-based cosine similarity between entry text content.

    Extracts key + value text, preprocesses (lowercase, stop words, stemming),
    and computes term frequency cosine similarity.
    """
    text_a = _extract_text(entry_a)
    text_b = _extract_text(entry_b)

    terms_a = preprocess_similarity(text_a)
    terms_b = preprocess_similarity(text_b)

    tf_a = _term_frequency(terms_a)
    tf_b = _term_frequency(terms_b)

    return cosine_similarity(tf_a, tf_b)


# ---------------------------------------------------------------------------
# Combined similarity
# ---------------------------------------------------------------------------


@dataclass
class SimilarityResult:
    """Result of comparing two memory entries."""

    entry_key: str
    combined_score: float
    tag_score: float
    text_score: float
    # Populated by compute_similarity_with_embeddings when embeddings are present.
    embedding_score: float | None = None
    used_embeddings: bool = False

    def __lt__(self, other: SimilarityResult) -> bool:
        """Allow sorting by combined score (descending)."""
        return self.combined_score > other.combined_score


def compute_similarity(
    entry_a: MemoryEntry,
    entry_b: MemoryEntry,
    *,
    tag_weight: float = DEFAULT_TAG_WEIGHT,
    text_weight: float = DEFAULT_TEXT_WEIGHT,
) -> SimilarityResult:
    """Compute combined similarity between two memory entries.

    Args:
        entry_a: First memory entry.
        entry_b: Second memory entry.
        tag_weight: Weight for tag similarity (default 0.4).
        text_weight: Weight for text similarity (default 0.6).

    Returns:
        SimilarityResult with combined and component scores.
    """
    tag_score = tag_similarity(entry_a, entry_b)
    text_score = text_similarity(entry_a, entry_b)

    # Normalize weights
    total_weight = tag_weight + text_weight
    if total_weight > 0:
        tag_weight = tag_weight / total_weight
        text_weight = text_weight / total_weight
    else:
        tag_weight = 0.5
        text_weight = 0.5

    combined = (tag_score * tag_weight) + (text_score * text_weight)

    return SimilarityResult(
        entry_key=entry_b.key,
        combined_score=round(combined, 4),
        tag_score=round(tag_score, 4),
        text_score=round(text_score, 4),
    )


def compute_similarity_with_embeddings(
    entry_a: MemoryEntry,
    entry_b: MemoryEntry,
    *,
    tag_weight: float = DEFAULT_EMBEDDING_TAG_WEIGHT,
    text_weight: float = DEFAULT_TEXT_WEIGHT,
    embedding_weight: float = DEFAULT_EMBEDDING_WEIGHT,
) -> SimilarityResult:
    """Compute similarity using stored embeddings as the primary signal when available.

    When both entries carry a non-empty ``embedding`` vector, embedding cosine
    similarity is weighted at *embedding_weight* (default 0.7) and Jaccard tag
    similarity at *tag_weight* (default 0.3).  When either entry lacks an
    embedding the function falls back to the Jaccard + TF-cosine baseline via
    :func:`compute_similarity` (STORY-SC03 / TAP-559).

    Args:
        entry_a: First memory entry.
        entry_b: Second memory entry.
        tag_weight: Weight for Jaccard tag similarity in the embedding path.
        text_weight: Weight for TF-cosine text similarity in the fallback path.
        embedding_weight: Weight for embedding cosine similarity (primary signal).

    Returns:
        SimilarityResult with ``used_embeddings=True`` when the semantic path fired.
    """
    from tapps_brain.embeddings import embedding_cosine_similarity

    tag_score = tag_similarity(entry_a, entry_b)
    text_score = text_similarity(entry_a, entry_b)

    if entry_a.embedding and entry_b.embedding:
        emb_score = embedding_cosine_similarity(entry_a.embedding, entry_b.embedding)
        # Primary: embedding cosine; secondary: Jaccard tag similarity.
        total = embedding_weight + tag_weight
        combined = (emb_score * embedding_weight + tag_score * tag_weight) / total
        return SimilarityResult(
            entry_key=entry_b.key,
            combined_score=round(combined, 4),
            tag_score=round(tag_score, 4),
            text_score=round(text_score, 4),
            embedding_score=round(emb_score, 4),
            used_embeddings=True,
        )

    # Fallback: Jaccard tag + TF-cosine (original path).
    return compute_similarity(
        entry_a,
        entry_b,
        tag_weight=DEFAULT_TAG_WEIGHT,
        text_weight=text_weight,
    )


def find_similar(
    entry: MemoryEntry,
    candidates: list[MemoryEntry],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    tag_weight: float = DEFAULT_TAG_WEIGHT,
    text_weight: float = DEFAULT_TEXT_WEIGHT,
    exclude_self: bool = True,
    use_embeddings: bool = True,
) -> list[SimilarityResult]:
    """Find entries similar to the given entry above a threshold.

    Args:
        entry: The reference entry to compare against.
        candidates: List of candidate entries to check.
        threshold: Minimum combined similarity score (default 0.7).
        tag_weight: Weight for tag similarity (default 0.4).
        text_weight: Weight for text similarity (default 0.6).
        exclude_self: If True, exclude entries with the same key.
        use_embeddings: When True (default), use :func:`compute_similarity_with_embeddings`
            which activates the embedding-cosine primary signal when stored vectors are
            present (STORY-SC03 / TAP-559). Set False to force the Jaccard+TF-cosine path.

    Returns:
        List of SimilarityResult for entries above threshold, sorted by score.
    """
    results: list[SimilarityResult] = []

    for candidate in candidates:
        # Skip self-comparison if requested
        if exclude_self and candidate.key == entry.key:
            continue

        if use_embeddings:
            result = compute_similarity_with_embeddings(
                entry,
                candidate,
                text_weight=text_weight,
            )
        else:
            result = compute_similarity(
                entry,
                candidate,
                tag_weight=tag_weight,
                text_weight=text_weight,
            )

        if result.combined_score >= threshold:
            results.append(result)

    # Sort by combined score descending
    results.sort()
    return results


# ---------------------------------------------------------------------------
# Clustering for batch consolidation
# ---------------------------------------------------------------------------


def find_consolidation_groups(
    entries: list[MemoryEntry],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    tag_weight: float = DEFAULT_TAG_WEIGHT,
    text_weight: float = DEFAULT_TEXT_WEIGHT,
    min_group_size: int = 2,
    use_embeddings: bool = True,
) -> list[list[str]]:
    """Find groups of similar entries that can be consolidated.

    Uses a greedy clustering approach: for each entry not yet in a group,
    find all similar entries and form a group.

    Args:
        entries: All memory entries to analyze.
        threshold: Minimum similarity to include in group.
        tag_weight: Weight for tag similarity.
        text_weight: Weight for text similarity.
        min_group_size: Minimum entries required to form a group.
        use_embeddings: When True (default), prefer embedding cosine similarity
            when stored vectors are present (STORY-SC03 / TAP-559).

    Returns:
        List of groups, where each group is a list of entry keys.
    """
    if len(entries) < min_group_size:
        return []

    assigned: set[str] = set()
    groups: list[list[str]] = []

    for entry in entries:
        if entry.key in assigned:
            continue

        # Find similar entries not yet assigned
        similar = find_similar(
            entry,
            [e for e in entries if e.key not in assigned],
            threshold=threshold,
            tag_weight=tag_weight,
            text_weight=text_weight,
            exclude_self=True,
            use_embeddings=use_embeddings,
        )

        if len(similar) >= min_group_size - 1:
            # Form a group: the entry + all similar entries
            group = [entry.key] + [r.entry_key for r in similar]
            groups.append(group)
            assigned.update(group)

    return groups


# ---------------------------------------------------------------------------
# Same-topic detection (tier + tag overlap)
# ---------------------------------------------------------------------------


def same_topic_score(entry_a: MemoryEntry, entry_b: MemoryEntry) -> float:
    """Compute same-topic score based on tier match and tag overlap.

    Returns 1.0 if same tier and >= 50% tag overlap, 0.0 otherwise.
    This is a binary signal used alongside similarity for consolidation.
    """
    if entry_a.tier != entry_b.tier:
        return 0.0

    tags_a = {tag.lower() for tag in entry_a.tags}
    tags_b = {tag.lower() for tag in entry_b.tags}

    if not tags_a or not tags_b:
        return 0.0

    # Check if at least 50% overlap (using smaller set as base)
    smaller = min(len(tags_a), len(tags_b))
    overlap = len(tags_a & tags_b)

    if smaller > 0 and overlap / smaller >= _MIN_TAG_OVERLAP_RATIO:
        return 1.0

    return 0.0


def is_same_topic(entry_a: MemoryEntry, entry_b: MemoryEntry) -> bool:
    """Check if two entries are on the same topic."""
    return same_topic_score(entry_a, entry_b) >= 1.0
