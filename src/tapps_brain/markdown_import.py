"""Markdown import for migrating MEMORY.md files into tapps-brain.

Parses markdown headings into memory keys (slugified) and body text into
values. Heading level determines tier:
  H1/H2 → architectural, H3 → pattern, H4+ → procedural.

Daily notes (``memory/YYYY-MM-DD.md``) are imported as context-tier entries
with date extracted from the filename.

Also supports round-tripping Obsidian frontmatter exports from
``export_to_markdown`` (TAP-5032).

Part of EPIC-012 (OpenClaw integration).
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.models import MAX_KEY_LENGTH, MAX_VALUE_LENGTH, MemorySource, MemoryTier
from tapps_brain.rate_limiter import batch_exempt_scope

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

_FRONTMATTER_BLOCK_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL | re.MULTILINE)

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


def looks_like_frontmatter_export(text: str) -> bool:
    """Return True when text looks like ``export_to_markdown`` output."""
    if "key:" not in text:
        return False
    return bool(_FRONTMATTER_BLOCK_RE.search(text))


def _parse_frontmatter_yamlish(block: str) -> dict[str, Any]:
    """Parse a minimal YAML-ish frontmatter block (no PyYAML dependency)."""
    meta: dict[str, Any] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, raw_val = line.partition(":")
        key = key.strip()
        raw_val = raw_val.strip()
        if not key:
            continue
        if raw_val.startswith("[") and raw_val.endswith("]"):
            try:
                meta[key] = json_loads_list(raw_val)
            except Exception:
                meta[key] = raw_val
            continue
        if (raw_val.startswith("'") and raw_val.endswith("'")) or (
            raw_val.startswith('"') and raw_val.endswith('"')
        ):
            try:
                meta[key] = ast.literal_eval(raw_val)
            except (ValueError, SyntaxError):
                meta[key] = raw_val[1:-1]
            continue
        try:
            meta[key] = float(raw_val) if "." in raw_val else int(raw_val)
        except ValueError:
            meta[key] = raw_val
    return meta


def json_loads_list(raw: str) -> list[Any]:
    """Parse a JSON list literal from frontmatter."""
    import json

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        msg = "expected list"
        raise TypeError(msg)
    return parsed


def parse_frontmatter_entries(text: str) -> list[dict[str, Any]]:
    """Extract memory dicts from frontmatter + body blocks."""
    entries: list[dict[str, Any]] = []
    # Split on frontmatter boundaries while keeping structure
    parts = re.split(r"(?m)^---\s*$", text)
    # parts alternate: preamble, fm, body, fm, body, ...
    idx = 1
    while idx + 1 < len(parts):
        fm_block = parts[idx].strip("\n")
        body_block = parts[idx + 1]
        meta = _parse_frontmatter_yamlish(fm_block)
        key = meta.get("key")
        if not isinstance(key, str) or not key.strip():
            # Fallback: first ## heading in body
            for line in body_block.splitlines():
                m = _HEADING_RE.match(line)
                if m and len(m.group(1)) >= _H_ARCHITECTURAL_MAX:
                    key = m.group(2).strip()
                    break
        if not isinstance(key, str) or not key.strip():
            idx += 2
            continue
        # Strip leading ## heading that duplicates the key
        body_lines = body_block.splitlines()
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        if body_lines and _HEADING_RE.match(body_lines[0]):
            body_lines = body_lines[1:]
            while body_lines and not body_lines[0].strip():
                body_lines.pop(0)
        value = "\n".join(body_lines).strip()
        if not value:
            idx += 2
            continue
        tier = meta.get("tier", "pattern")
        source = meta.get("source", MemorySource.system.value)
        confidence = meta.get("confidence", -1.0)
        tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
        entries.append(
            {
                "key": key.strip()[:MAX_KEY_LENGTH],
                "value": value[:MAX_VALUE_LENGTH],
                "tier": str(tier),
                "source": str(source),
                "confidence": float(confidence) if confidence is not None else -1.0,
                "tags": [str(t) for t in tags],
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"),
            }
        )
        idx += 2
    return entries


def import_frontmatter_markdown(
    path: Path,
    store: MemoryStore,
    *,
    overwrite: bool = False,
) -> int:
    """Import Markdown produced by ``export_to_markdown`` (TAP-5032).

    Reads ``key``, ``tier``, ``confidence``, and ``source`` from YAML
    frontmatter. Existing keys are skipped unless ``overwrite=True``.
    """
    if not path.is_file():
        logger.warning("markdown_import.file_not_found", path=str(path))
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning("markdown_import.encoding_error", path=str(path))
        return 0
    return import_frontmatter_markdown_text(text, store, overwrite=overwrite)


def import_frontmatter_markdown_text(
    text: str,
    store: MemoryStore,
    *,
    overwrite: bool = False,
) -> int:
    """Import frontmatter Markdown from an in-memory string."""
    entries = parse_frontmatter_entries(text)
    imported = 0
    for item in entries:
        key = item["key"]
        if store.get(key) is not None and not overwrite:
            logger.debug("markdown_import.skip_duplicate", key=key)
            continue
        kwargs: dict[str, Any] = {
            "key": key,
            "value": item["value"],
            "tier": item.get("tier", "pattern"),
            "source": item.get("source", MemorySource.system.value),
            "tags": item.get("tags"),
            "confidence": item.get("confidence", -1.0),
        }
        with batch_exempt_scope("import_markdown"):
            saved = store.save(**kwargs)
        if isinstance(saved, dict) and saved.get("error"):
            logger.warning(
                "markdown_import.save_failed",
                key=key,
                error=saved.get("error"),
                message=saved.get("message"),
            )
            continue
        imported += 1
    return imported


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
        body = value[:MAX_VALUE_LENGTH]

        with batch_exempt_scope("import_markdown"):
            saved = store.save(
                key=key,
                value=body,
                tier=tier.value,
                source=MemorySource.system.value,
            )
        if isinstance(saved, dict) and saved.get("error"):
            logger.warning(
                "markdown_import.save_failed",
                key=key,
                error=saved.get("error"),
                message=saved.get("message"),
            )
            continue
        imported += 1
        logger.debug("markdown_import.imported", key=key, tier=tier.value)

    return imported


def _parse_sections(text: str) -> list[tuple[str, str, MemoryTier]]:
    """Split markdown into (key, value, tier) tuples from headings + body.

    Within a single document, colliding slugified headings are disambiguated
    with the same deterministic suffix scheme as :mod:`tapps_brain.markdown_sync`
    (TAP-718) so the second section is not silently dropped.
    """
    from tapps_brain.markdown_sync import _resolve_slug_collision

    sections: list[tuple[str, str, MemoryTier]] = []
    current_key: str | None = None
    current_tier: MemoryTier = MemoryTier.pattern
    body_lines: list[str] = []
    seen_slugs: dict[str, str] = {}

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
            slug = _slugify(heading_text)
            current_key = _resolve_slug_collision(slug, heading_text, seen_slugs)
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

    with batch_exempt_scope("import_markdown"):
        saved = store.save(
            key=key,
            value=value,
            tier=MemoryTier.context.value,
            source=MemorySource.system.value,
        )
    if isinstance(saved, dict) and saved.get("error"):
        logger.warning(
            "markdown_import.daily_save_failed",
            key=key,
            error=saved.get("error"),
            message=saved.get("message"),
        )
        return False
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
            body = value[:MAX_VALUE_LENGTH]
            with batch_exempt_scope("import_markdown"):
                store.save(
                    key=key,
                    value=body,
                    tier=tier.value,
                    source=MemorySource.system.value,
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
