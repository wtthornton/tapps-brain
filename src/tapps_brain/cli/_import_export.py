"""Shared CLI import/export helpers (TAP-5027)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.store import MemoryStore


def import_file_to_store(
    store: MemoryStore,
    input_file: Path,
    *,
    dry_run: bool = False,
    mode: str | None = None,
    fmt: str | None = None,
    max_entries: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import a file into *store*; return a CLI-shaped result dict.

    Raises:
        ValueError: Parse / limit failures (caller prints and exits).
        FileNotFoundError: Missing input file.
        json.JSONDecodeError: Invalid JSON body.
    """
    from tapps_brain.io import (
        CLI_DEFAULT_MAX_IMPORT_ENTRIES,
        import_memory_dicts,
        parse_import_payload,
        parse_jsonl_payload,
        resolve_max_import_entries,
    )

    if not input_file.exists():
        msg = f"File not found: {input_file}"
        raise FileNotFoundError(msg)

    limit = resolve_max_import_entries(
        max_entries if max_entries is not None else CLI_DEFAULT_MAX_IMPORT_ENTRIES
    )
    raw = input_file.read_text(encoding="utf-8")
    suffix = input_file.suffix.lower()
    forced = (fmt or "").lower() or None

    if forced == "markdown" or (forced is None and suffix in {".md", ".markdown"}):
        return _import_markdown(
            store, input_file, raw, mode=mode, dry_run=dry_run, overwrite=overwrite
        )

    if forced in {"mem0", "letta-af"}:
        return _import_adapter(
            store, input_file, raw, forced=forced, dry_run=dry_run, overwrite=overwrite
        )

    if forced == "jsonl" or suffix == ".jsonl":
        parsed = parse_jsonl_payload(raw, max_entries=limit)
    else:
        data = json.loads(raw)
        auto = _maybe_adapter_import(store, input_file, data, dry_run=dry_run, overwrite=overwrite)
        if auto is not None:
            return auto
        parsed = parse_import_payload(data, max_entries=limit)

    if dry_run:
        return {"would_import": len(parsed["memories"]), "file": str(input_file)}

    stats = import_memory_dicts(
        store,
        parsed["memories"],
        overwrite=overwrite,
        relations=parsed.get("relations"),
        embeddings=parsed.get("embeddings"),
    )
    return {
        "imported": stats["imported_count"],
        "skipped": stats["skipped_count"],
        "file": str(input_file),
    }


def _import_markdown(
    store: MemoryStore,
    input_file: Path,
    raw: str,
    *,
    mode: str | None,
    dry_run: bool,
    overwrite: bool,
) -> dict[str, Any]:
    from tapps_brain.markdown_import import (
        import_frontmatter_markdown,
        import_memory_md,
        looks_like_frontmatter_export,
    )

    use_frontmatter = mode == "frontmatter" or (mode is None and looks_like_frontmatter_export(raw))
    if mode == "memory-md":
        use_frontmatter = False
    preview = "frontmatter" if use_frontmatter else "memory-md"
    if dry_run:
        return {"would_import": "markdown", "mode": preview, "file": str(input_file)}
    if use_frontmatter:
        imported = import_frontmatter_markdown(input_file, store, overwrite=overwrite)
    else:
        imported = import_memory_md(input_file, store)
    return {
        "imported": imported,
        "skipped": 0,
        "file": str(input_file),
        "mode": preview,
    }


def _import_adapter(
    store: MemoryStore,
    input_file: Path,
    raw: str,
    *,
    forced: str,
    dry_run: bool,
    overwrite: bool,
) -> dict[str, Any]:
    from tapps_brain.io import import_memory_dicts

    data = json.loads(raw)
    warnings: list[str] = []
    if forced == "mem0":
        from tapps_brain.adapters.mem0 import mem0_to_memory_dicts

        memory_dicts = mem0_to_memory_dicts(data)
    else:
        from tapps_brain.adapters.letta_af import letta_af_to_memory_dicts

        memory_dicts, warnings = letta_af_to_memory_dicts(data)
    if dry_run:
        return {
            "would_import": len(memory_dicts),
            "file": str(input_file),
            "warnings": warnings,
        }
    stats = import_memory_dicts(store, memory_dicts, overwrite=overwrite)
    return {
        "imported": stats["imported_count"],
        "skipped": stats["skipped_count"],
        "file": str(input_file),
        "warnings": warnings,
    }


def _maybe_adapter_import(
    store: MemoryStore,
    input_file: Path,
    data: object,
    *,
    dry_run: bool,
    overwrite: bool,
) -> dict[str, Any] | None:
    from tapps_brain.adapters.letta_af import looks_like_letta_af
    from tapps_brain.adapters.mem0 import looks_like_mem0
    from tapps_brain.io import import_memory_dicts

    if looks_like_mem0(data):
        from tapps_brain.adapters.mem0 import mem0_to_memory_dicts

        memory_dicts = mem0_to_memory_dicts(data)
        if dry_run:
            return {"would_import": len(memory_dicts), "file": str(input_file)}
        stats = import_memory_dicts(store, memory_dicts, overwrite=overwrite)
        return {
            "imported": stats["imported_count"],
            "skipped": stats["skipped_count"],
            "file": str(input_file),
        }
    if looks_like_letta_af(data):
        from tapps_brain.adapters.letta_af import letta_af_to_memory_dicts

        memory_dicts, warnings = letta_af_to_memory_dicts(data)
        if dry_run:
            return {
                "would_import": len(memory_dicts),
                "file": str(input_file),
                "warnings": warnings,
            }
        stats = import_memory_dicts(store, memory_dicts, overwrite=overwrite)
        return {
            "imported": stats["imported_count"],
            "skipped": stats["skipped_count"],
            "file": str(input_file),
            "warnings": warnings,
        }
    return None
