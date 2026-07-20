"""Shared helpers for service modules (EPIC-070 STORY-070.1)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

_MAX_CONFIDENCE_BOOST: float = 0.2


def validate_iso_timestamp(field_name: str, raw: str) -> dict[str, str] | None:
    """Return a bad_request envelope when *raw* is non-empty and not ISO-8601."""
    if not raw or not str(raw).strip():
        return None
    try:
        datetime.fromisoformat(str(raw).strip())
    except ValueError:
        detail = f"{field_name} must be a valid ISO-8601 timestamp, got {raw!r}"
        return {"error": "bad_request", "detail": detail, "message": detail}
    return None


def parse_details_json(details_json: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """Parse optional JSON object for *details_json* parameters.

    Returns ``(dict, None)`` on success or ``(None, error_message)`` on failure.
    """
    if details_json is None or not str(details_json).strip():
        return {}, None
    try:
        data = json.loads(details_json)
    except json.JSONDecodeError as exc:
        return None, f"invalid_details_json: {exc}"
    if not isinstance(data, dict):
        return None, "details_json must be a JSON object"
    return data, None
