"""Profile-scoped learned data MCP tools (EPIC-075 / TAP-3163)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tapps_brain.mcp_server.context import ToolContext

from tapps_brain.services import kg_service, profile_data_service


def _bad_json_error(field: str, detail: str) -> dict[str, str]:
    return {"error": "bad_json", "field": field, "detail": detail}


def _parse_profile_value_json(
    value_json: str,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Parse ``value_json`` tool arg; return ``(parsed, error)``.

    Success and failure are discriminated out-of-band: a user payload that
    itself contains an ``"error"`` key (e.g. learned error statistics) is
    valid data, so the error signal cannot live inside the parsed object.
    """
    if not value_json or not value_json.strip():
        return None, {"error": "bad_request", "detail": "value_json is required."}
    try:
        parsed = json.loads(value_json)
    except json.JSONDecodeError as exc:
        return None, _bad_json_error("value_json", str(exc))
    if not isinstance(parsed, dict):
        return None, _bad_json_error(
            "value_json", f"expected JSON object, got {type(parsed).__name__}"
        )
    return parsed, None


def register_profile_tools(mcp: Any, ctx: ToolContext) -> None:  # noqa: ANN401
    """Register ``brain_profile_set`` and ``brain_profile_get`` on *mcp*."""
    _pid = ctx.pid

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_profile_set(profile: str, key: str, value_json: str) -> str:
        """Store profile-scoped learned data for the current project.

        Parameters
        ----------
        profile:
            Negotiated brain profile name (e.g. ``repo-brain``).
        key:
            Data key within the profile scope (e.g. ``domain_weights``).
        value_json:
            JSON object string to persist.

        Returns
        -------
        ``{"ok": true}`` on success.
        """
        project_id = _pid()
        prof = (profile or "").strip()
        data_key = (key or "").strip()
        if not prof:
            return json.dumps({"error": "bad_request", "detail": "profile is required."})
        if not data_key:
            return json.dumps({"error": "bad_request", "detail": "key is required."})
        parsed, parse_err = _parse_profile_value_json(value_json)
        if parse_err is not None or parsed is None:
            return json.dumps(parse_err, default=str)

        cm = kg_service._get_or_create_cm()
        if cm is None:
            return json.dumps(
                {"error": "db_unavailable", "detail": "TAPPS_BRAIN_DATABASE_URL is not set."}
            )

        result = profile_data_service.profile_data_set(
            cm,
            project_id,
            profile_name=prof,
            data_key=data_key,
            value_json=parsed,
        )
        return json.dumps(result, default=str)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_profile_get(profile: str, key: str) -> str:
        """Read profile-scoped learned data for the current project.

        Returns ``{"ok": true, "value_json": {...}}`` or ``{"ok": false}``.
        """
        project_id = _pid()
        prof = (profile or "").strip()
        data_key = (key or "").strip()
        if not prof:
            return json.dumps({"error": "bad_request", "detail": "profile is required."})
        if not data_key:
            return json.dumps({"error": "bad_request", "detail": "key is required."})

        cm = kg_service._get_or_create_cm()
        if cm is None:
            return json.dumps(
                {"error": "db_unavailable", "detail": "TAPPS_BRAIN_DATABASE_URL is not set."}
            )

        result = profile_data_service.profile_data_get(
            cm,
            project_id,
            profile_name=prof,
            data_key=data_key,
        )
        return json.dumps(result, default=str)
