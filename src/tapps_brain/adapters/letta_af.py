"""Letta Agent File (.af) inbound adapter — preserve mode (TAP-5033).

Maps core memory blocks into MemoryEntry dicts. Archival passages and
tool/prompt runtime sections are skipped with warnings (not a crash).
Optional inbound only; not a primary interchange format.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def looks_like_letta_af(data: object) -> bool:
    """Heuristic for Letta .af JSON (agent file with core memory blocks)."""
    if not isinstance(data, dict):
        return False
    if data.get("type") in {"agent", "letta_agent", "agent_file"}:
        return True
    if "agent_type" in data and ("memory" in data or "blocks" in data):
        return True
    if isinstance(data.get("core_memory"), (dict, list)):
        return True
    return isinstance(data.get("memory"), dict) and (
        "blocks" in data["memory"] or "persona" in data["memory"] or "human" in data["memory"]
    )


def letta_af_to_memory_dicts(data: object) -> tuple[list[dict[str, Any]], list[str]]:
    """Convert Letta .af core memory into MemoryEntry dicts.

    Returns ``(entries, warnings)``. Archival passages are skipped.
    """
    warnings: list[str] = []
    if not isinstance(data, dict):
        return [], ["letta_af: expected a JSON object"]

    out: list[dict[str, Any]] = []
    now = datetime.now(tz=UTC).isoformat()

    # Skip archival passages explicitly
    archival = data.get("archival_memory") or data.get("archival_passages") or data.get("passages")
    if archival:
        count = len(archival) if isinstance(archival, list) else 1
        warnings.append(
            f"letta_af: skipped {count} archival passage(s) (preserve core blocks only)"
        )

    blocks = _extract_core_blocks(data)
    if not blocks:
        warnings.append("letta_af: no core memory blocks found")
        return out, warnings

    for label, value in blocks:
        if not value.strip():
            continue
        key = f"letta-{_slug(label)}"
        out.append(
            {
                "key": key,
                "value": value.strip(),
                "tier": "pattern",
                "source": "system",
                "source_agent": "letta-af-import",
                "scope": "project",
                "tags": ["letta", "core-memory", label],
                "confidence": 0.9,
                "created_at": now,
                "updated_at": now,
                "agent_scope": "private",
            }
        )
    return out, warnings


def _extract_core_blocks(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Pull persona/human/core blocks from common .af layouts."""
    blocks: list[tuple[str, str]] = []

    memory = data.get("memory")
    if isinstance(memory, dict):
        for label in ("persona", "human", "system"):
            val = memory.get(label)
            if isinstance(val, str) and val.strip():
                blocks.append((label, val))
        nested_blocks = memory.get("blocks")
        if isinstance(nested_blocks, list):
            blocks.extend(_blocks_from_list(nested_blocks))
        elif isinstance(nested_blocks, dict):
            for label, val in nested_blocks.items():
                if isinstance(val, str) and val.strip():
                    blocks.append((str(label), val))
                elif isinstance(val, dict) and isinstance(val.get("value"), str):
                    blocks.append((str(label), val["value"]))

    core = data.get("core_memory")
    if isinstance(core, dict):
        for label, val in core.items():
            if isinstance(val, str) and val.strip():
                blocks.append((str(label), val))
            elif isinstance(val, dict) and isinstance(val.get("value"), str):
                blocks.append((str(label), val["value"]))
    elif isinstance(core, list):
        blocks.extend(_blocks_from_list(core))

    top_blocks = data.get("blocks")
    if isinstance(top_blocks, list):
        blocks.extend(_blocks_from_list(top_blocks))

    # Deduplicate by label (first wins)
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for label, value in blocks:
        if label in seen:
            continue
        seen.add(label)
        deduped.append((label, value))
    return deduped


def _blocks_from_list(items: list[Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or item.get("id") or "block")
        value = item.get("value") or item.get("text") or item.get("content")
        if isinstance(value, str) and value.strip():
            out.append((label, value))
    return out


def _slug(label: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label.lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-_")[:64] or "block"
