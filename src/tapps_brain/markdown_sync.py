"""Bidirectional MEMORY.md sync for OpenClaw workspace integration.

Exports tapps-brain memory entries to ``MEMORY.md`` organised by tier, and
imports entries from ``MEMORY.md`` / ``memory/*.md`` back into the store.

Conflict resolution: **tapps-brain always wins** — existing store entries are
never overwritten by file contents. Only keys that are absent from the store
are created during :func:`sync_from_markdown`.

Sync state is persisted in ``{workspace_dir}/.tapps-brain/sync_state.json``
so callers can detect stale syncs and schedule refreshes.

Part of EPIC-026 (OpenClaw memory replacement, story-026.4).
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.models import MAX_KEY_LENGTH, MAX_VALUE_LENGTH, MemorySource, MemoryTier

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SYNC_STATE_DIR = ".tapps-brain"
_SYNC_STATE_FILENAME = "sync_state.json"
_SYNC_STATE_VERSION = 1

# Schema version embedded in MEMORY.md YAML front matter.
# Increment when the exported format changes in a backward-incompatible way.
MEMORY_MD_SCHEMA_VERSION = 1

# Heading prefix used for each tier when writing MEMORY.md
_TIER_HEADING: dict[str, str] = {
    MemoryTier.architectural: "##",
    MemoryTier.pattern: "###",
    MemoryTier.procedural: "####",
    MemoryTier.context: "#####",
}

# Export order — most-stable entries first
_TIER_ORDER: list[str] = [
    MemoryTier.architectural,
    MemoryTier.pattern,
    MemoryTier.procedural,
    MemoryTier.context,
]

# Parsing helpers (mirrors markdown_import.py)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9._-]+")
_MULTI_SEP_RE = re.compile(r"[-_.]{2,}")

# Daily note filename: YYYY-MM-DD.md
_DAILY_NOTE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert heading text to a valid memory key slug.

    Lowercases, replaces non-alphanumeric (except ``._-``) with hyphens,
    collapses runs of separators, strips leading/trailing separators, and
    truncates to ``MAX_KEY_LENGTH``.
    """
    slug = text.lower().strip()
    slug = _SLUG_CLEAN_RE.sub("-", slug)
    slug = _MULTI_SEP_RE.sub("-", slug)
    slug = slug.strip("-._")
    if not slug:
        slug = "m-"
    return slug[:MAX_KEY_LENGTH]


def _tier_from_heading_level(level: int) -> str:
    """Map a heading level (1-6) to a tier string.

    H1 is the document title and is handled separately (skipped).
    H2 -> architectural, H3 -> pattern, H4 -> procedural, H5+ -> context.
    """
    arch_level = 2
    pattern_level = 3
    procedural_level = 4
    if level <= arch_level:
        return MemoryTier.architectural
    if level == pattern_level:
        return MemoryTier.pattern
    if level == procedural_level:
        return MemoryTier.procedural
    return MemoryTier.context  # H5 and H6


def _load_sync_state(workspace_dir: Path) -> dict[str, Any]:
    """Load the sync state dict from ``.tapps-brain/sync_state.json``.

    Returns an empty state dict (with version key) on any error.
    """
    state_path = workspace_dir / _SYNC_STATE_DIR / _SYNC_STATE_FILENAME
    if not state_path.is_file():
        return {"version": _SYNC_STATE_VERSION}
    try:
        raw = state_path.read_text(encoding="utf-8")
        return dict(json.loads(raw))
    except (json.JSONDecodeError, OSError, ValueError):
        logger.warning("markdown_sync.state_load_error", path=str(state_path))
        return {"version": _SYNC_STATE_VERSION}


def _save_sync_state(workspace_dir: Path, state: dict[str, Any]) -> None:
    """Persist the sync state to ``.tapps-brain/sync_state.json``.

    Creates the directory if it does not exist.  Errors are logged but not
    re-raised so that a state-write failure never aborts a sync.
    """
    state_dir = workspace_dir / _SYNC_STATE_DIR
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / _SYNC_STATE_FILENAME
        tmp_state_path = state_path.with_name(state_path.name + ".tmp")
        tmp_state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp_state_path, state_path)  # atomic on POSIX and Windows (Python ≥ 3.3)
    except OSError:
        logger.warning("markdown_sync.state_save_error", workspace=str(workspace_dir))


def _parse_memory_md_sections(text: str) -> list[tuple[str, str, str]]:
    """Parse MEMORY.md text into ``(key, value, tier)`` tuples.

    Rules:
    - An optional YAML front matter block (lines between leading ``---``
      delimiters) is skipped entirely — it carries document-level metadata
      such as ``schema_version`` written by :func:`sync_to_markdown`.
    - Lines starting with ``<!--`` are HTML comment markers — excluded from
      values (they carry sync metadata written by :func:`sync_to_markdown`).
    - H1 headings are treated as the document title and **skipped**.
    - H2 → architectural, H3 → pattern, H4 → procedural, H5/H6 → context.
    - Heading text is slugified to produce the key.
    - Each section body (stripped) becomes the value; empty bodies are dropped.
    """
    all_lines = text.splitlines()

    # Strip leading YAML front matter (--- ... ---) if present.
    start_index = 0
    if all_lines and all_lines[0].strip() == "---":
        for i in range(1, len(all_lines)):
            if all_lines[i].strip() == "---":
                start_index = i + 1
                break

    sections: list[tuple[str, str, str]] = []
    current_key: str | None = None
    current_tier: str = MemoryTier.pattern
    body_lines: list[str] = []

    for line in all_lines[start_index:]:
        # Exclude HTML comment lines (sync metadata)
        if line.strip().startswith("<!--"):
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            # Flush the in-progress section
            if current_key is not None:
                body = "\n".join(body_lines).strip()
                if body:
                    sections.append((current_key, body, current_tier))

            level = len(heading_match.group(1))
            heading_text = heading_match.group(2)

            # H1 is the document title — skip it
            if level == 1:
                current_key = None
                body_lines = []
                continue

            current_key = _slugify(heading_text)
            current_tier = _tier_from_heading_level(level)
            body_lines = []
        else:
            body_lines.append(line)

    # Flush the final section
    if current_key is not None:
        body = "\n".join(body_lines).strip()
        if body:
            sections.append((current_key, body, current_tier))

    return sections


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sync_to_markdown(store: MemoryStore, workspace_dir: Path) -> dict[str, Any]:
    """Export all active store entries to ``MEMORY.md``, organised by tier.

    Entries are grouped in the order: architectural → pattern → procedural →
    context.  Within each tier they are sorted by key.  The heading prefix
    reflects the tier so that a subsequent :func:`sync_from_markdown` call
    will reconstruct the same tier for each entry.

    The sync timestamp is written to
    ``{workspace_dir}/.tapps-brain/sync_state.json``.

    Args:
        store: :class:`~tapps_brain.store.MemoryStore` to read entries from.
        workspace_dir: Root directory of the OpenClaw workspace.

    Returns:
        Dict with keys:

        - ``exported`` (int) — number of entries written.
        - ``path`` (str) — absolute path of the written ``MEMORY.md``.
    """
    entries = store.list_all(include_superseded=False)

    # Group by tier (normalise to str via StrEnum behaviour)
    grouped: dict[str, list[Any]] = {t: [] for t in _TIER_ORDER}
    for entry in entries:
        tier_str = str(entry.tier)
        if tier_str in grouped:
            grouped[tier_str].append(entry)
        else:
            # Unknown tier — fall back to context bucket
            grouped[MemoryTier.context].append(entry)

    # Build MEMORY.md content — YAML front matter first, then the document body.
    lines: list[str] = [
        "---",
        f"schema_version: {MEMORY_MD_SCHEMA_VERSION}",
        "---",
        "",
        "# Memory",
        "",
        "<!-- Generated by tapps-brain sync. Edit freely; tapps-brain wins on import conflict. -->",
        "",
    ]
    exported = 0

    for tier_str in _TIER_ORDER:
        tier_entries = sorted(grouped[tier_str], key=lambda e: e.key)
        if not tier_entries:
            continue

        heading_prefix = _TIER_HEADING[tier_str]
        for entry in tier_entries:
            lines.append(f"{heading_prefix} {entry.key}")
            lines.append("")
            lines.append(entry.value.strip())
            lines.append("")
            exported += 1

    memory_md_path = workspace_dir / "MEMORY.md"
    tmp_md_path = memory_md_path.with_name(memory_md_path.name + ".tmp")
    try:
        tmp_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp_md_path, memory_md_path)  # atomic on POSIX and Windows (Python ≥ 3.3)
    except BaseException:
        # Clean up the partial tmp file so stale artefacts do not accumulate.
        try:
            tmp_md_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    state = _load_sync_state(workspace_dir)
    state["last_sync_to"] = datetime.now(tz=UTC).isoformat()
    _save_sync_state(workspace_dir, state)

    logger.info(
        "markdown_sync.export_complete",
        workspace=str(workspace_dir),
        exported=exported,
    )
    return {"exported": exported, "path": str(memory_md_path)}


def sync_from_markdown(store: MemoryStore, workspace_dir: Path) -> dict[str, Any]:
    """Import entries from ``MEMORY.md`` and ``memory/*.md`` into the store.

    For each entry found in the markdown files:

    - **Key absent from store**: the entry is saved (source = ``system``).
    - **Key already in store (tapps-brain wins)**: the entry is skipped.

    Daily notes (``memory/YYYY-MM-DD.md``) are imported as ``context``-tier
    entries with key ``daily-YYYY-MM-DD``.

    The sync timestamp is written to
    ``{workspace_dir}/.tapps-brain/sync_state.json``.

    Args:
        store: :class:`~tapps_brain.store.MemoryStore` to import into.
        workspace_dir: Root directory of the OpenClaw workspace.

    Returns:
        Dict with keys:

        - ``imported`` (int) — total new entries saved.
        - ``skipped`` (int) — total entries that already existed.
        - ``memory_md`` (int) — new entries from ``MEMORY.md``.
        - ``daily_notes`` (int) — new entries from daily note files.
    """
    md_imported, md_skipped = _import_memory_md_sync(workspace_dir, store)
    daily_imported, daily_skipped = _import_daily_notes_sync(workspace_dir, store)

    total_imported = md_imported + daily_imported
    total_skipped = md_skipped + daily_skipped

    state = _load_sync_state(workspace_dir)
    state["last_sync_from"] = datetime.now(tz=UTC).isoformat()
    _save_sync_state(workspace_dir, state)

    logger.info(
        "markdown_sync.import_complete",
        workspace=str(workspace_dir),
        imported=total_imported,
        skipped=total_skipped,
    )
    return {
        "imported": total_imported,
        "skipped": total_skipped,
        "memory_md": md_imported,
        "daily_notes": daily_imported,
    }


def get_sync_state(workspace_dir: Path) -> dict[str, Any]:
    """Return the current sync state for a workspace.

    Reads ``{workspace_dir}/.tapps-brain/sync_state.json``.

    Args:
        workspace_dir: Root directory of the OpenClaw workspace.

    Returns:
        Dict with optional keys ``last_sync_to`` (ISO-8601 str),
        ``last_sync_from`` (ISO-8601 str), and ``version`` (int).
        Returns ``{"version": 1}`` when no sync has occurred yet.
    """
    return _load_sync_state(workspace_dir)


# ---------------------------------------------------------------------------
# Private import helpers
# ---------------------------------------------------------------------------


def _import_memory_md_sync(
    workspace_dir: Path,
    store: MemoryStore,
) -> tuple[int, int]:
    """Parse ``MEMORY.md`` and save entries not already in the store.

    Returns:
        ``(imported, skipped)`` counts.
    """
    memory_md_path = workspace_dir / "MEMORY.md"
    if not memory_md_path.is_file():
        logger.info("markdown_sync.no_memory_md", workspace=str(workspace_dir))
        return 0, 0

    try:
        text = memory_md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning("markdown_sync.encoding_error", path=str(memory_md_path))
        return 0, 0

    sections = _parse_memory_md_sections(text)
    imported = 0
    skipped = 0

    for key, value, tier_str in sections:
        if store.get(key) is not None:
            logger.debug("markdown_sync.skip_existing", key=key)
            skipped += 1
            continue

        if len(value) > MAX_VALUE_LENGTH:
            value = value[:MAX_VALUE_LENGTH]

        store.save(
            key=key,
            value=value,
            tier=tier_str,
            source=MemorySource.system.value,
        )
        imported += 1
        logger.debug("markdown_sync.imported", key=key, tier=tier_str)

    return imported, skipped


def _import_daily_notes_sync(
    workspace_dir: Path,
    store: MemoryStore,
) -> tuple[int, int]:
    """Import daily notes from ``memory/YYYY-MM-DD.md`` (tapps-brain wins).

    Each note is imported as a ``context``-tier entry with key
    ``daily-YYYY-MM-DD``.  Notes whose key already exists in the store are
    skipped.

    Returns:
        ``(imported, skipped)`` counts.
    """
    memory_dir = workspace_dir / "memory"
    if not memory_dir.is_dir():
        return 0, 0

    imported = 0
    skipped = 0

    for note_path in sorted(memory_dir.iterdir()):
        if not note_path.is_file():
            continue

        match = _DAILY_NOTE_RE.match(note_path.name)
        if not match:
            continue

        date_str = match.group(1)
        key = f"daily-{date_str}"

        if store.get(key) is not None:
            logger.debug("markdown_sync.skip_daily_existing", key=key)
            skipped += 1
            continue

        try:
            text = note_path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError:
            logger.warning("markdown_sync.encoding_error", path=str(note_path))
            skipped += 1
            continue

        if not text:
            logger.debug("markdown_sync.skip_empty_daily", key=key)
            skipped += 1
            continue

        value = text[:MAX_VALUE_LENGTH]
        store.save(
            key=key,
            value=value,
            tier=MemoryTier.context.value,
            source=MemorySource.system.value,
        )
        imported += 1
        logger.debug("markdown_sync.imported_daily", key=key, date=date_str)

    return imported, skipped
