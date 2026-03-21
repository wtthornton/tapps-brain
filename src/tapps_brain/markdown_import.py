"""Markdown import for migrating MEMORY.md files into tapps-brain.

Parses markdown headings into memory keys (slugified) and body text into
values. Heading level determines tier:
  H1/H2 → architectural, H3 → pattern, H4+ → procedural.

Part of EPIC-012 (OpenClaw integration).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

from tapps_brain.models import MAX_KEY_LENGTH, MAX_VALUE_LENGTH, MemorySource, MemoryTier

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

# Matches markdown headings: group(1)=hashes, group(2)=text
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# Tier boundary heading levels
_H_ARCHITECTURAL_MAX = 2  # H1/H2 → architectural
_H_PATTERN = 3  # H3 → pattern; H4+ → procedural

# Characters allowed in slugified keys
_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9._-]+")
_MULTI_SEP_RE = re.compile(r"[-_.]{2,}")


def _slugify(text: str) -> str:
    """Convert heading text to a valid memory key slug.

    Lowercase, replace non-alphanumeric with hyphens, collapse runs,
    strip leading/trailing separators, truncate to MAX_KEY_LENGTH.
    """
    slug = text.lower().strip()
    slug = _SLUG_CLEAN_RE.sub("-", slug)
    slug = _MULTI_SEP_RE.sub("-", slug)
    slug = slug.strip("-._")
    # Ensure starts with alphanumeric
    slug = slug.lstrip("-._")
    if not slug or not slug[0].isalnum():
        slug = "m-" + slug
    return slug[:MAX_KEY_LENGTH]


def _tier_from_level(level: int) -> MemoryTier:
    """Map heading level (1-6) to a memory tier."""
    if level <= _H_ARCHITECTURAL_MAX:
        return MemoryTier.architectural
    if level == _H_PATTERN:
        return MemoryTier.pattern
    return MemoryTier.procedural


def import_memory_md(path: Path, store: MemoryStore) -> int:
    """Parse a MEMORY.md file and import entries into the store.

    Each heading becomes a key (slugified). The body text under that heading
    becomes the value. Entries whose keys already exist are skipped.

    Args:
        path: Path to the markdown file.
        store: MemoryStore instance to import into.

    Returns:
        Number of new entries imported.
    """
    if not path.is_file():
        logger.warning("markdown_import.file_not_found", path=str(path))
        return 0

    text = path.read_text(encoding="utf-8")
    return _parse_and_import(text, store)


def _parse_and_import(text: str, store: MemoryStore) -> int:
    """Parse markdown text into sections and import them.

    Returns the number of new entries imported.
    """
    sections = _parse_sections(text)
    imported = 0

    for key, value, tier in sections:
        # Skip if key already exists (deduplication)
        if store.get(key) is not None:
            logger.debug("markdown_import.skip_duplicate", key=key)
            continue

        # Truncate value if too long
        if len(value) > MAX_VALUE_LENGTH:
            value = value[:MAX_VALUE_LENGTH]

        store.save(
            key=key,
            value=value,
            tier=tier.value,
            source=MemorySource.system.value,
        )
        imported += 1
        logger.debug("markdown_import.imported", key=key, tier=tier.value)

    return imported


def _parse_sections(text: str) -> list[tuple[str, str, MemoryTier]]:
    """Split markdown into (key, value, tier) tuples from headings + body."""
    sections: list[tuple[str, str, MemoryTier]] = []
    current_key: str | None = None
    current_tier: MemoryTier = MemoryTier.pattern
    body_lines: list[str] = []

    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            # Flush previous section
            if current_key is not None:
                body = "\n".join(body_lines).strip()
                if body:
                    sections.append((current_key, body, current_tier))

            level = len(match.group(1))
            heading_text = match.group(2)
            current_key = _slugify(heading_text)
            current_tier = _tier_from_level(level)
            body_lines = []
        else:
            body_lines.append(line)

    # Flush last section
    if current_key is not None:
        body = "\n".join(body_lines).strip()
        if body:
            sections.append((current_key, body, current_tier))

    return sections
