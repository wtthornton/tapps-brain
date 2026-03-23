"""Markdown import for migrating MEMORY.md files into tapps-brain.

Parses markdown headings into memory keys (slugified) and body text into
values. Heading level determines tier:
  H1/H2 → architectural, H3 → pattern, H4+ → procedural.

Daily notes (``memory/YYYY-MM-DD.md``) are imported as context-tier entries
with date extracted from the filename.

Part of EPIC-012 (OpenClaw integration).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

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

# Daily note filename pattern: YYYY-MM-DD.md
_DAILY_NOTE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

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
    # After stripping separators, if empty add a safe prefix
    if not slug:
        slug = "m-"
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

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning("markdown_import.encoding_error", path=str(path))
        return 0
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
            batch_context="import_markdown",
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


def _import_daily_note(path: Path, store: MemoryStore) -> bool:
    """Import a single daily note file as a context-tier entry.

    The key is derived from the date in the filename (``daily-YYYY-MM-DD``).
    If the key already exists in the store, the note is skipped.

    Returns:
        ``True`` if the note was imported, ``False`` if skipped or empty.
    """
    match = _DAILY_NOTE_RE.match(path.name)
    if not match:
        return False

    date_str = match.group(1)
    key = f"daily-{date_str}"

    # Deduplicate
    if store.get(key) is not None:
        logger.debug("markdown_import.skip_daily_duplicate", key=key)
        return False

    try:
        text = path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        logger.warning("markdown_import.encoding_error", path=str(path))
        return False
    if not text:
        logger.debug("markdown_import.skip_empty_daily", path=str(path))
        return False

    # Truncate if needed
    value = text[:MAX_VALUE_LENGTH]

    store.save(
        key=key,
        value=value,
        tier=MemoryTier.context.value,
        source=MemorySource.system.value,
        batch_context="import_markdown",
    )
    logger.debug("markdown_import.imported_daily", key=key, date=date_str)
    return True


def import_openclaw_workspace(
    workspace_dir: Path,
    store: MemoryStore,
) -> dict[str, Any]:
    """Import an OpenClaw workspace's markdown memories into the store.

    Imports both the top-level ``MEMORY.md`` and any daily notes found in
    ``memory/YYYY-MM-DD.md``.

    Args:
        workspace_dir: Root directory of the OpenClaw workspace.
        store: MemoryStore instance to import into.

    Returns:
        Dict with keys ``memory_md`` (int), ``daily_notes`` (int),
        ``skipped`` (int) representing counts.
    """
    memory_md_count = 0
    daily_notes_count = 0
    skipped = 0

    # 1) Import MEMORY.md from workspace root
    memory_md_path = workspace_dir / "MEMORY.md"
    if memory_md_path.is_file():
        try:
            text = memory_md_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("markdown_import.encoding_error", path=str(memory_md_path))
            text = ""
        sections = _parse_sections(text)
        for key, value, tier in sections:
            if store.get(key) is not None:
                logger.debug("markdown_import.skip_duplicate", key=key)
                skipped += 1
                continue
            if len(value) > MAX_VALUE_LENGTH:
                value = value[:MAX_VALUE_LENGTH]
            store.save(
                key=key,
                value=value,
                tier=tier.value,
                source=MemorySource.system.value,
                batch_context="import_markdown",
            )
            memory_md_count += 1
    else:
        logger.info("markdown_import.no_memory_md", dir=str(workspace_dir))

    # 2) Import daily notes from memory/ subdirectory
    memory_dir = workspace_dir / "memory"
    if memory_dir.is_dir():
        for note_path in sorted(memory_dir.iterdir()):
            if not note_path.is_file():
                continue
            match = _DAILY_NOTE_RE.match(note_path.name)
            if not match:
                continue
            if _import_daily_note(note_path, store):
                daily_notes_count += 1
            else:
                skipped += 1
    else:
        logger.info("markdown_import.no_memory_dir", dir=str(workspace_dir))

    logger.info(
        "markdown_import.workspace_complete",
        workspace=str(workspace_dir),
        memory_md=memory_md_count,
        daily_notes=daily_notes_count,
        skipped=skipped,
    )

    return {
        "memory_md": memory_md_count,
        "daily_notes": daily_notes_count,
        "skipped": skipped,
    }
