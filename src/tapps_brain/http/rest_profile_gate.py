"""REST endpoint → MCP tool-name mapping for X-Brain-Profile gating (TAP-1929).

The MCP transport honors :data:`tapps_brain.mcp_server.REQUEST_PROFILE` via the
``ListToolsMiddleware`` / call-tool interceptors.  Until now the REST surface
under ``/v1/*`` was profile-agnostic — an HTTP consumer with
``X-Brain-Profile: agent_brain`` could still POST to ``/v1/reinforce`` (a
``memory_*``-backed endpoint excluded from the ``agent_brain`` surface)
without any server-side check.

This module owns the canonical map from REST path → tool name so that
:class:`tapps_brain.http.middleware.RestProfileGateMiddleware` can refuse
out-of-profile REST calls with the same wire shape used by the MCP path
(``-32602`` JSON-RPC error with ``data.reason="out_of_profile"``).

Drift detection: :func:`validate_rest_route_map` is called at startup against
the bundled :class:`~tapps_brain.mcp_server.profile_registry.ProfileRegistry`
known-tool set.  If any mapped tool name is not in the catalog, the server
refuses to start so misconfiguration cannot reach production.
"""

from __future__ import annotations

from typing import Any

# Canonical REST path → MCP tool name mapping.
#
# Keys are exact request paths (including the ":batch" suffix for batch
# endpoints).  Values are the tool names declared in
# ``tapps_brain.mcp_server.mcp_profiles.yaml``.  When the resolved profile
# does not include the mapped tool, the middleware returns 403.
REST_ROUTE_TO_TOOL: dict[str, str] = {
    # AgentBrain facade (TAP-993)
    "/v1/remember": "brain_remember",
    "/v1/recall": "brain_recall",
    "/v1/forget": "brain_forget",
    "/v1/learn_success": "brain_learn_success",
    "/v1/learn_failure": "brain_learn_failure",
    # Memory layer
    "/v1/reinforce": "memory_reinforce",
    "/v1/remember:batch": "memory_save_many",
    "/v1/recall:batch": "memory_recall_many",
    "/v1/reinforce:batch": "memory_reinforce_many",
    # Knowledge graph (EPIC-076)
    "/v1/experience": "brain_record_event",
    "/v1/experience:batch": "brain_record_event",  # TAP-1934 — same tool
    "/v1/kg/neighbors": "brain_get_neighbors",
    "/v1/kg/explain": "brain_explain_connection",
    "/v1/kg/feedback": "brain_record_feedback",
}

# Paths that bypass profile gating entirely.  Mirrors the
# ``_ORIGIN_EXEMPT_PATHS`` allowlist in ``http_adapter`` plus the v1 catalog
# read endpoint (which gets per-request profile filtering instead).
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/",
        "/health",
        "/healthz",
        "/ready",
        "/metrics",
        "/v1/tools/list",
    }
)


def resolve_tool_for_path(path: str) -> str | None:
    """Return the tool name a REST path maps to, or ``None`` for unmapped paths.

    Used by :class:`tapps_brain.http.middleware.RestProfileGateMiddleware` to
    decide whether to gate the call.  Returns ``None`` for non-``/v1/*`` paths
    and unmapped ``/v1/*`` paths (admin routes, future endpoints) — those are
    not gated by this middleware.
    """
    return REST_ROUTE_TO_TOOL.get(path)


def validate_rest_route_map(known_tools: frozenset[str]) -> None:
    """Validate every mapped tool name exists in *known_tools*.

    Mirrors :meth:`~tapps_brain.mcp_server.profile_registry.ProfileRegistry.validate_against`
    for the REST surface.  Called once at startup.

    Raises
    ------
    ValueError
        When any value in :data:`REST_ROUTE_TO_TOOL` is not present in
        *known_tools*.  The message lists every offending path → tool mapping.
    """
    missing: list[str] = []
    for route, tool in REST_ROUTE_TO_TOOL.items():
        if tool not in known_tools:
            missing.append(f"  {route!r} → {tool!r}")
    if missing:
        raise ValueError(
            "REST route drift detected — update REST_ROUTE_TO_TOOL or "
            "mcp_profiles.yaml so the following routes map to a known tool:\n" + "\n".join(missing)
        )


def out_of_profile_response_body(
    *,
    tool: str,
    profile: str | None,
    detail: str | None = None,
) -> dict[str, Any]:
    """Build the documented JSON-RPC ``-32602`` error body for REST denials.

    Mirrors the MCP wire shape from TAP-1619 so HTTP consumers see consistent
    error structure across transports.  HTTP status code is the caller's
    responsibility (typically 403).
    """
    return {
        "error": "out_of_profile",
        "detail": detail
        or (
            f"Tool {tool!r} is not in the resolved profile {profile!r}. "
            "Set X-Brain-Profile to a profile that includes this tool, "
            "or use a different endpoint."
        ),
        "data": {
            "reason": "out_of_profile",
            "tool": tool,
            "profile": profile,
        },
    }
