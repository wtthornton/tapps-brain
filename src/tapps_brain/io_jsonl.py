"""JSONL streaming export/import helpers (TAP-5034)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from tapps_brain import __version__
from tapps_brain.io_limits import (
    NATIVE_FORMAT,
    NATIVE_FORMAT_VERSION,
    enforce_import_limit,
    resolve_max_import_entries,
)

if TYPE_CHECKING:
    from tapps_brain.models import MemoryEntry

# ---------------------------------------------------------------------------


def build_jsonl_export(
    entries: list[MemoryEntry],
    *,
    source_project: str = "",
    exported_at: str | None = None,
) -> str:
    """Build JSONL: line 1 meta envelope, then one MemoryEntry JSON per line."""
    at = exported_at or datetime.now(tz=UTC).isoformat()
    meta = {
        "format": NATIVE_FORMAT,
        "format_version": NATIVE_FORMAT_VERSION,
        "encoding": "jsonl",
        "exported_at": at,
        "source_project": source_project,
        "entry_count": len(entries),
        "tapps_version": __version__,
    }
    lines = [json.dumps(meta, default=str)]
    for entry in entries:
        lines.append(json.dumps(entry.model_dump(mode="json"), default=str))
    return "\n".join(lines) + "\n"


def parse_jsonl_payload(
    text: str,
    *,
    max_entries: int | None = None,
) -> dict[str, Any]:
    """Parse JSONL export (meta line + entry lines) into import memories."""
    limit = resolve_max_import_entries(max_entries)
    memories: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            msg = f"Invalid JSON on line {line_no}: {exc}"
            raise ValueError(msg) from exc
        if not isinstance(obj, dict):
            continue
        # Skip meta / envelope lines
        if obj.get("encoding") == "jsonl" or (
            obj.get("format") == NATIVE_FORMAT and "key" not in obj and "memories" not in obj
        ):
            continue
        if "key" in obj and "value" in obj:
            memories.append(obj)
            continue
        if isinstance(obj.get("memories"), list):
            for m in obj["memories"]:
                if isinstance(m, dict):
                    memories.append(m)
    enforce_import_limit(len(memories), limit)
    return {
        "memories": memories,
        "relations": None,
        "embeddings": None,
        "detected_format": "jsonl",
    }


# ---------------------------------------------------------------------------
