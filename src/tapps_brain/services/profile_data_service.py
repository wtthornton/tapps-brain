"""Profile-scoped learned data KV service (EPIC-075 / TAP-3163)."""

from __future__ import annotations

import json
from typing import Any


def profile_data_set(
    cm: Any,
    project_id: str,
    *,
    profile_name: str,
    data_key: str,
    value_json: dict[str, Any],
) -> dict[str, Any]:
    """Upsert learned KV for ``(project_id, profile_name, data_key)``."""
    prof = (profile_name or "").strip()
    key = (data_key or "").strip()
    if not prof:
        return {"error": "bad_request", "detail": "profile_name is required."}
    if not key:
        return {"error": "bad_request", "detail": "data_key is required."}
    if not isinstance(value_json, dict):
        return {"error": "bad_request", "detail": "value_json must be a JSON object."}

    sql = """
        INSERT INTO profile_scoped_data (project_id, profile_name, data_key, value_json)
        VALUES (%s, %s, %s, %s::jsonb)
        ON CONFLICT (project_id, profile_name, data_key)
        DO UPDATE SET value_json = EXCLUDED.value_json, updated_at = now()
    """
    with cm.project_context(project_id) as conn, conn.cursor() as cur:
        cur.execute(sql, (project_id, prof, key, json.dumps(value_json)))
        conn.commit()
    return {"ok": True}


def profile_data_get(
    cm: Any,
    project_id: str,
    *,
    profile_name: str,
    data_key: str,
) -> dict[str, Any]:
    """Read learned KV for ``(project_id, profile_name, data_key)``."""
    prof = (profile_name or "").strip()
    key = (data_key or "").strip()
    if not prof:
        return {"error": "bad_request", "detail": "profile_name is required."}
    if not key:
        return {"error": "bad_request", "detail": "data_key is required."}

    sql = """
        SELECT value_json
        FROM profile_scoped_data
        WHERE project_id = %s AND profile_name = %s AND data_key = %s
    """
    with cm.project_context(project_id) as conn, conn.cursor() as cur:
        cur.execute(sql, (project_id, prof, key))
        row = cur.fetchone()

    if row is None:
        return {"ok": False}
    payload = row[0] if isinstance(row[0], dict) else {}
    return {"ok": True, "value_json": payload}
