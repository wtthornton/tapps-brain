"""Import and export for shared memory entries.

Enables teams to share and back up project memories via JSON or Markdown files.
All file paths are validated through ``security/path_validator.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import structlog

from tapps_brain import __version__
from tapps_brain.models import MemoryEntry

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain._protocols import PathValidatorLike
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_IMPORT_ENTRIES = 500

ExportFormat = Literal["json", "markdown"]
GroupByOption = Literal["tier", "tag", "none"]


# ---------------------------------------------------------------------------
# Markdown export (Epic 65.2)
# ---------------------------------------------------------------------------


def _entry_to_frontmatter(entry: MemoryEntry) -> str:
    """Render a MemoryEntry as Obsidian-style YAML frontmatter."""
    tags = entry.tags.copy()
    if str(entry.tier) not in tags:
        tags.append(str(entry.tier))
    lines = [
        "---",
        f"tags: {json.dumps(tags)}",
        f"created_at: {entry.created_at!r}",
        f"updated_at: {entry.updated_at!r}",
        f"confidence: {entry.confidence:.2f}",
        f"source: {entry.source.value!r}",
        f"tier: {str(entry.tier)!r}",
        "---",
    ]
    return "\n".join(lines)


def export_to_markdown(
    entries: list[MemoryEntry],
    *,
    include_frontmatter: bool = True,
    group_by: GroupByOption = "tier",
    include_metadata: bool = False,
) -> str:
    """Export memory entries to Markdown (Epic 65.2).

    Outputs Obsidian-friendly Markdown with optional frontmatter, grouped by
    tier or tag, sorted by key within groups.

    Args:
        entries: Memory entries to export.
        include_frontmatter: Include YAML frontmatter per entry (default True).
        group_by: "tier" (group by tier), "tag" (group by first tag), or "none".
        include_metadata: Include created_at/confidence in body (default False).

    Returns:
        Markdown string.
    """
    if not entries:
        return "# TappsMCP Memory Export\n\n*No memories.*\n"

    lines: list[str] = ["# TappsMCP Memory Export", ""]

    def sort_entries(lst: list[MemoryEntry]) -> list[MemoryEntry]:
        return sorted(lst, key=lambda e: (e.updated_at, e.key))

    def render_entry(e: MemoryEntry) -> list[str]:
        block: list[str] = []
        if include_frontmatter:
            block.append(_entry_to_frontmatter(e))
            block.append("")
        title = f"## {e.key}"
        block.append(title)
        block.append("")
        block.append(e.value.strip())
        if include_metadata:
            block.append("")
            block.append(f"*created: {e.created_at} | confidence: {e.confidence:.2f}*")
        block.append("")
        return block

    if group_by == "none":
        for entry in sort_entries(entries):
            lines.extend(render_entry(entry))
    elif group_by == "tier":
        by_tier: dict[str, list[MemoryEntry]] = {}
        for e in entries:
            t = str(e.tier)
            by_tier.setdefault(t, []).append(e)
        for tier_name in ("architectural", "pattern", "procedural", "context"):
            tier_entries = by_tier.get(tier_name, [])
            if not tier_entries:
                continue
            lines.append(f"# {tier_name.title()}")
            lines.append("")
            for entry in sort_entries(tier_entries):
                lines.extend(render_entry(entry))
    else:  # group_by == "tag"
        by_tag: dict[str, list[MemoryEntry]] = {"_untagged": []}
        for e in entries:
            first_tag = e.tags[0] if e.tags else None
            if first_tag is not None:
                by_tag.setdefault(first_tag, []).append(e)
            else:
                by_tag["_untagged"].append(e)
        ordered = sorted(by_tag.keys(), key=lambda k: (k == "_untagged", k))
        for tag_key in ordered:
            tag_entries = by_tag[tag_key]
            if not tag_entries:
                continue
            label = "Untagged" if tag_key == "_untagged" else tag_key
            lines.append(f"# {label}")
            lines.append("")
            for entry in sort_entries(tag_entries):
                lines.extend(render_entry(entry))

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_memories(
    store: MemoryStore,
    output_path: Path,
    validator: PathValidatorLike,
    *,
    tier: str | None = None,
    scope: str | None = None,
    min_confidence: float | None = None,
    export_format: ExportFormat = "json",
    include_frontmatter: bool = True,
    group_by: GroupByOption = "tier",
    include_metadata: bool = False,
) -> dict[str, Any]:
    """Export memories to a JSON or Markdown file (Epic 65.2).

    Args:
        store: The memory store to export from.
        output_path: Destination file path.
        validator: Path validator for sandbox enforcement.
        tier: Optional tier filter.
        scope: Optional scope filter.
        min_confidence: Optional minimum confidence filter.
        export_format: "json" (default) or "markdown".
        include_frontmatter: For markdown, include Obsidian frontmatter (default True).
        group_by: For markdown, "tier", "tag", or "none" (default "tier").
        include_metadata: For markdown, include created_at/confidence in body (default False).

    Returns:
        Summary dict with ``exported_count``, ``file_path``, ``exported_at``.
    """
    validated_path = validator.validate_path(output_path, must_exist=False, max_file_size=None)

    snapshot = store.snapshot()
    entries = snapshot.entries

    # Apply filters
    if tier is not None:
        entries = [e for e in entries if str(e.tier) == tier]
    if scope is not None:
        entries = [e for e in entries if e.scope.value == scope]
    if min_confidence is not None:
        entries = [e for e in entries if e.confidence >= min_confidence]

    exported_at = datetime.now(tz=UTC).isoformat()

    validated_path.parent.mkdir(parents=True, exist_ok=True)

    eff_format = export_format if export_format in ("json", "markdown") else "json"

    if eff_format == "markdown":
        content = export_to_markdown(
            entries,
            include_frontmatter=include_frontmatter,
            group_by=group_by,
            include_metadata=include_metadata,
        )
        validated_path.write_text(content, encoding="utf-8")
    else:  # "json" (default)
        payload: dict[str, Any] = {
            "memories": [e.model_dump(mode="json") for e in entries],
            "exported_at": exported_at,
            "source_project": snapshot.project_root,
            "entry_count": len(entries),
            "tapps_version": __version__,
        }
        validated_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    logger.info(
        "memories_exported",
        count=len(entries),
        path=str(validated_path),
        format=eff_format,
    )

    return {
        "exported_count": len(entries),
        "file_path": str(validated_path),
        "exported_at": exported_at,
        "format": eff_format,
    }


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _validate_import_payload(data: object) -> list[dict[str, Any]]:
    """Validate the top-level structure of an import JSON payload.

    Returns the list of raw memory dicts.

    Raises:
        ValueError: If the payload is malformed.
    """
    if not isinstance(data, dict):
        msg = "Import file must contain a JSON object."
        raise ValueError(msg)

    memories = data.get("memories")
    if not isinstance(memories, list):
        msg = "Import file must contain a 'memories' list."
        raise ValueError(msg)

    if len(memories) > _MAX_IMPORT_ENTRIES:
        msg = f"Import exceeds max entries ({len(memories)} > {_MAX_IMPORT_ENTRIES})."
        raise ValueError(msg)

    valid = [m for m in memories if isinstance(m, dict)]
    dropped = len(memories) - len(valid)
    if dropped:
        logger.warning("memory_import_non_dict_entries_dropped", count=dropped)
    return valid


def import_memories(
    store: MemoryStore,
    input_path: Path,
    validator: PathValidatorLike,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import memories from a JSON file.

    Args:
        store: The memory store to import into.
        input_path: Source JSON file path.
        validator: Path validator for sandbox enforcement.
        overwrite: If True, overwrite existing keys. Default: skip.

    Returns:
        Summary dict with ``imported_count``, ``skipped_count``, ``error_count``.
    """
    validated_path = validator.validate_path(input_path, must_exist=True)

    raw = validated_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"Import file is not valid JSON: {exc}"
        raise ValueError(msg) from exc
    memory_dicts = _validate_import_payload(data)

    imported = 0
    skipped = 0
    errors = 0

    for raw_entry in memory_dicts:
        try:
            entry = MemoryEntry.model_validate(raw_entry)
        except Exception as exc:
            errors += 1
            logger.warning("memory_import_entry_invalid", entry=raw_entry, error=str(exc))
            continue

        # Check for existing key
        existing = store.get(entry.key)
        if existing is not None and not overwrite:
            skipped += 1
            continue

        # Mark as imported
        agent_suffix = "(imported)"
        source_agent = entry.source_agent
        if not source_agent.endswith(agent_suffix):
            source_agent = f"{source_agent} {agent_suffix}"

        store.save(
            key=entry.key,
            value=entry.value,
            tier=str(entry.tier),
            source=entry.source.value,
            source_agent=source_agent,
            scope=entry.scope.value,
            tags=entry.tags,
            branch=entry.branch,
            confidence=entry.confidence,
        )
        imported += 1

    logger.info(
        "memories_imported",
        imported=imported,
        skipped=skipped,
        errors=errors,
        path=str(validated_path),
    )

    return {
        "imported_count": imported,
        "skipped_count": skipped,
        "error_count": errors,
        "file_path": str(validated_path),
    }
