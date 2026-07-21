"""MIF v2 memory interchange helpers (TAP-5031)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from tapps_brain import __version__

if TYPE_CHECKING:
    from tapps_brain.models import MemoryEntry

MIF_FORMAT = "mif"
MIF_VERSION = "2"

_TIER_TO_MIF_TYPE: dict[str, str] = {
    "architectural": "semantic",
    "pattern": "semantic",
    "procedural": "procedural",
    "context": "episodic",
    "ephemeral": "episodic",
    "session": "episodic",
}
_MIF_TYPE_TO_TIER: dict[str, str] = {
    "semantic": "pattern",
    "procedural": "procedural",
    "episodic": "context",
    "working": "context",
}

# ---------------------------------------------------------------------------


def entry_to_mif_unit(entry: MemoryEntry) -> dict[str, Any]:
    """Map a MemoryEntry to a MIF v2 Level-1 JSON-LD unit + extensions.tapps."""
    tier = str(entry.tier)
    memory_type = _TIER_TO_MIF_TYPE.get(tier, "semantic")
    unit_id = f"urn:mif:{uuid.uuid5(uuid.NAMESPACE_URL, entry.key)}"
    created = entry.created_at
    return {
        "@context": "https://mif-spec.dev/schema/context.jsonld",
        "@type": "Memory",
        "@id": unit_id,
        "memoryType": memory_type,
        "content": entry.value,
        "created": created,
        "modified": entry.updated_at,
        "tags": list(entry.tags),
        "extensions": {
            "tapps": {
                "key": entry.key,
                "tier": tier,
                "scope": entry.scope.value if hasattr(entry.scope, "value") else str(entry.scope),
                "confidence": entry.confidence,
                "agent_scope": entry.agent_scope,
                "memory_group": entry.memory_group,
                "source": entry.source.value
                if hasattr(entry.source, "value")
                else str(entry.source),
                "source_agent": entry.source_agent,
            }
        },
    }


def build_mif_document(
    entries: list[MemoryEntry],
    *,
    source_project: str = "",
    exported_at: str | None = None,
) -> dict[str, Any]:
    """Build a MIF v2 document wrapping memory units."""
    at = exported_at or datetime.now(tz=UTC).isoformat()
    return {
        "format": MIF_FORMAT,
        "mif_version": MIF_VERSION,
        "exported_at": at,
        "source_project": source_project,
        "entry_count": len(entries),
        "tapps_version": __version__,
        "memories": [entry_to_mif_unit(e) for e in entries],
    }


def is_mif_document(data: dict[str, Any]) -> bool:
    if data.get("format") == MIF_FORMAT or data.get("mif_version") is not None:
        return True
    if data.get("@type") == "Memory" or "memoryType" in data:
        return True
    memories = data.get("memories")
    return isinstance(memories, list) and looks_like_mif_list(memories)


def looks_like_mif_list(items: list[Any]) -> bool:
    dicts = [i for i in items if isinstance(i, dict)]
    if not dicts:
        return False
    sample = dicts[0]
    return (
        "memoryType" in sample
        or sample.get("@type") == "Memory"
        or ("content" in sample and "created" in sample and "key" not in sample)
    )


def mif_unit_to_memory_dict(unit: dict[str, Any]) -> dict[str, Any] | None:
    content = unit.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    ext = unit.get("extensions") or unit.get("vendor") or {}
    tapps = {}
    if isinstance(ext, dict):
        raw_tapps = ext.get("tapps") or ext.get("vendor.tapps") or {}
        if isinstance(raw_tapps, dict):
            tapps = raw_tapps
    key = tapps.get("key")
    if not isinstance(key, str) or not key.strip():
        # Derive a stable key from @id / id
        raw_id = unit.get("@id") or unit.get("id") or ""
        key = str(raw_id).removeprefix("urn:mif:")[:128] or f"mif-{uuid.uuid4().hex[:12]}"
    memory_type = str(unit.get("memoryType") or unit.get("type") or "semantic")
    tier = tapps.get("tier") or _MIF_TYPE_TO_TIER.get(memory_type, "pattern")
    created = unit.get("created") or unit.get("created_at") or datetime.now(tz=UTC).isoformat()
    result: dict[str, Any] = {
        "key": key,
        "value": content,
        "tier": tier,
        "confidence": tapps.get("confidence", -1.0),
        "source": tapps.get("source", "system"),
        "source_agent": tapps.get("source_agent", "mif-import"),
        "scope": tapps.get("scope", "project"),
        "tags": unit.get("tags") if isinstance(unit.get("tags"), list) else [],
        "created_at": created,
        "updated_at": unit.get("modified") or created,
        "agent_scope": tapps.get("agent_scope", "private"),
        "memory_group": tapps.get("memory_group"),
    }
    return result


# ---------------------------------------------------------------------------
