"""Mem0 JSON inbound adapter — preserve mode (TAP-5033).

Maps Mem0-shaped exports into MemoryEntry-compatible dicts without LLM
re-derive. Optional inbound only; not a primary interchange format.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def looks_like_mem0(data: object) -> bool:
    """Heuristic: Mem0 export list/object with ``memory`` field (not ``value``)."""
    if isinstance(data, list):
        if not data:
            return False
        sample = next((x for x in data if isinstance(x, dict)), None)
        if sample is None:
            return False
        return "memory" in sample and "value" not in sample
    if isinstance(data, dict):
        if "results" in data and isinstance(data["results"], list):
            return looks_like_mem0(data["results"])
        if "memories" in data and isinstance(data["memories"], list):
            sample = next((x for x in data["memories"] if isinstance(x, dict)), None)
            return bool(sample and "memory" in sample and "value" not in sample)
    return False


def mem0_to_memory_dicts(data: object) -> list[dict[str, Any]]:
    """Convert Mem0-shaped JSON into MemoryEntry dicts (preserve mode)."""
    items: list[Any]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if isinstance(data.get("results"), list):
            items = data["results"]
        elif isinstance(data.get("memories"), list):
            items = data["memories"]
        else:
            items = [data]
    else:
        return []

    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("memory") or item.get("text") or item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        raw_id = item.get("id") or item.get("hash") or item.get("key")
        key = str(raw_id).strip() if raw_id is not None else ""
        if not key:
            key = f"mem0-{len(out) + 1}"
        # Sanitize to slug-ish key
        key = key.replace(" ", "-")[:128]
        categories = item.get("categories") or item.get("tags") or []
        tags = [str(c) for c in categories] if isinstance(categories, list) else []
        created = (
            item.get("created_at")
            or item.get("created")
            or item.get("updated_at")
            or datetime.now(tz=UTC).isoformat()
        )
        out.append(
            {
                "key": key,
                "value": content.strip(),
                "tier": "pattern",
                "source": "system",
                "source_agent": "mem0-import",
                "scope": "project",
                "tags": tags,
                "confidence": float(item.get("score", item.get("confidence", 0.8))),
                "created_at": str(created),
                "updated_at": str(item.get("updated_at") or created),
            }
        )
    return out
