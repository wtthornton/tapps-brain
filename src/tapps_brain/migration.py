"""Migration utilities for importing data from memory-core to tapps-brain.

Provides :func:`migrate_from_workspace` to import:

1. ``MEMORY.md`` sections with tier inference (H2→architectural, H3→pattern,
   H4→procedural, H5+→context).
2. ``memory/YYYY-MM-DD.md`` daily notes as context-tier entries.
3. memory-core's SQLite database (``~/.openclaw/memory/<agentId>.sqlite``)
   if found.

**Conflict resolution:** tapps-brain always wins — existing entries are never
overwritten. Duplicate keys are counted as *skipped*.

**Idempotency:** Running twice produces no duplicates.

Part of EPIC-026 (OpenClaw memory replacement, story-026.5).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.markdown_sync import sync_from_markdown
from tapps_brain.models import MAX_KEY_LENGTH, MAX_VALUE_LENGTH, MemorySource, MemoryTier

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# memory-core default database directory
_MEMORY_CORE_DB_DIR = Path.home() / ".openclaw" / "memory"

# Common table names used by memory-core (checked in order)
_MEMORY_CORE_TABLES = ("memories", "entries", "notes")

# Key column candidates (most-to-least specific)
_KEY_COLUMNS = ("key", "id", "slug", "title", "name")

# Value column candidates
_VALUE_COLUMNS = ("value", "content", "text", "body", "description")

# H2-H6 headings in MEMORY.md - each is one importable entry
_HEADING_RE = re.compile(r"^#{2,6}\s+\S", re.MULTILINE)

# Daily-note filename pattern: YYYY-MM-DD.md
_DAILY_NOTE_GLOB = "????-??-??.md"

# Key-slug normalisation — mirrors markdown_sync._slugify
_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9._-]+")
_MULTI_SEP_RE = re.compile(r"[-_.]{2,}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert arbitrary text to a valid memory key slug.

    Lowercases, replaces non-alphanumeric characters with hyphens, collapses
    consecutive separators, strips leading/trailing separators, and truncates
    to ``MAX_KEY_LENGTH``.
    """
    slug = _SLUG_CLEAN_RE.sub("-", text.lower().strip())
    slug = _MULTI_SEP_RE.sub("-", slug)
    slug = slug.strip("-._")
    return (slug or "m-")[:MAX_KEY_LENGTH]


def _count_workspace_entries(workspace_dir: Path) -> tuple[int, int]:
    """Count importable entries in a workspace without touching the store.

    Args:
        workspace_dir: Root directory of the OpenClaw workspace.

    Returns:
        ``(memory_md_count, daily_notes_count)`` as a tuple.
    """
    # Count H2-H6 headings in MEMORY.md
    md_count = 0
    memory_md_path = workspace_dir / "MEMORY.md"
    if memory_md_path.is_file():
        try:
            text = memory_md_path.read_text(encoding="utf-8")
            md_count = len(_HEADING_RE.findall(text))
        except (OSError, UnicodeDecodeError):
            pass

    # Count daily-note files (YYYY-MM-DD.md)
    daily_count = 0
    memory_dir = workspace_dir / "memory"
    if memory_dir.is_dir():
        daily_count = sum(1 for _ in memory_dir.glob(_DAILY_NOTE_GLOB))

    return md_count, daily_count


def _import_memory_core_sqlite(  # noqa: PLR0915
    store: MemoryStore | None,
    db_path: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Import entries from a memory-core SQLite database.

    In *dry_run* mode ``store`` is not accessed (may be ``None``); the function
    only counts how many rows would be imported.

    Tries the table names in :data:`_MEMORY_CORE_TABLES` (first match wins).
    Within the chosen table it auto-detects the best key and value columns.
    Empty values are silently skipped.

    Args:
        store: MemoryStore to save into (ignored when *dry_run* is ``True``).
        db_path: Path to the memory-core ``.sqlite`` file.
        dry_run: Count rows without writing.

    Returns:
        Dict with ``imported``, ``skipped``, and ``errors`` integer counts.
    """
    imported = 0
    skipped = 0
    errors = 0

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        logger.warning("migration.sqlite_open_error", path=str(db_path), error=str(exc))
        return {"imported": 0, "skipped": 0, "errors": 1}

    try:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}

        for table_name in _MEMORY_CORE_TABLES:
            if table_name not in existing_tables:
                continue

            # Discover columns available in this table
            cursor = conn.execute(f"PRAGMA table_info({table_name})")
            col_names = {row[1] for row in cursor.fetchall()}

            key_col: str | None = next((c for c in _KEY_COLUMNS if c in col_names), None)
            val_col: str | None = next((c for c in _VALUE_COLUMNS if c in col_names), None)

            if not val_col:
                logger.warning(
                    "migration.no_value_col",
                    table=table_name,
                    available_cols=sorted(col_names),
                )
                continue

            select_key = key_col if key_col else "rowid"
            cursor = conn.execute(f"SELECT {select_key}, {val_col} FROM {table_name}")
            rows = list(cursor.fetchall())

            for idx, row in enumerate(rows):
                raw_key = str(row[0]) if row[0] else ""
                raw_val = str(row[1]) if row[1] else ""

                if not raw_val.strip():
                    skipped += 1
                    continue

                key = _slugify(raw_key) if raw_key else f"mc-{idx + 1}"
                value = raw_val[:MAX_VALUE_LENGTH]

                if dry_run:
                    imported += 1
                    continue

                # store is always non-None when dry_run is False (enforced by caller)
                assert store is not None
                if store.get(key) is not None:
                    skipped += 1
                    continue

                try:
                    store.save(
                        key=key,
                        value=value,
                        tier=MemoryTier.context,
                        source=MemorySource.system,
                        tags=["migrated-from-memory-core"],
                    )
                    imported += 1
                except Exception as exc:
                    logger.warning("migration.save_error", key=key, error=str(exc))
                    errors += 1

            break  # Use only the first matching table

    except sqlite3.Error as exc:
        logger.warning("migration.sqlite_read_error", path=str(db_path), error=str(exc))
        errors += 1
    finally:
        conn.close()

    return {"imported": imported, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_memory_core_db(agent_id: str | None = None) -> Path | None:
    """Locate memory-core's SQLite database file.

    Searches ``~/.openclaw/memory/`` for ``<agentId>.sqlite`` first, then falls
    back to the first ``.sqlite`` file found alphabetically in that directory.

    Args:
        agent_id: Agent ID whose database to look for (optional).

    Returns:
        Resolved path to the ``.sqlite`` file, or ``None`` if not found.
    """
    if not _MEMORY_CORE_DB_DIR.is_dir():
        return None
    if agent_id:
        candidate = _MEMORY_CORE_DB_DIR / f"{agent_id}.sqlite"
        if candidate.is_file():
            return candidate
    for f in sorted(_MEMORY_CORE_DB_DIR.glob("*.sqlite")):
        return f
    return None


def migrate_from_workspace(
    store: MemoryStore | None,
    workspace_dir: Path,
    *,
    agent_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate memories from an OpenClaw workspace to tapps-brain.

    Import sequence:

    1. ``MEMORY.md`` sections via :func:`~tapps_brain.markdown_sync.sync_from_markdown`.
    2. ``memory/YYYY-MM-DD.md`` daily notes (handled by *sync_from_markdown*).
    3. memory-core's SQLite database if one is found by
       :func:`find_memory_core_db`.

    In *dry_run* mode ``store`` is not required and may be ``None``; the
    function returns estimated counts without writing anything.

    Args:
        store: MemoryStore to import into.  Required when *dry_run* is ``False``.
        workspace_dir: Root directory of the OpenClaw workspace.
        agent_id: Agent ID used to locate memory-core SQLite (optional).
        dry_run: If ``True``, count what would be imported without writing.

    Returns:
        Dict with the following keys:

        - ``imported`` (int) — total entries saved (or would be saved).
        - ``skipped`` (int) — entries that already existed in the store.
        - ``errors`` (int) — entries that failed to import.
        - ``memory_md`` (int) — new entries from ``MEMORY.md``.
        - ``daily_notes`` (int) — new entries from daily-note files.
        - ``memory_core_sqlite`` (int) — new entries from memory-core SQLite.
        - ``memory_core_db`` (str | None) — path to memory-core DB used.
        - ``dry_run`` (bool) — present (and ``True``) only in dry-run mode.

    Raises:
        ValueError: If *store* is ``None`` and *dry_run* is ``False``.
    """
    db_path = find_memory_core_db(agent_id)

    if dry_run:
        md_count, daily_count = _count_workspace_entries(workspace_dir)
        sqlite_count = 0
        if db_path:
            sqlite_result = _import_memory_core_sqlite(None, db_path, dry_run=True)
            sqlite_count = sqlite_result["imported"]
        total = md_count + daily_count + sqlite_count
        result: dict[str, Any] = {
            "imported": total,
            "skipped": 0,
            "errors": 0,
            "memory_md": md_count,
            "daily_notes": daily_count,
            "memory_core_sqlite": sqlite_count,
            "memory_core_db": str(db_path) if db_path else None,
            "dry_run": True,
        }
        logger.info(
            "migration.dry_run_complete",
            workspace=str(workspace_dir),
            would_import=total,
        )
        return result

    if store is None:
        raise ValueError("store must be provided when dry_run=False")

    # Phase 1 + 2: MEMORY.md and daily notes via sync_from_markdown
    sync_result = sync_from_markdown(store, workspace_dir)
    md_imported: int = sync_result["memory_md"]
    daily_imported: int = sync_result["daily_notes"]
    total_imported: int = sync_result["imported"]
    total_skipped: int = sync_result["skipped"]

    # Phase 3: memory-core SQLite (optional)
    sqlite_imported = 0
    sqlite_errors = 0
    if db_path:
        sqlite_result = _import_memory_core_sqlite(store, db_path, dry_run=False)
        sqlite_imported = sqlite_result["imported"]
        total_imported += sqlite_imported
        total_skipped += sqlite_result["skipped"]
        sqlite_errors = sqlite_result["errors"]

    logger.info(
        "migration.complete",
        workspace=str(workspace_dir),
        imported=total_imported,
        skipped=total_skipped,
        errors=sqlite_errors,
    )
    return {
        "imported": total_imported,
        "skipped": total_skipped,
        "errors": sqlite_errors,
        "memory_md": md_imported,
        "daily_notes": daily_imported,
        "memory_core_sqlite": sqlite_imported,
        "memory_core_db": str(db_path) if db_path else None,
    }
