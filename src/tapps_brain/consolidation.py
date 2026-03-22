"""Memory consolidation engine (Epic 58, Story 58.2).

Merges related memory entries into consolidated summaries with provenance tracking.
Uses deterministic merging (no LLM calls) - newest value wins for conflicts.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

import structlog

from tapps_brain.models import (
    ConsolidatedEntry,
    ConsolidationReason,
    MemoryEntry,
    MemorySource,
    MemoryTier,
)
from tapps_brain.relations import RelationEntry
from tapps_brain.similarity import (
    DEFAULT_SIMILARITY_THRESHOLD,
    find_similar,
    is_same_topic,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MIN_ENTRIES_TO_CONSOLIDATE = 2
MAX_CONSOLIDATED_VALUE_LENGTH = 4096
_MIN_PREFIX_LENGTH = 3
_MIN_ENTRIES_FOR_CONSOLIDATION = 2


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def generate_consolidated_key(entries: list[MemoryEntry]) -> str:
    """Generate a unique key for a consolidated entry.

    Uses a hash of source keys combined with a prefix derived from
    common tags or the most frequent key prefix.

    Args:
        entries: Source entries being consolidated.

    Returns:
        A unique key for the consolidated entry.
    """
    if not entries:
        return "consolidated-empty"

    # Extract common prefix from keys
    keys = [e.key for e in entries]
    common_prefix = _find_common_prefix(keys)

    # If no common prefix, use common tags
    if not common_prefix or len(common_prefix) < _MIN_PREFIX_LENGTH:
        all_tags = [tag.lower() for e in entries for tag in e.tags]
        if all_tags:
            # Use most common tag
            tag_counts: dict[str, int] = {}
            for tag in all_tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            common_prefix = max(tag_counts, key=lambda t: tag_counts[t])
        else:
            common_prefix = "consolidated"

    # Create deterministic hash from sorted source keys
    sorted_keys = sorted(keys)
    hash_input = "-".join(sorted_keys)
    hash_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]

    # Clean prefix to match key format
    clean_prefix = re.sub(r"[^a-z0-9]", "-", common_prefix.lower())
    clean_prefix = re.sub(r"-+", "-", clean_prefix).strip("-")

    if not clean_prefix:
        clean_prefix = "consolidated"

    return f"{clean_prefix}-{hash_suffix}"


def _find_common_prefix(keys: list[str]) -> str:
    """Find the longest common prefix among keys."""
    if not keys:
        return ""
    if len(keys) == 1:
        return keys[0].split("-")[0] if "-" in keys[0] else keys[0][:10]

    # Find common prefix character by character
    prefix = []
    for chars in zip(*keys, strict=False):
        if len(set(chars)) == 1:
            prefix.append(chars[0])
        else:
            break

    result = "".join(prefix).rstrip("-_.")
    return result


# ---------------------------------------------------------------------------
# Value merging
# ---------------------------------------------------------------------------


def merge_values(entries: list[MemoryEntry]) -> str:
    """Merge values from multiple entries.

    Strategy: newest-wins for the primary value, with a summary section
    listing key points from all entries.

    Args:
        entries: Entries to merge, should be sorted by updated_at.

    Returns:
        Merged value string, truncated to max length if needed.
    """
    if not entries:
        return ""

    if len(entries) == 1:
        return entries[0].value

    # Sort by updated_at (newest first)
    sorted_entries = sorted(
        entries,
        key=lambda e: e.updated_at,
        reverse=True,
    )

    # Start with newest entry's value
    newest = sorted_entries[0]
    merged_parts = [newest.value]

    # Add context from older entries if they have unique information
    seen_sentences = _extract_sentences(newest.value)

    for entry in sorted_entries[1:]:
        new_sentences = _extract_sentences(entry.value)
        unique_sentences = [s for s in new_sentences if s not in seen_sentences]

        if unique_sentences:
            merged_parts.append(f"[From {entry.key}]: " + " ".join(unique_sentences[:2]))
            seen_sentences.update(unique_sentences)

    merged = " ".join(merged_parts)

    # Truncate if too long
    if len(merged) > MAX_CONSOLIDATED_VALUE_LENGTH:
        merged = merged[: MAX_CONSOLIDATED_VALUE_LENGTH - 3] + "..."

    return merged


def _extract_sentences(text: str) -> set[str]:
    """Extract normalized sentences from text for deduplication."""
    # Simple sentence splitting
    sentences = re.split(r"[.!?]+", text)
    return {s.strip().lower() for s in sentences if s.strip()}


# ---------------------------------------------------------------------------
# Confidence calculation
# ---------------------------------------------------------------------------


def calculate_weighted_confidence(entries: list[MemoryEntry]) -> float:
    """Calculate weighted average confidence with recency bias.

    More recent entries get higher weights. Uses exponential decay
    based on position in the sorted list.

    Args:
        entries: Entries with confidence scores.

    Returns:
        Weighted average confidence in range [0.0, 1.0].
    """
    if not entries:
        return 0.5

    if len(entries) == 1:
        return entries[0].confidence

    # Sort by updated_at (newest first)
    sorted_entries = sorted(
        entries,
        key=lambda e: e.updated_at,
        reverse=True,
    )

    # Calculate weights with exponential decay
    # Weight for position i: 0.5^i (so newest=1.0, next=0.5, etc.)
    weights = [0.5**i for i in range(len(sorted_entries))]
    total_weight = sum(weights)

    # Weighted average
    weighted_sum = sum(e.confidence * w for e, w in zip(sorted_entries, weights, strict=True))

    return min(1.0, max(0.0, weighted_sum / total_weight))


# ---------------------------------------------------------------------------
# Tag merging
# ---------------------------------------------------------------------------


def merge_tags(entries: list[MemoryEntry], max_tags: int = 10) -> list[str]:
    """Merge tags from multiple entries, prioritizing common tags.

    Args:
        entries: Entries with tags.
        max_tags: Maximum number of tags to include.

    Returns:
        Merged tag list, limited to max_tags.
    """
    if not entries:
        return []

    # Count tag occurrences
    tag_counts: dict[str, int] = {}
    for entry in entries:
        for tag in entry.tags:
            tag_lower = tag.lower()
            tag_counts[tag_lower] = tag_counts.get(tag_lower, 0) + 1

    # Sort by count (descending), then alphabetically
    sorted_tags = sorted(
        tag_counts.keys(),
        key=lambda t: (-tag_counts[t], t),
    )

    return sorted_tags[:max_tags]


# ---------------------------------------------------------------------------
# Tier selection
# ---------------------------------------------------------------------------


def select_tier(entries: list[MemoryEntry]) -> MemoryTier | str:
    """Select the most appropriate tier for consolidated entry.

    Priority: architectural > pattern > context.
    If entries have different tiers, use the most durable one.

    Args:
        entries: Entries with tier classifications.

    Returns:
        Selected tier for the consolidated entry.
    """
    if not entries:
        return MemoryTier.pattern

    tier_priority: dict[MemoryTier | str, int] = {
        MemoryTier.architectural: 4,
        MemoryTier.pattern: 3,
        MemoryTier.procedural: 2,  # Epic 65.11
        MemoryTier.context: 1,
    }

    # Find highest priority tier
    best_entry = max(entries, key=lambda e: tier_priority.get(e.tier, 0))
    return best_entry.tier


# ---------------------------------------------------------------------------
# Main consolidation function
# ---------------------------------------------------------------------------


def consolidate(
    entries: list[MemoryEntry],
    *,
    reason: ConsolidationReason = ConsolidationReason.similarity,
) -> ConsolidatedEntry:
    """Consolidate multiple memory entries into a single entry.

    Args:
        entries: Entries to consolidate (minimum 2).
        reason: Why consolidation is happening.

    Returns:
        A new ConsolidatedEntry with merged content and provenance.

    Raises:
        ValueError: If fewer than 2 entries provided.
    """
    if len(entries) < _MIN_ENTRIES_FOR_CONSOLIDATION:
        msg = f"Need at least 2 entries to consolidate, got {len(entries)}"
        raise ValueError(msg)

    # Generate consolidated key
    key = generate_consolidated_key(entries)

    # Merge values (newest-wins)
    value = merge_values(entries)

    # Calculate weighted confidence
    confidence = calculate_weighted_confidence(entries)

    # Merge tags
    tags = merge_tags(entries)

    # Select tier
    tier = select_tier(entries)

    # Track source IDs
    source_ids = [e.key for e in entries]

    # Use newest entry's scope
    sorted_by_updated = sorted(entries, key=lambda e: e.updated_at, reverse=True)
    scope = sorted_by_updated[0].scope

    # Create consolidated entry
    now = datetime.now(tz=UTC).isoformat()
    consolidated = ConsolidatedEntry(
        key=key,
        value=value,
        tier=tier,
        confidence=confidence,
        source=MemorySource.system,
        source_agent="tapps-consolidation",
        scope=scope,
        tags=tags,
        created_at=now,
        updated_at=now,
        last_accessed=now,
        source_ids=source_ids,
        consolidated_at=now,
        consolidation_reason=reason,
    )

    # Epic 65.12: extract relations from consolidated value (optional)
    try:
        from tapps_brain.relations import extract_relations as _extract_rels

        relations = _extract_rels(key, value)
        if relations:
            logger.debug(
                "consolidation_relations_extracted",
                key=key,
                relation_count=len(relations),
            )
    except ImportError:
        pass

    logger.debug(
        "memory_consolidated",
        key=key,
        source_count=len(entries),
        source_ids=source_ids,
        reason=reason,
        confidence=round(confidence, 3),
    )

    return consolidated


# ---------------------------------------------------------------------------
# Consolidation detection
# ---------------------------------------------------------------------------


def should_consolidate(
    entry: MemoryEntry,
    candidates: list[MemoryEntry],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[MemoryEntry]:
    """Check if an entry should be consolidated with existing entries.

    Args:
        entry: The new or updated entry.
        candidates: Existing entries to check against.
        threshold: Similarity threshold for consolidation.

    Returns:
        List of entries that should be consolidated with the given entry.
        Returns empty list if no consolidation needed.
    """
    # Filter out already-consolidated entries from candidates
    active_candidates = [
        c for c in candidates if not getattr(c, "is_consolidated", False) and c.key != entry.key
    ]

    if not active_candidates:
        return []

    # Check for same-topic entries first (stricter match)
    same_topic_matches = [c for c in active_candidates if is_same_topic(entry, c)]
    if same_topic_matches:
        return same_topic_matches

    # Fall back to similarity-based detection
    similar = find_similar(entry, active_candidates, threshold=threshold)
    return [c for c in active_candidates if any(r.entry_key == c.key for r in similar)]


def merge_entry_relations(
    relation_lists: list[list[dict[str, Any]]],
    target_key: str,
) -> list[RelationEntry]:
    """Merge relations from multiple source entries, deduplicating triples.

    Relations are deduplicated by ``(subject, predicate, object_entity)``
    (case-insensitive).  When duplicates are found the highest confidence
    is kept and ``source_entry_keys`` are merged.

    Args:
        relation_lists: One list of relation dicts per source entry.
        target_key: The consolidated entry key to assign as source.

    Returns:
        Deduplicated list of :class:`RelationEntry` instances.
    """
    seen: dict[tuple[str, str, str], RelationEntry] = {}
    for rels in relation_lists:
        for r in rels:
            triple = (
                r["subject"].lower(),
                r["predicate"].lower(),
                r["object_entity"].lower(),
            )
            if triple in seen:
                existing = seen[triple]
                # Merge source keys and keep highest confidence
                merged_keys = list(dict.fromkeys([*existing.source_entry_keys, target_key]))
                seen[triple] = existing.model_copy(
                    update={
                        "source_entry_keys": merged_keys,
                        "confidence": max(existing.confidence, float(r.get("confidence", 0.8))),
                    }
                )
            else:
                seen[triple] = RelationEntry(
                    subject=r["subject"],
                    predicate=r["predicate"],
                    object_entity=r["object_entity"],
                    source_entry_keys=[target_key],
                    confidence=float(r.get("confidence", 0.8)),
                )
    return list(seen.values())


def detect_consolidation_reason(
    entry: MemoryEntry,
    matches: list[MemoryEntry],
) -> ConsolidationReason:
    """Detect the most appropriate consolidation reason.

    Args:
        entry: The new entry.
        matches: Entries that match for consolidation.

    Returns:
        The consolidation reason.
    """
    if not matches:
        return ConsolidationReason.manual

    # Check for same-topic (tier + tag overlap)
    if any(is_same_topic(entry, m) for m in matches):
        return ConsolidationReason.same_topic

    # Check for supersession (entry key references another entry's key)
    for match in matches:
        if match.key in entry.value.lower() or match.key in entry.key:
            return ConsolidationReason.supersession

    # Default to similarity
    return ConsolidationReason.similarity
