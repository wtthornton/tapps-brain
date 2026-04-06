"""Rule-based extraction of durable facts from session context (Epic 65.5).

Extracts decision-like statements from context for auto-capture on session stop.
Deterministic, no LLM calls.
"""

from __future__ import annotations

import re
import unicodedata

# Minimum chars for a chunk to be considered (skip tiny fragments)
_MIN_CHUNK_CHARS = 20


# Decision/architecture pattern phrases (case-insensitive).
# Keep specific phrases before broad ones (first match wins per chunk).
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
    # Common in agent replies and dev notes (auto-capture / OpenClaw ingest)
    (re.compile(r"\bnote\s*:", re.I), "pattern"),
    (re.compile(r"\bsummary\s*:", re.I), "pattern"),
    (re.compile(r"\btl;?dr\b", re.I), "context"),
    (re.compile(r"\bremember\s+that\b", re.I), "context"),
    (re.compile(r"\bimplementation\s+note\b", re.I), "pattern"),
    (re.compile(r"\broot\s+cause\b", re.I), "pattern"),
    (re.compile(r"\b(?:chosen|final)\s+approach\b", re.I), "architectural"),
    (re.compile(r"\bwe(?:'re|\s+are)\s+using\b", re.I), "pattern"),
    (re.compile(r"\bwe\s+use\b", re.I), "pattern"),
]

# Personal-assistant extraction patterns (Issue #67).
# Tier names match personal-assistant.yaml layer names; normalized at save time.
_PA_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Identity
    (re.compile(r"\bmy\s+name\s+is\b", re.I), "identity"),
    (re.compile(r"\bi(?:'m|\s+am)\s+(?:a|an)\b", re.I), "identity"),
    (re.compile(r"\bi\s+live\s+in\b", re.I), "identity"),
    (re.compile(r"\bi\s+work\s+(?:at|for)\b", re.I), "identity"),
    (re.compile(r"\bmy\s+(?:wife|husband|partner|spouse)\b", re.I), "identity"),
    (re.compile(r"\bmy\s+(?:son|daughter|child|children|kids?)\b", re.I), "identity"),
    (re.compile(r"\bmy\s+(?:mom|dad|mother|father|parents?)\b", re.I), "identity"),
    (re.compile(r"\bi\s+(?:am\s+)?allergic\s+to\b", re.I), "identity"),
    (re.compile(r"\bmy\s+allerg(?:y|ies)\b", re.I), "identity"),
    # Long-term preferences
    (re.compile(r"\bi\s+prefer\b", re.I), "long-term"),
    (re.compile(r"\bi\s+(?:like|love|enjoy)\b", re.I), "long-term"),
    (re.compile(r"\bi\s+(?:dislike|hate|don't\s+like|do\s+not\s+like)\b", re.I), "long-term"),
    (re.compile(r"\bmy\s+(?:favorite|favourite)\b", re.I), "long-term"),
    (re.compile(r"\bi\s+always\b", re.I), "long-term"),
    (re.compile(r"\bi\s+never\b", re.I), "long-term"),
    (re.compile(r"\bmy\s+(?:friend|colleague|boss|manager)\b", re.I), "long-term"),
    (re.compile(r"\bmy\s+(?:doctor|dentist|physician|therapist)\b", re.I), "long-term"),
    (re.compile(r"\bmy\s+(?:medication|medicine|prescription)\b", re.I), "long-term"),
    (re.compile(r"\bi\s+can't\s+eat\b", re.I), "long-term"),
    (re.compile(r"\bi\s+don't\s+(?:eat|drink)\b", re.I), "long-term"),
    # Procedural / routines
    (re.compile(r"\bevery\s+(?:morning|evening|night|day|week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I), "procedural"),
    (re.compile(r"\bon\s+(?:mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?|weekends?)\b", re.I), "procedural"),
    (re.compile(r"\bmy\s+(?:routine|schedule|habit|workout)\b", re.I), "procedural"),
    (re.compile(r"\bi\s+usually\b", re.I), "procedural"),
    # Short-term / context
    (re.compile(r"\btoday\s+i\b", re.I), "short-term"),
    (re.compile(r"\bright\s+now\b", re.I), "short-term"),
    (re.compile(r"\bthis\s+week\b", re.I), "short-term"),
    (re.compile(r"\bremind\s+me\b", re.I), "short-term"),
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
    return text[:max_len] if len(text) > max_len else text


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
    profile: str | None = None,
) -> list[dict[str, str]]:
    """Extract durable fact candidates from context using rule-based patterns.

    Looks for decision-like phrases: "we decided", "note:", "summary:", "we use",
    "remember that", "root cause", "final approach", etc. Returns candidates as
    [{key, value, tier}]. Tier is inferred from pattern (architectural, pattern,
    context).

    When *profile* is ``"personal-assistant"``, additional patterns are used
    for preferences, relationships, health, routines, and short-term context.

    Args:
        context: Raw session/transcript text to scan.
        capture_prompt: Optional guidance (Epic 65.3); currently unused but
            reserved for future filtering.
        max_facts: Maximum number of facts to return (default 10).
        max_value_chars: Maximum characters per value (default 4096).
        profile: Profile name (e.g. ``"personal-assistant"``). When set,
            profile-specific extraction patterns are activated.

    Returns:
        List of dicts with keys: key, value, tier.
        Deterministic; no LLM calls.
    """
    if not context or not context.strip():
        return []

    # Select pattern set based on active profile.
    if profile == "personal-assistant":
        patterns = _PA_PATTERNS + _DECISION_PATTERNS
    else:
        patterns = _DECISION_PATTERNS

    facts: list[dict[str, str]] = []
    seen_values: set[str] = set()

    # Split into candidate sentences/paragraphs.
    # _SENTENCE_BOUNDARY matches both sentence-ending whitespace and newlines
    # (including double-newlines), so a single split covers all boundaries.
    chunks = _SENTENCE_BOUNDARY.split(context.strip())
    for chunk in chunks:
        text = chunk.strip()
        if len(text) < _MIN_CHUNK_CHARS:
            continue
        for pattern, tier in patterns:
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

    return facts[:max_facts]
