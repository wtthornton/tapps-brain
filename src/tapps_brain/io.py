"""Import and export for shared memory entries.

Enables teams to share and back up project memories via JSON, JSONL, Markdown,
or MIF v2. All file paths are validated through ``security/path_validator.py``.

Native envelope (TAP-5027 / TAP-5028)::

    {"format": "tapps-memory", "format_version": "1.0", "memories": [...], ...}
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import structlog

from tapps_brain import __version__
from tapps_brain.io_bundle import (
    build_embeddings_sidecar,
    collect_embeddings,
    collect_relations,
    restore_embeddings,
    restore_relations,
)
from tapps_brain.io_jsonl import build_jsonl_export, parse_jsonl_payload
from tapps_brain.io_limits import (
    CLI_DEFAULT_MAX_IMPORT_ENTRIES,
    DEFAULT_MAX_IMPORT_ENTRIES,
    NATIVE_FORMAT,
    NATIVE_FORMAT_VERSION,
    enforce_import_limit,
    resolve_max_import_entries,
)
from tapps_brain.io_mif import (
    MIF_FORMAT,
    build_mif_document,
    entry_to_mif_unit,
    is_mif_document,
    looks_like_mif_list,
    mif_unit_to_memory_dict,
)
from tapps_brain.models import MemoryEntry

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain._protocols import PathValidatorLike
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

ExportFormat = Literal["json", "markdown", "mif", "jsonl"]
GroupByOption = Literal["tier", "tag", "none"]

# Re-exports for public API stability
__all__ = [
    "CLI_DEFAULT_MAX_IMPORT_ENTRIES",
    "DEFAULT_MAX_IMPORT_ENTRIES",
    "MIF_FORMAT",
    "NATIVE_FORMAT",
    "NATIVE_FORMAT_VERSION",
    "build_embeddings_sidecar",
    "build_jsonl_export",
    "build_mif_document",
    "build_native_envelope",
    "collect_embeddings",
    "collect_relations",
    "entry_to_mif_unit",
    "export_bundle_dict",
    "export_memories",
    "export_to_markdown",
    "import_memories",
    "import_memory_dicts",
    "parse_import_payload",
    "parse_jsonl_payload",
    "resolve_max_import_entries",
    "restore_embeddings",
    "restore_relations",
]


def build_native_envelope(
    memories: list[dict[str, Any]],
    *,
    source_project: str = "",
    relations: list[dict[str, Any]] | None = None,
    embeddings: dict[str, Any] | None = None,
    exported_at: str | None = None,
) -> dict[str, Any]:
    """Build the versioned ``tapps-memory`` 1.0 export envelope."""
    at = exported_at or datetime.now(tz=UTC).isoformat()
    payload: dict[str, Any] = {
        "format": NATIVE_FORMAT,
        "format_version": NATIVE_FORMAT_VERSION,
        "memories": memories,
        "exported_at": at,
        "source_project": source_project,
        "entry_count": len(memories),
        "tapps_version": __version__,
    }
    if relations is not None:
        payload["relations"] = relations
        payload["relation_count"] = len(relations)
    if embeddings is not None:
        payload["embeddings"] = embeddings
    return payload


def parse_import_payload(
    data: object,
    *,
    max_entries: int | None = None,
) -> dict[str, Any]:
    """Normalize import JSON into memories (+ optional relations/embeddings)."""
    limit = resolve_max_import_entries(max_entries)

    if isinstance(data, list):
        return _parse_list_payload(data, limit=limit)

    if not isinstance(data, dict):
        msg = "Import payload must be a JSON object or array."
        raise ValueError(msg)

    if is_mif_document(data):
        return _parse_mif_payload(data, limit=limit)

    return _parse_envelope_payload(data, limit=limit)


def _parse_list_payload(data: list[Any], *, limit: int) -> dict[str, Any]:
    if looks_like_mif_list(data):
        memories = [mif_unit_to_memory_dict(u) for u in data if isinstance(u, dict)]
        enforce_import_limit(len(memories), limit)
        return {
            "memories": [m for m in memories if m],
            "relations": None,
            "embeddings": None,
            "detected_format": MIF_FORMAT,
        }
    memories = [m for m in data if isinstance(m, dict)]
    dropped = len(data) - len(memories)
    if dropped:
        logger.warning("memory_import_non_dict_entries_dropped", count=dropped)
    enforce_import_limit(len(memories), limit)
    return {
        "memories": memories,
        "relations": None,
        "embeddings": None,
        "detected_format": "bare-array",
    }


def _parse_mif_payload(data: dict[str, Any], *, limit: int) -> dict[str, Any]:
    units = data.get("memories")
    if units is None and "content" in data:
        units = [data]
    if not isinstance(units, list):
        msg = "MIF document must contain a 'memories' list or a single unit."
        raise ValueError(msg)
    memories = [mif_unit_to_memory_dict(u) for u in units if isinstance(u, dict)]
    enforce_import_limit(len(memories), limit)
    return {
        "memories": [m for m in memories if m],
        "relations": None,
        "embeddings": data.get("embeddings"),
        "detected_format": MIF_FORMAT,
    }


def _parse_envelope_payload(data: dict[str, Any], *, limit: int) -> dict[str, Any]:
    memories_raw = data.get("memories")
    if memories_raw is None:
        msg = "Import payload must contain a 'memories' list (or be a bare array)."
        raise ValueError(msg)
    if not isinstance(memories_raw, list):
        msg = "Import file must contain a 'memories' list."
        raise ValueError(msg)

    enforce_import_limit(len(memories_raw), limit)
    memories = [m for m in memories_raw if isinstance(m, dict)]
    dropped = len(memories_raw) - len(memories)
    if dropped:
        logger.warning("memory_import_non_dict_entries_dropped", count=dropped)

    relations = data.get("relations")
    if relations is not None and not isinstance(relations, list):
        msg = "'relations' must be a list when present."
        raise ValueError(msg)

    embeddings = data.get("embeddings")
    if embeddings is not None and not isinstance(embeddings, dict):
        msg = "'embeddings' must be an object when present."
        raise ValueError(msg)

    fmt = data.get("format")
    detected = NATIVE_FORMAT if fmt == NATIVE_FORMAT else "legacy-envelope"
    return {
        "memories": memories,
        "relations": relations,
        "embeddings": embeddings,
        "detected_format": detected,
    }


# ---------------------------------------------------------------------------
# Markdown export (Epic 65.2) + frontmatter key (TAP-5032)
# ---------------------------------------------------------------------------


def _entry_to_frontmatter(entry: MemoryEntry) -> str:
    """Render a MemoryEntry as Obsidian-style YAML frontmatter."""
    tags = entry.tags.copy()
    if str(entry.tier) not in tags:
        tags.append(str(entry.tier))
    lines = [
        "---",
        f"key: {entry.key!r}",
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
    tier or tag, sorted by ``(updated_at, key)`` within groups.
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
            tier_entries = by_tier.pop(tier_name, [])
            if not tier_entries:
                continue
            lines.append(f"# {tier_name.title()}")
            lines.append("")
            for entry in sort_entries(tier_entries):
                lines.extend(render_entry(entry))
        for tier_name in sorted(by_tier.keys()):
            tier_entries = by_tier[tier_name]
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


def _filter_entries(
    entries: list[MemoryEntry],
    *,
    tier: str | None,
    scope: str | None,
    min_confidence: float | None,
) -> list[MemoryEntry]:
    if tier is not None:
        entries = [e for e in entries if str(e.tier) == tier]
    if scope is not None:
        entries = [e for e in entries if e.scope.value == scope]
    if min_confidence is not None:
        entries = [e for e in entries if e.confidence >= min_confidence]
    return entries


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
    include_relations: bool = False,
    include_embeddings: bool = False,
) -> dict[str, Any]:
    """Export memories to JSON, JSONL, Markdown, or MIF (TAP-5027)."""
    validated_path = validator.validate_path(output_path, must_exist=False, max_file_size=None)

    snapshot = store.snapshot()
    entries = _filter_entries(
        list(snapshot.entries),
        tier=tier,
        scope=scope,
        min_confidence=min_confidence,
    )

    exported_at = datetime.now(tz=UTC).isoformat()
    validated_path.parent.mkdir(parents=True, exist_ok=True)

    allowed: set[str] = {"json", "markdown", "mif", "jsonl"}
    eff_format: str = export_format if export_format in allowed else "json"

    relations: list[dict[str, Any]] | None = None
    if include_relations:
        relations = collect_relations(store)

    embeddings: dict[str, Any] | None = None
    if include_embeddings:
        embeddings = collect_embeddings(store)

    if eff_format == "markdown":
        content = export_to_markdown(
            entries,
            include_frontmatter=include_frontmatter,
            group_by=group_by,
            include_metadata=include_metadata,
        )
        validated_path.write_text(content, encoding="utf-8")
    elif eff_format == "mif":
        payload = build_mif_document(
            entries,
            source_project=snapshot.project_root,
            exported_at=exported_at,
        )
        if relations is not None:
            payload["relations"] = relations
        if embeddings is not None:
            payload["embeddings"] = embeddings
        validated_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    elif eff_format == "jsonl":
        content = build_jsonl_export(
            entries,
            source_project=snapshot.project_root,
            exported_at=exported_at,
        )
        validated_path.write_text(content, encoding="utf-8")
    else:
        payload = build_native_envelope(
            [e.model_dump(mode="json") for e in entries],
            source_project=snapshot.project_root,
            relations=relations,
            embeddings=embeddings,
            exported_at=exported_at,
        )
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
        "relation_count": len(relations) if relations is not None else 0,
        "embedding_count": (
            int(embeddings.get("entry_count", 0)) if isinstance(embeddings, dict) else 0
        ),
    }


def export_bundle_dict(
    store: MemoryStore,
    *,
    project_root: str = "",
    tier: str | None = None,
    scope: str | None = None,
    min_confidence: float | None = None,
    include_relations: bool = True,
    include_embeddings: bool = False,
    export_format: str = "json",
) -> dict[str, Any]:
    """Build an in-memory export bundle (used by MCP ``memory_export``)."""
    if hasattr(store, "list_all"):
        entries = list(store.list_all(tier=tier, scope=scope))
    else:
        entries = list(store.snapshot().entries)
        entries = _filter_entries(entries, tier=tier, scope=scope, min_confidence=min_confidence)
    if min_confidence is not None:
        entries = [e for e in entries if e.confidence >= min_confidence]

    exported_at = datetime.now(tz=UTC).isoformat()
    root = project_root or getattr(getattr(store, "snapshot", lambda: None)(), "project_root", "")
    if not root:
        root = str(getattr(store, "project_root", "") or "")

    relations = collect_relations(store) if include_relations else None
    embeddings = collect_embeddings(store) if include_embeddings else None

    if export_format == "mif":
        payload = build_mif_document(entries, source_project=root, exported_at=exported_at)
        if relations is not None:
            payload["relations"] = relations
        if embeddings is not None:
            payload["embeddings"] = embeddings
        return payload

    return build_native_envelope(
        [e.model_dump(mode="json") for e in entries],
        source_project=root,
        relations=relations,
        embeddings=embeddings,
        exported_at=exported_at,
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _validate_import_payload(
    data: object,
    *,
    max_entries: int | None = None,
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper returning just the memories list."""
    parsed = parse_import_payload(data, max_entries=max_entries)
    return list(parsed["memories"])


def _save_imported_entry(
    store: MemoryStore,
    entry: MemoryEntry,
    *,
    overwrite: bool,
) -> str:
    """Save one entry; returns ``imported`` / ``skipped`` / ``error``."""
    peek = getattr(store, "_ensure_entry_cached", None)
    existing = peek(entry.key) if callable(peek) else store.get(entry.key)
    if existing is not None and not overwrite:
        return "skipped"

    agent_suffix = "(imported)"
    source_agent = entry.source_agent
    if not source_agent.endswith(agent_suffix):
        source_agent = f"{source_agent} {agent_suffix}"

    result = store.save(
        key=entry.key,
        value=entry.value,
        tier=str(entry.tier),
        source=entry.source.value,
        source_agent=source_agent,
        scope=entry.scope.value,
        tags=entry.tags,
        branch=entry.branch,
        confidence=entry.confidence,
        memory_group=entry.memory_group,
        temporal_sensitivity=getattr(entry, "temporal_sensitivity", None),
        agent_scope=entry.agent_scope,
    )
    if isinstance(result, dict) and result.get("error"):
        return "error"
    return "imported"


def import_memory_dicts(
    store: MemoryStore,
    memory_dicts: list[dict[str, Any]],
    *,
    overwrite: bool = False,
    relations: list[dict[str, Any]] | None = None,
    embeddings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Import pre-parsed memory dicts (and optional relations/embeddings)."""
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
        status = _save_imported_entry(store, entry, overwrite=overwrite)
        if status == "imported":
            imported += 1
        elif status == "skipped":
            skipped += 1
        else:
            errors += 1

    relations_restored = 0
    if relations:
        relations_restored = restore_relations(store, relations)

    embedding_stats = {"restored": 0, "skipped_mismatch": 0, "skipped_no_api": 0}
    if embeddings:
        embedding_stats = restore_embeddings(store, embeddings)

    return {
        "imported_count": imported,
        "skipped_count": skipped,
        "error_count": errors,
        "relations_restored": relations_restored,
        "embeddings_restored": embedding_stats["restored"],
        "embeddings_skipped_mismatch": embedding_stats["skipped_mismatch"],
    }


def import_memories(
    store: MemoryStore,
    input_path: Path,
    validator: PathValidatorLike,
    *,
    overwrite: bool = False,
    max_entries: int | None = None,
) -> dict[str, Any]:
    """Import memories from a JSON / JSONL file (native, bare array, or MIF)."""
    validated_path = validator.validate_path(input_path, must_exist=True)
    raw = validated_path.read_text(encoding="utf-8")

    # JSONL heuristic: multiple non-empty lines that each parse as JSON
    stripped_lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(stripped_lines) > 1:
        try:
            first = json.loads(stripped_lines[0])
            if isinstance(first, dict) and (
                first.get("encoding") == "jsonl"
                or (first.get("format") == NATIVE_FORMAT and "memories" not in first)
            ):
                parsed = parse_jsonl_payload(raw, max_entries=max_entries)
                result = import_memory_dicts(
                    store,
                    parsed["memories"],
                    overwrite=overwrite,
                    relations=parsed.get("relations"),
                    embeddings=parsed.get("embeddings"),
                )
                result["file_path"] = str(validated_path)
                result["detected_format"] = "jsonl"
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"Import file is not valid JSON: {exc}"
        raise ValueError(msg) from exc

    parsed = parse_import_payload(data, max_entries=max_entries)
    result = import_memory_dicts(
        store,
        parsed["memories"],
        overwrite=overwrite,
        relations=parsed.get("relations"),
        embeddings=parsed.get("embeddings"),
    )
    result["file_path"] = str(validated_path)
    result["detected_format"] = parsed["detected_format"]

    logger.info(
        "memories_imported",
        imported=result["imported_count"],
        skipped=result["skipped_count"],
        errors=result["error_count"],
        path=str(validated_path),
        detected_format=result["detected_format"],
    )
    return result
