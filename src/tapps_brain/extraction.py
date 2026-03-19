"""Rule-based extraction of durable facts from session context (Epic 65.5).

Extracts decision-like statements from context for auto-capture on session stop.
Deterministic, no LLM calls.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Minimum chars for a chunk to be considered (skip tiny fragments)
_MIN_CHUNK_CHARS = 20


# Decision/architecture pattern phrases (case-insensitive)
_DECISION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bwe\s+decided\b", re.I), "architectural"),
    (re.compile(r"\bkey\s+decision\b", re.I), "architectural"),
    (re.compile(r"\barchitecture\s+choice\b", re.I), "architectural"),
    (re.compile(r"\bwe\s+agreed\b", re.I), "pattern"),
    (re.compile(r"\bimportant\s*:", re.I), "pattern"),
    (re.compile(r"\bdecision\s*:", re.I), "architectural"),
    (re.compile(r"\bchose\s+to\s+", re.I), "pattern"),
    (re.compile(r"\bwe\s+chose\b", re.I), "pattern"),
    (re.compile(r"\bgoing\s+forward\b", re.I), "context"),
    (re.compile(r"\bconvention\s*:", re.I), "pattern"),
    (re.compile(r"\bpattern\s*:", re.I), "pattern"),
]

# Sentence boundary for splitting
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")


def _slugify(text: str, max_len: int = 64) -> str:
    """Convert text to a valid memory key slug (lowercase, alphanumeric, dots, hyphens)."""
    # Normalize unicode
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    # Replace non-slug chars with hyphens
    text = re.sub(r"[^a-z0-9._-]", "-", text.lower())
    # Collapse hyphens
    text = re.sub(r"-+", "-", text).strip("-")
    if not text:
        text = "fact"
    # Ensure starts with alphanumeric
    if text and text[0] not in "abcdefghijklmnopqrstuvwxyz0123456789":
        text = "x-" + text
    return text[:max_len] if len(text) > max_len else text or "fact"


def _truncate(value: str, max_chars: int) -> str:
    """Truncate value to max_chars, preserving word boundary."""
    value = value.strip()
    if len(value) <= max_chars:
        return value
    cut = value[: max_chars - 3].rsplit(maxsplit=1)
    return (cut[0] if cut else value[: max_chars - 3]) + "..."


def extract_durable_facts(
    context: str,
    capture_prompt: str = "",
    *,
    max_facts: int = 10,
    max_value_chars: int = 4096,
) -> list[dict[str, Any]]:
    """Extract durable fact candidates from context using rule-based patterns.

    Looks for decision-like phrases: "we decided", "key decision", "architecture
    choice", "we agreed", "important:", etc. Returns candidates as
    [{key, value, tier}]. Tier is inferred from pattern (architectural, pattern,
    context).

    Args:
        context: Raw session/transcript text to scan.
        capture_prompt: Optional guidance (Epic 65.3); currently unused but
            reserved for future filtering.
        max_facts: Maximum number of facts to return (default 10).
        max_value_chars: Maximum characters per value (default 4096).

    Returns:
        List of dicts with keys: key, value, tier.
        Deterministic; no LLM calls.
    """
    if not context or not context.strip():
        return []

    facts: list[dict[str, Any]] = []
    seen_values: set[str] = set()

    # Split into candidate sentences/paragraphs
    chunks = _SENTENCE_BOUNDARY.split(context.strip())
    # Also try double-newline as paragraph boundary
    for c in chunks:
        for para in c.split("\n\n"):
            text = para.strip()
            if len(text) < _MIN_CHUNK_CHARS:
                continue
            for pattern, tier in _DECISION_PATTERNS:
                if pattern.search(text):
                    value = _truncate(text, max_value_chars)
                    if value in seen_values:
                        continue
                    seen_values.add(value)
                    key = _slugify(value[:80])
                    # Ensure unique key
                    base_key = key
                    idx = 1
                    existing_keys = {f["key"] for f in facts}
                    while key in existing_keys:
                        key = f"{base_key}.{idx}"
                        idx += 1
                    facts.append({"key": key, "value": value, "tier": tier})
                    break  # One match per chunk
            if len(facts) >= max_facts:
                break
        if len(facts) >= max_facts:
            break

    return facts[:max_facts]
