"""Entity/Relationship extraction from memory entries (Epic 65.12).

Rule-based, deterministic extraction of subject-predicate-object triples
from memory entry values. No LLM calls -- uses regex patterns to identify
common relationship verbs and their arguments.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from tapps_brain.models import MemoryEntry

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=UTC).isoformat()


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class RelationEntry(BaseModel):
    """A subject-predicate-object triple extracted from memory content."""

    subject: str = Field(description="The entity performing the action.")
    predicate: str = Field(description="The relationship verb.")
    object_entity: str = Field(
        description="The entity being acted upon (named to avoid Python keyword)."
    )
    source_entry_keys: list[str] = Field(
        default_factory=list,
        description="Memory entry keys this relation was extracted from.",
    )
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Extraction confidence.")
    created_at: str = Field(default_factory=_utc_now_iso, description="ISO-8601 UTC creation time.")

    # -- Constants ----------------------------------------------------------
    MAX_RELATIONS_PER_ENTRY: ClassVar[int] = 5
    MIN_ENTITY_LENGTH: ClassVar[int] = 2


# ---------------------------------------------------------------------------
# Regex patterns for relationship extraction
# ---------------------------------------------------------------------------

# Each pattern captures (subject, predicate_label, object_entity).
# Group 1 = subject (greedy, up to the verb), Group 2 = object (up to
# sentence boundary).  The predicate is the label string.
#
# Subject: 1-6 words before the verb.
# Object: 1-8 words after the verb, stopping at sentence punctuation.
# Words are alpha-start tokens of 1+ word chars (letters, digits, underscore).
# Dots/slashes excluded so sentence boundaries terminate matches naturally.
_WORD = r"[A-Za-z]\w*"
_SUBJ = r"((?:" + _WORD + r"\s+){0,5}" + _WORD + r")"
_OBJ = r"(" + _WORD + r"(?:\s+" + _WORD + r"){0,7})"

_RELATION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_SUBJ + r"\s+manages\s+" + _OBJ, re.I), "manages"),
    (re.compile(_SUBJ + r"\s+owns\s+" + _OBJ, re.I), "owns"),
    (re.compile(_SUBJ + r"\s+handles\s+" + _OBJ, re.I), "handles"),
    (re.compile(_SUBJ + r"\s+uses\s+" + _OBJ, re.I), "uses"),
    (re.compile(_SUBJ + r"\s+depends\s+on\s+" + _OBJ, re.I), "depends on"),
    (re.compile(_SUBJ + r"\s+creates\s+" + _OBJ, re.I), "creates"),
    (re.compile(_SUBJ + r"\s+provides\s+" + _OBJ, re.I), "provides"),
]

# Query patterns that indicate relationship lookups
_QUERY_PATTERNS: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"(?:who|what)\s+handles\s+(.+)", re.I), "handles"),
    (re.compile(r"(?:who|what)\s+manages\s+(.+)", re.I), "manages"),
    (re.compile(r"(?:who|what)\s+owns\s+(.+)", re.I), "owns"),
    (re.compile(r"(?:who|what)\s+uses\s+(.+)", re.I), "uses"),
    (re.compile(r"(?:who|what)\s+creates\s+(.+)", re.I), "creates"),
    (re.compile(r"(?:who|what)\s+provides\s+(.+)", re.I), "provides"),
    (re.compile(r"(?:who|what)\s+depends\s+on\s+(.+)", re.I), "depends on"),
]

# Max hops for relationship traversal
_MAX_HOPS: int = 2


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _clean_entity(raw: str) -> str:
    """Normalize an extracted entity string."""
    # Strip leading/trailing whitespace and common noise
    cleaned = raw.strip().strip(".,;:!?\"'")
    # Collapse internal whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_relations(entry_key: str, value: str) -> list[RelationEntry]:
    """Extract entity-relationship triples from a single memory value.

    Uses rule-based regex patterns to find subject-predicate-object triples.
    Returns at most ``RelationEntry.MAX_RELATIONS_PER_ENTRY`` relations.

    Args:
        entry_key: The memory entry key (for provenance tracking).
        value: The text content to extract relations from.

    Returns:
        List of ``RelationEntry`` objects (max 5).
    """
    if not value or not value.strip():
        return []

    results: list[RelationEntry] = []
    seen_triples: set[tuple[str, str, str]] = set()

    for pattern, predicate in _RELATION_PATTERNS:
        for match in pattern.finditer(value):
            subject = _clean_entity(match.group(1))
            obj = _clean_entity(match.group(2))

            # Skip empty or trivially short entities
            if (
                len(subject) < RelationEntry.MIN_ENTITY_LENGTH
                or len(obj) < RelationEntry.MIN_ENTITY_LENGTH
            ):
                continue

            triple_key = (subject.lower(), predicate.lower(), obj.lower())
            if triple_key in seen_triples:
                continue
            seen_triples.add(triple_key)

            results.append(
                RelationEntry(
                    subject=subject,
                    predicate=predicate,
                    object_entity=obj,
                    source_entry_keys=[entry_key],
                    confidence=0.8,
                )
            )

            if len(results) >= RelationEntry.MAX_RELATIONS_PER_ENTRY:
                break

        if len(results) >= RelationEntry.MAX_RELATIONS_PER_ENTRY:
            break

    logger.debug(
        "relations.extracted",
        entry_key=entry_key,
        count=len(results),
    )
    return results


def extract_relations_from_entries(
    entries: list[MemoryEntry],
) -> list[RelationEntry]:
    """Extract and deduplicate relations from multiple memory entries.

    Calls :func:`extract_relations` for each entry and deduplicates by
    the ``(subject, predicate, object_entity)`` triple, merging
    ``source_entry_keys`` when duplicates are found.

    Args:
        entries: List of memory entries to process.

    Returns:
        Deduplicated list of ``RelationEntry`` objects.
    """
    # Map triple -> RelationEntry for dedup + provenance merge
    seen: dict[tuple[str, str, str], RelationEntry] = {}

    for entry in entries:
        relations = extract_relations(entry.key, entry.value)
        for rel in relations:
            triple_key = (
                rel.subject.lower(),
                rel.predicate.lower(),
                rel.object_entity.lower(),
            )
            if triple_key in seen:
                existing = seen[triple_key]
                # Merge source keys (deduplicated)
                merged_keys = list(
                    dict.fromkeys(existing.source_entry_keys + rel.source_entry_keys)
                )
                seen[triple_key] = existing.model_copy(update={"source_entry_keys": merged_keys})
            else:
                seen[triple_key] = rel

    result = list(seen.values())
    logger.debug(
        "relations.extracted_from_entries",
        entry_count=len(entries),
        relation_count=len(result),
    )
    return result


def expand_via_relations(
    query: str,
    relations: list[RelationEntry],
) -> list[str]:
    """Expand a query using relationship graph traversal.

    For queries like "who handles X" or "what manages Y", performs 1-hop
    and 2-hop traversal of the relation graph to find connected entities.

    Args:
        query: The search query string.
        relations: Available relations to traverse.

    Returns:
        List of additional query terms discovered via relations.
        Returns empty list if query doesn't match a relationship pattern.
    """
    if not query or not relations:
        return []

    # Try to match a relationship query pattern
    target_entity: str | None = None
    target_predicate: str | None = None

    for pattern, predicate in _QUERY_PATTERNS:
        match = pattern.search(query)
        if match:
            target_entity = _clean_entity(match.group(1))
            target_predicate = predicate
            break

    if target_entity is None:
        return []

    target_lower = target_entity.lower()
    expanded: list[str] = []
    seen: set[str] = set()

    # --- Hop 1: direct matches -------------------------------------------------
    hop1_subjects: list[str] = []
    for rel in relations:
        # Match predicate if we identified one, otherwise match any
        if target_predicate and rel.predicate.lower() != target_predicate:
            continue

        # Object matches target -> subject is the answer
        if rel.object_entity.lower() == target_lower:
            subj = rel.subject
            if subj.lower() not in seen:
                seen.add(subj.lower())
                expanded.append(subj)
                hop1_subjects.append(subj)

        # Subject matches target -> object is related
        if rel.subject.lower() == target_lower:
            obj = rel.object_entity
            if obj.lower() not in seen:
                seen.add(obj.lower())
                expanded.append(obj)

    # --- Hop 2: entities related to hop-1 results ------------------------------
    for hop1_entity in hop1_subjects:
        hop1_lower = hop1_entity.lower()
        for rel in relations:
            # Find what hop1 entities are also connected to
            if rel.subject.lower() == hop1_lower:
                obj = rel.object_entity
                if obj.lower() not in seen and obj.lower() != target_lower:
                    seen.add(obj.lower())
                    expanded.append(obj)
            if rel.object_entity.lower() == hop1_lower:
                subj = rel.subject
                if subj.lower() not in seen and subj.lower() != target_lower:
                    seen.add(subj.lower())
                    expanded.append(subj)

    logger.debug(
        "relations.expanded_query",
        query=query,
        target_entity=target_entity,
        target_predicate=target_predicate,
        expanded_count=len(expanded),
    )
    return expanded
