"""ASGI middleware for the tapps-brain HTTP adapter (TAP-604).

Extracted from ``tapps_brain.http_adapter``.  Four middleware classes:

* :class:`OtelSpanMiddleware`        — W3C traceparent extraction + OTel server span.
* :class:`OriginAllowlistMiddleware` — DNS-rebinding guard (TAP-627).
* :class:`RestProfileGateMiddleware` — ``/v1/*`` capability-profile gate (TAP-1929).
* :class:`McpTenantMiddleware`       — ``/mcp`` tenant envelope enforcement.

Classes that need settings or OTel helpers resolve them via a lazy
``import tapps_brain.http_adapter`` inside their ``dispatch`` methods so
that unit tests patching ``tapps_brain.http_adapter.get_settings`` and
``tapps_brain.http_adapter.start_span`` continue to work unchanged.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

try:
    from fastapi import Request, Response  # noqa: TC002
    from fastapi.responses import JSONResponse
    from starlette.middleware.base import BaseHTTPMiddleware
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "tapps_brain.http.middleware requires the [http] extra.  "
        "Install it with:  uv sync --extra http  (or --extra all)."
    ) from exc

from tapps_brain.http.metrics_collector import _record_labeled_request

logger = structlog.get_logger(__name__)

MCP_AUTH_MODEL = "global_bearer"
MCP_AUTH_EXPECTED_ENV = "TAPPS_BRAIN_AUTH_TOKEN"


def _peek_mcp_tool_name(body_bytes: bytes) -> str | None:
    """Return ``params.name`` from a JSON-RPC ``tools/call`` body, else ``None``.

    Best-effort and side-effect-free — any parse error, batch body, or
    non-tool-call method yields ``None``.  Used only on auth-rejection paths
    to enrich the error envelope, so callers don't need to replay the body.
    """
    if not body_bytes:
        return None
    try:
        payload: Any = json.loads(body_bytes)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("method") != "tools/call":
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    name = params.get("name")
    return name if isinstance(name, str) and name else None


def _mcp_auth_error_body(
    detail: str,
    *,
    error: str,
    project_id: str | None,
    tool: str | None,
) -> dict[str, Any]:
    """Build the enriched JSON body for an /mcp auth failure.

    Always includes ``auth_model`` and ``expected_env`` so clients (e.g.
    tapps-mcp ``auth_probe`` / ``tapps_doctor``) can surface the real
    remediation instead of a bare HTTP status line.  ``tool`` and
    ``project_id`` are best-effort diagnostics and omitted when unknown.
    """
    body: dict[str, Any] = {
        "error": error,
        "detail": detail,
        "auth_model": MCP_AUTH_MODEL,
        "expected_env": MCP_AUTH_EXPECTED_ENV,
    }
    if tool is not None:
        body["tool"] = tool
    if project_id is not None:
        body["project_id"] = project_id
    return body


# Paths that are intentionally unauthenticated and Origin-agnostic (TAP-627).
# These are probe / scrape endpoints that must remain reachable from any origin
# (load-balancer health checks, Prometheus scrapers, etc.) and do not accept
# bearer tokens that a DNS-rebinding attacker could steal.
_ORIGIN_EXEMPT_PATHS: frozenset[str] = frozenset(
    {"/", "/health", "/healthz", "/ready", "/metrics", "/v1/tools/list", "/v1/skill"}
)


def _resolve_tenant_headers(request: Request) -> tuple[str, str, str | None, str | None]:
    """Extract and resolve ``project_id``, ``agent_id``, ``scope``, ``group``.

    ``X-Tapps-Agent`` takes precedence over ``X-Agent-Id`` (STORY-070.7).

    Returns a 4-tuple ``(project_id, agent_id, scope, group)`` where
    ``agent_id`` defaults to ``"unknown"`` and ``scope``/``group`` are
    ``None`` when absent.
    """
    project_id = (request.headers.get("x-project-id") or "").strip()
    agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
    tapps_agent = (request.headers.get("x-tapps-agent") or "").strip()
    if tapps_agent:
        agent_id = tapps_agent
    scope: str | None = (request.headers.get("x-tapps-scope") or "").strip() or None
    group: str | None = (request.headers.get("x-tapps-group") or "").strip() or None
    return project_id, agent_id, scope, group


# Anonymous placeholders Ruling 9 forbids under strict identity: the implicit
# ``_resolve_tenant_headers`` default ("unknown") and the literal string a
# caller might send when it has no real identity wired up yet ("default").
_ANONYMOUS_AGENT_IDS = frozenset({"unknown", "default"})


def strict_identity_refusal(agent_id: str) -> dict[str, Any] | None:
    """Refusal envelope for a write under ``TAPPS_BRAIN_STRICT_IDENTITY=1`` (VAL-25-flag).

    Returns ``None`` (no refusal) when the flag is off — the default — or
    when *agent_id* is a real logical name. Callers wrap a non-``None``
    result in a 400 response; this function does no I/O and raises nothing,
    so it is safe to call unconditionally at the top of a write handler.
    """
    from tapps_brain.http.settings import is_strict_identity_enabled

    if not is_strict_identity_enabled():
        return None
    if agent_id not in _ANONYMOUS_AGENT_IDS:
        return None
    detail = (
        "TAPPS_BRAIN_STRICT_IDENTITY is enabled: writes must resolve to a real "
        "agent identity, not 'unknown' or 'default'. Set X-Agent-Id (or the "
        "higher-precedence X-Tapps-Agent) to a stable logical name."
    )
    return {"error": "identity_required", "detail": detail, "message": detail, "agent_id": agent_id}


async def _check_mcp_auth(request: Request, auth_token: str | None) -> JSONResponse | None:
    """Verify the bearer token for ``/mcp`` requests.

    Returns a ``JSONResponse`` error when auth fails, or ``None`` when the
    check passes (token matches or auth is not configured).  Rejection
    bodies include ``auth_model``/``expected_env`` and — best-effort —
    ``tool`` and ``project_id`` so clients can self-diagnose without a
    round-trip to the server logs.
    """
    if not auth_token:
        from tapps_brain.http.settings import is_strict_mode

        if is_strict_mode():
            return JSONResponse(
                status_code=503,
                content={
                    "error": "service_unavailable",
                    "detail": (
                        "MCP authentication is required but TAPPS_BRAIN_AUTH_TOKEN is unset."
                    ),
                },
            )
        return None
    # Lazy import avoids circular dep; gets the patched version in tests.
    import hmac

    import tapps_brain.http_adapter as _http_mod

    tok = _http_mod._extract_bearer(request)
    project_id = (request.headers.get("x-project-id") or "").strip() or None
    if tok is None or tok == "":
        tool = _peek_mcp_tool_name(await request.body())
        logger.warning(
            "mcp_auth.missing_bearer",
            project_id=project_id,
            tool=tool,
            has_authorization_header=request.headers.get("authorization") is not None,
        )
        return JSONResponse(
            status_code=401,
            content=_mcp_auth_error_body(
                "Bearer token required for /mcp.",
                error="unauthorized",
                project_id=project_id,
                tool=tool,
            ),
        )
    # TAP-544: constant-time comparison for the /mcp bearer-token check.
    if not hmac.compare_digest(tok.encode("utf-8"), auth_token.encode("utf-8")):
        tool = _peek_mcp_tool_name(await request.body())
        logger.warning(
            "mcp_auth.bearer_mismatch",
            project_id=project_id,
            tool=tool,
        )
        return JSONResponse(
            status_code=403,
            content=_mcp_auth_error_body(
                "Invalid token.",
                error="forbidden",
                project_id=project_id,
                tool=tool,
            ),
        )
    return None


def _resolve_mcp_profile(
    request: Request,
    project_id: str,
    agent_id: str,
) -> tuple[str | None, JSONResponse | None]:
    """Resolve the per-request MCP profile from headers / agent registry.

    Returns ``(resolved_profile, None)`` on success or ``(None, error_response)``
    when the ``X-Brain-Profile`` header names an unknown profile.
    """
    # Lazy import so profile_resolver module doesn't need to import http_adapter.
    from tapps_brain.http.profile_resolver import _get_profile_resolver
    from tapps_brain.mcp_server.profile_registry import UnknownProfileError

    header_profile: str | None = (request.headers.get("x-brain-profile") or "").strip() or None
    if header_profile is not None:
        try:
            resolver = _get_profile_resolver()
            resolver._registry.get(header_profile)
        except UnknownProfileError as exc:
            return None, JSONResponse(
                status_code=400,
                content={
                    "error": "bad_request",
                    "detail": f"Unknown MCP profile {header_profile!r}.",
                    "available": exc.available,
                },
            )
    resolved_profile: str | None = _get_profile_resolver().resolve(
        project_id=project_id,
        agent_id=agent_id,
        header_profile=header_profile,
    )
    return resolved_profile, None


class OtelSpanMiddleware(BaseHTTPMiddleware):
    """Wrap each request in an OTel server span with W3C traceparent extraction."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:  # noqa: ANN401
        # Lazy imports keep http_adapter patchable in tests.
        import tapps_brain.http_adapter as _http_mod

        start_span = _http_mod.start_span  # type: ignore[attr-defined]
        extract_trace_context = _http_mod.extract_trace_context  # type: ignore[attr-defined]
        SPAN_KIND_SERVER = _http_mod.SPAN_KIND_SERVER  # type: ignore[attr-defined]  # noqa: N806

        carrier: dict[str, str] = {}
        tp = request.headers.get("traceparent")
        if tp:
            carrier["traceparent"] = tp
        ts = request.headers.get("tracestate")
        if ts:
            carrier["tracestate"] = ts
        trace_ctx = extract_trace_context(carrier) if carrier else None
        method = request.method
        path = request.url.path
        project_id = request.headers.get("x-project-id", "")
        # STORY-070.7: tag spans with per-call agent identity so observability
        # can filter by tenant x agent without inspecting headers downstream.
        agent_id_header = request.headers.get("x-tapps-agent") or request.headers.get(
            "x-agent-id", ""
        )
        with start_span(
            f"{method} {path}",
            {
                "http.method": method,
                "http.route": path,
                "tapps.project_id": project_id,
                "tapps.agent_id": agent_id_header,
            },
            kind=SPAN_KIND_SERVER,
            context=trace_ctx,
        ):
            return await call_next(request)  # type: ignore[no-any-return]


class OriginAllowlistMiddleware(BaseHTTPMiddleware):
    """DNS-rebinding guard applied to every bearer-authenticated route (TAP-627).

    When ``TAPPS_BRAIN_ALLOWED_ORIGINS`` is set, any browser-originated request
    (``Origin`` header present) whose origin is not in the allowlist receives a
    ``403 Forbidden`` response before the handler is reached.

    Intentionally exempt (unauthenticated probe / scrape endpoints that are
    Origin-agnostic by design):

    * ``/`` — root liveness check
    * ``/health`` — liveness probe
    * ``/healthz`` — readiness probe (DB-checked; used by Docker healthcheck)
    * ``/ready`` — readiness probe
    * ``/metrics`` — Prometheus scrape endpoint
    * ``/v1/tools/list`` — static tool-catalog snapshot (TAP-1843; no secrets)
    * ``/v1/skill`` — version-matched SKILL.md for HTTP-only consumers

    Previously only ``/mcp`` was guarded (STORY-070.3/4).  TAP-627 extends
    protection to all bearer-authenticated routes (``/v1/*``, ``/admin/*``,
    ``/mcp``, ``/info``, etc.) so that DNS-rebinding attacks against REST
    endpoints are also blocked.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:  # noqa: ANN401
        # Lazy import so tests can patch tapps_brain.http_adapter.get_settings.
        import tapps_brain.http_adapter as _http_mod

        cfg = _http_mod.get_settings()
        if cfg.allowed_origins and request.url.path not in _ORIGIN_EXEMPT_PATHS:
            origin = request.headers.get("origin", "")
            if origin and origin not in cfg.allowed_origins:
                return JSONResponse(
                    status_code=403,
                    content={"error": "forbidden", "detail": f"Origin '{origin}' not allowed."},
                )
        return await call_next(request)  # type: ignore[no-any-return]


class RestProfileGateMiddleware(BaseHTTPMiddleware):
    """X-Brain-Profile enforcement for ``/v1/*`` REST endpoints (TAP-1929).

    Until v3.19, the ``X-Brain-Profile`` header gated only the MCP tool list
    / call interceptors.  An HTTP consumer running with
    ``X-Brain-Profile: agent_brain`` could still POST to ``/v1/forget`` (a
    ``memory_*`` endpoint outside the agent_brain profile) without any
    server-side check — the profile boundary was documentation, not
    enforcement.

    This middleware:

    * Resolves the per-request profile (header → agent registry → server
      default), reusing the same :class:`tapps_brain.mcp_server.profile_resolver.ProfileResolver`
      singleton the MCP transport uses.
    * Maps the request path to a tool name via
      :data:`tapps_brain.http.rest_profile_gate.REST_ROUTE_TO_TOOL`.
    * Returns ``403`` with the documented JSON-RPC ``-32602`` error shape
      when the resolved profile does not include the mapped tool.

    Paths in :data:`tapps_brain.http.rest_profile_gate.PUBLIC_PATHS`
    (probes, scrape endpoints, the static ``/v1/tools/list`` snapshot)
    bypass this middleware entirely.  Unmapped ``/v1/*`` paths (admin
    routes, future endpoints) also pass through — they are gated by
    bearer-token auth in their own dependencies.

    When the ``X-Brain-Profile`` header is absent, the server's default
    profile applies (typically ``"full"``), so behaviour is backward
    compatible with pre-1929 callers that never set the header.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:  # noqa: ANN401, PLR0911
        path = request.url.path
        if not path.startswith("/v1/"):
            return await call_next(request)  # type: ignore[no-any-return]

        from tapps_brain.http.rest_profile_gate import (
            PUBLIC_PATHS,
            out_of_profile_response_body,
            resolve_tool_for_path,
            tools_for_path,
        )

        if path in PUBLIC_PATHS:
            return await call_next(request)  # type: ignore[no-any-return]

        tool = resolve_tool_for_path(path)
        allowed_for_route = tools_for_path(path)
        if tool is None or allowed_for_route is None:
            # Unmapped /v1/* path — let it through (admin / future routes).
            return await call_next(request)  # type: ignore[no-any-return]

        # Profile resolution mirrors McpTenantMiddleware / _resolve_tenant_headers.
        project_id = (request.headers.get("x-project-id") or "").strip()
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        tapps_agent = (request.headers.get("x-tapps-agent") or "").strip()
        if tapps_agent:
            agent_id = tapps_agent

        from tapps_brain.http.profile_resolver import _get_profile_resolver
        from tapps_brain.mcp_server.profile_registry import UnknownProfileError

        header_profile: str | None = (request.headers.get("x-brain-profile") or "").strip() or None
        if header_profile is not None:
            try:
                resolver = _get_profile_resolver()
                resolver._registry.get(header_profile)
            except UnknownProfileError as exc:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "bad_request",
                        "detail": f"Unknown MCP profile {header_profile!r}.",
                        "available": exc.available,
                    },
                )

        resolved_profile: str = _get_profile_resolver().resolve(
            project_id=project_id,
            agent_id=agent_id,
            header_profile=header_profile,
        )

        try:
            allowed_tools = _get_profile_resolver()._registry.get(resolved_profile)
        except UnknownProfileError as exc:
            # Server default points at an unknown profile — refuse rather than
            # fail open (spoofed / misconfigured profiles must not unlock tools).
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "detail": f"Unknown MCP profile {resolved_profile!r}.",
                    "available": exc.available,
                },
            )

        if allowed_for_route.isdisjoint(allowed_tools):
            # TAP-1972: hint the smallest profile that exposes the tool so
            # consumers (`tapps doctor`, operator scripts) can surface
            # "switch to profile X" without re-parsing the YAML.
            suggested = _get_profile_resolver()._registry.suggest_profile_for_tool(
                tool, exclude=resolved_profile
            )
            logger.info(
                "rest_profile_gate.denied",
                path=path,
                tool=tool,
                profile=resolved_profile,
                project_id=project_id,
                agent_id=agent_id,
                suggested_profile=suggested,
            )
            # TAP-6696 / VAL-10: same denied_profile outcome the MCP call-tool
            # interceptor records, so a REST-triggered denial shows up in
            # /metrics too (previously this branch only logged, never metriced).
            from tapps_brain.mcp_server.tool_filter import record_denied_profile_call

            record_denied_profile_call(profile=resolved_profile, tool=tool)
            return JSONResponse(
                status_code=403,
                content=out_of_profile_response_body(
                    tool=tool,
                    profile=resolved_profile,
                    suggested_profile=suggested,
                ),
            )

        # Bridge into mcp_server contextvars so downstream handlers can read
        # request.state.brain_profile / REQUEST_PROFILE if they care.
        from tapps_brain import mcp_server as _mcp_mod

        token_profile = _mcp_mod.REQUEST_PROFILE.set(resolved_profile)
        request.state.brain_profile = resolved_profile
        try:
            return await call_next(request)  # type: ignore[no-any-return]
        finally:
            _mcp_mod.REQUEST_PROFILE.reset(token_profile)


class McpTenantMiddleware(BaseHTTPMiddleware):
    """Enforce the MCP wire envelope for ``/mcp``:

    * ``X-Project-Id`` required (400 on miss).
    * ``X-Agent-Id`` optional (defaults to ``"unknown"``).
    * ``Authorization: Bearer <TAPPS_BRAIN_AUTH_TOKEN>``.
    * Sets contextvars consumed by :mod:`tapps_brain.mcp_server`.

    The ``dispatch`` method is refactored to CC ≤ 10 by delegating each
    concern to a focused private helper:

    * :func:`_check_mcp_auth` — bearer-token verification.
    * :func:`_resolve_tenant_headers` — header extraction.
    * :func:`_resolve_mcp_profile` — per-request profile resolution.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:  # noqa: ANN401
        if not request.url.path.startswith("/mcp"):
            return await call_next(request)  # type: ignore[no-any-return]

        # Lazy import so tests can patch tapps_brain.http_adapter.get_settings.
        import tapps_brain.http_adapter as _http_mod

        cfg = _http_mod.get_settings()

        # --- Auth ---
        auth_err = await _check_mcp_auth(request, cfg.auth_token)
        if auth_err is not None:
            return auth_err

        # --- Tenant headers ---
        project_id, agent_id, scope, group = _resolve_tenant_headers(request)
        if not project_id:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "bad_request",
                    "detail": "X-Project-Id header is required for /mcp requests.",
                },
            )

        # --- Profile resolution (STORY-073.2) ---
        resolved_profile, profile_err = _resolve_mcp_profile(request, project_id, agent_id)
        if profile_err is not None:
            return profile_err

        # Bridge into tapps_brain.mcp_server contextvars so the service layer
        # sees the per-request tenant regardless of whether the JSON-RPC
        # envelope also carries ``_meta.project_id``.
        from tapps_brain import mcp_server as _mcp_mod

        token_pid = _mcp_mod.REQUEST_PROJECT_ID.set(project_id)
        token_agent = _mcp_mod.REQUEST_AGENT_ID.set(agent_id)
        token_scope = _mcp_mod.REQUEST_SCOPE.set(scope)
        token_group = _mcp_mod.REQUEST_GROUP.set(group)
        token_profile = _mcp_mod.REQUEST_PROFILE.set(resolved_profile)
        # Also mirror into request.state for handlers / observability.
        request.state.project_id = project_id
        request.state.agent_id = agent_id
        request.state.scope = scope
        request.state.group = group
        request.state.brain_profile = resolved_profile
        # STORY-070.12: track per-(project_id, agent_id) request counts.
        _record_labeled_request(project_id, agent_id)
        try:
            return await call_next(request)  # type: ignore[no-any-return]
        finally:
            _mcp_mod.REQUEST_PROJECT_ID.reset(token_pid)
            _mcp_mod.REQUEST_AGENT_ID.reset(token_agent)
            _mcp_mod.REQUEST_SCOPE.reset(token_scope)
            _mcp_mod.REQUEST_GROUP.reset(token_group)
            _mcp_mod.REQUEST_PROFILE.reset(token_profile)
