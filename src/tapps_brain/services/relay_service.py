"""Relay service functions (EPIC-070 STORY-070.1)."""

from __future__ import annotations

import json
from typing import Any


def tapps_brain_relay_export(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    source_agent: str,
    items_json: str,
) -> dict[str, Any]:
    from tapps_brain.memory_relay import RELAY_VERSION, build_relay_json

    try:
        parsed = json.loads(items_json)
    except json.JSONDecodeError as exc:
        return {"error": "invalid_json", "message": str(exc)}

    if not isinstance(parsed, list):
        return {"error": "invalid_format", "message": "items_json must be a JSON array"}

    for i, row in enumerate(parsed):
        if not isinstance(row, dict):
            return {
                "error": "invalid_item",
                "message": f"items[{i}] must be an object",
            }

    payload = build_relay_json(
        source_agent=source_agent,
        items=parsed,
        relay_version=RELAY_VERSION,
    )
    return {
        "relay_version": RELAY_VERSION,
        "payload": payload,
        "item_count": len(parsed),
    }
