"""OpenAPI contract builder for the tapps-brain HTTP adapter (TAP-508).

The spec is generated from FastAPI's auto-discovered routes and then
enriched with the cross-cutting concerns that aren't visible in the
route signatures: dual auth schemes, the standard tenant headers
(``X-Project-Id``, ``X-Tapps-Agent``, ``X-Idempotency-Key``), the error
envelope shape, and the ASGI-mounted ``/mcp`` route.

The brain version is read from ``importlib.metadata.version("tapps-brain")``
so the published spec is always tagged against the running adapter.

Usage::

    from tapps_brain.openapi_contract import build_openapi_spec
    spec = build_openapi_spec(app)

The HTTP adapter wires this in by overriding ``app.openapi``; the same
function is used by ``scripts/snapshot_openapi.py`` to write the
checked-in copies under ``docs/contracts/``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from fastapi.openapi.utils import get_openapi

from tapps_brain.postgres_migrations import discover_private_migrations

if TYPE_CHECKING:
    from fastapi import FastAPI


# Routes that require the data-plane bearer token.
_DATA_PLANE_PREFIXES = ("/v1/", "/snapshot", "/info")
# Public scrape endpoints under /v1/ — no bearerAuth / tenant headers.
_PUBLIC_V1_PATHS = frozenset({"/v1/tools/list", "/v1/skill"})
# Routes that require the admin bearer token.
_ADMIN_PREFIXES = ("/admin/",)
# Routes that take ``X-Project-Id`` / ``X-Agent-Id`` tenant headers.
_TENANT_HEADER_PREFIXES = ("/v1/", "/snapshot", "/mcp")
# Routes whose handlers implement X-Idempotency-Key check/replay/save
# (every http_adapter route that calls ``_get_ikey_and_istore``).  An explicit
# set — the old prefix heuristic plus a blanket ``:batch`` suffix rule both
# omitted implemented routes (/v1/forget, /v1/learn_*, /v1/experience) and
# wrongly advertised the header on /v1/recall:batch, which never reads it.
_IDEMPOTENCY_PATHS = frozenset(
    {
        "/v1/remember",
        "/v1/remember:batch",
        "/v1/reinforce",
        "/v1/reinforce:batch",
        "/v1/forget",
        "/v1/learn_success",
        "/v1/learn_failure",
        "/v1/experience",
        "/v1/experience:batch",
    }
)


def _service_version() -> str:
    """Return the installed brain version, or ``"unknown"`` if not packaged."""
    try:
        return version("tapps-brain")
    except PackageNotFoundError:
        return "unknown"


def _bundled_schema_version() -> int:
    """Return the max bundled private-schema migration version.

    Used as the ``schema_version`` field in ``/info`` and as the spec's
    ``info.x-schema-version``.  This is the *built-in* version, not the
    DB's live applied version — clients that need the live version should
    hit ``/ready``.
    """
    try:
        migs = discover_private_migrations()
        if not migs:
            return 0
        return max(v for v, _, _ in migs)
    except Exception:
        return 0


def _security_schemes() -> dict[str, Any]:
    return {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "Data-plane bearer token.  Set ``TAPPS_BRAIN_AUTH_TOKEN`` "
                "(or per-tenant token via ``TAPPS_BRAIN_PER_TENANT_AUTH=1``) "
                "to enable.  When unset, protected routes are open "
                "(not-for-production)."
            ),
        },
        "adminBearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "Admin bearer token for ``/admin/*`` routes.  Set "
                "``TAPPS_BRAIN_ADMIN_TOKEN``; when unset, ``/admin/*`` "
                "returns 503."
            ),
        },
    }


def _common_parameters() -> dict[str, Any]:
    return {
        "XProjectId": {
            "name": "X-Project-Id",
            "in": "header",
            "required": True,
            "schema": {"type": "string", "minLength": 1},
            "description": (
                "Tenant identity (ADR-010).  Required on every data-plane "
                "and MCP request; the brain isolates state by "
                "``(project_id, agent_id)``."
            ),
        },
        "XAgentId": {
            "name": "X-Agent-Id",
            "in": "header",
            "required": False,
            "schema": {"type": "string", "default": "unknown"},
            "description": (
                'Per-call agent identity.  Defaults to ``"unknown"`` when '
                "omitted.  Threaded into RLS as ``app.agent_id``."
            ),
        },
        "XTappsAgent": {
            "name": "X-Tapps-Agent",
            "in": "header",
            "required": False,
            "schema": {"type": "string"},
            "description": (
                "Per-call agent identity (STORY-070.7).  When set, takes "
                "precedence over ``X-Agent-Id`` for tenant routing on both "
                "REST and MCP.  The HTTP SDK sends this header."
            ),
        },
        "XIdempotencyKey": {
            "name": "X-Idempotency-Key",
            "in": "header",
            "required": False,
            "schema": {"type": "string", "format": "uuid"},
            "description": (
                "Idempotency key (UUID).  When ``TAPPS_BRAIN_IDEMPOTENCY=1`` "
                "a duplicate key within 24 h replays the original response "
                "with header ``Idempotency-Replayed: true``."
            ),
        },
    }


def _error_envelope_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {
                "type": "string",
                "description": (
                    "Taxonomy code (see ``tapps_brain.errors``).  Stable "
                    "wire identifier — clients map this to retry policy."
                ),
                "examples": [
                    "bad_request",
                    "unauthorized",
                    "forbidden",
                    "payload_too_large",
                    "store_unavailable",
                    "admin_disabled",
                ],
            },
            "detail": {
                "type": "string",
                "description": "Human-readable explanation; not machine-stable.",
            },
            "retry_after": {
                "type": "number",
                "description": ("Seconds to wait before retry, present on rate-limited responses."),
            },
        },
    }


def _info_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["service", "version", "schema_version", "build"],
        "properties": {
            "service": {"type": "string"},
            "version": {
                "type": "string",
                "description": "Brain code version (PEP 440).",
            },
            "schema_version": {
                "type": "integer",
                "description": "Bundled private-schema migration version.",
            },
            "build": {
                "type": "string",
                "description": (
                    "Build identifier from ``TAPPS_BRAIN_BUILD`` env var; "
                    '``"unknown"`` outside of a built image.'
                ),
            },
            "python": {"type": "string"},
            "platform": {"type": "string"},
            "uptime_seconds": {"type": "number"},
            "auth_enabled": {"type": "boolean"},
            "dsn_configured": {"type": "boolean"},
        },
    }


def _memory_version_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["key", "version"],
        "properties": {
            "key": {"type": "string", "description": "Memory key, exactly as returned."},
            "version": {
                "type": "string",
                "description": "First 16 hex chars of the SHA-256 of the recalled value.",
            },
        },
    }


def _recall_response_schema() -> dict[str, Any]:
    """The TAP-6583 provenance fields on ``/v1/recall``'s 200 response.

    The route hand-builds its ``JSONResponse`` (no ``response_model``), so
    FastAPI's auto-schema leaves the 200 response empty; this documents the
    two additive fields explicitly. ``results`` / ``query`` stay documented
    in the route's docstring, same as every other ``/v1/*`` route here.
    """
    return {
        "type": "object",
        "properties": {
            "recall_digest": {
                "type": "string",
                "pattern": "^([0-9a-f]{64})?$",
                "description": (
                    "SHA-256 hex digest over the sorted (key, version) pairs in "
                    "`results` (TAP-6583). Empty string when nothing matched."
                ),
            },
            "memory_versions": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/MemoryVersion"},
                "description": (
                    "The (key, version) pairs `recall_digest` covers, in result order."
                ),
            },
        },
    }


def _mcp_path_definition() -> dict[str, Any]:
    return {
        "post": {
            "summary": "MCP Streamable-HTTP transport",
            "operationId": "mcpStreamableHttp",
            "description": (
                "Single endpoint for the MCP Streamable-HTTP transport "
                "(MCP spec 2025-03-26).  Tools are advertised via the "
                "``tools/list`` JSON-RPC method; invoke them with "
                "``tools/call``.  See the FastMCP client or "
                "``tapps_brain.client`` for typed wrappers.\n\n"
                "Mounted as an ASGI sub-app so this path is not visible "
                "in the route table; the contract is documented here "
                "explicitly to lock the public path."
            ),
            "security": [{"bearerAuth": []}],
            "parameters": [
                {"$ref": "#/components/parameters/XProjectId"},
                {"$ref": "#/components/parameters/XAgentId"},
                {"$ref": "#/components/parameters/XTappsAgent"},
                {"$ref": "#/components/parameters/XIdempotencyKey"},
            ],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"type": "object"},
                        "description": "MCP JSON-RPC envelope.",
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "MCP JSON-RPC response.",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                "401": {
                    "description": "Missing/malformed bearer or X-Project-Id.",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                    },
                },
                "403": {
                    "description": "Forbidden Origin or invalid token.",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                    },
                },
            },
        }
    }


def _path_needs(prefixes: tuple[str, ...], path: str) -> bool:
    return any(path.startswith(p) for p in prefixes)


def _enrich_path(path: str, methods: dict[str, Any]) -> None:
    """Mutate *methods* (a path item) in-place to add headers, security,
    and the standard error responses."""
    is_public_v1 = path in _PUBLIC_V1_PATHS
    is_data_plane = _path_needs(_DATA_PLANE_PREFIXES, path) and not is_public_v1
    is_admin = _path_needs(_ADMIN_PREFIXES, path)
    needs_tenant_headers = _path_needs(_TENANT_HEADER_PREFIXES, path) and not is_public_v1
    needs_idempotency = path in _IDEMPOTENCY_PATHS

    for verb, op in methods.items():
        if verb in ("parameters", "summary", "description"):
            continue
        if not isinstance(op, dict):
            continue

        # Security
        if is_admin:
            op["security"] = [{"adminBearerAuth": []}]
        elif is_data_plane:
            op["security"] = [{"bearerAuth": []}]
        elif is_public_v1:
            op["security"] = []

        # Parameters: append references to common headers if missing.
        params = op.setdefault("parameters", [])
        existing_refs = {p.get("$ref") for p in params if isinstance(p, dict) and "$ref" in p}
        existing_names = {p.get("name") for p in params if isinstance(p, dict) and "name" in p}

        wanted: list[tuple[str, str]] = []
        if needs_tenant_headers:
            wanted.extend(
                [
                    ("#/components/parameters/XProjectId", "X-Project-Id"),
                    ("#/components/parameters/XAgentId", "X-Agent-Id"),
                    ("#/components/parameters/XTappsAgent", "X-Tapps-Agent"),
                ]
            )
        if needs_idempotency:
            wanted.append(("#/components/parameters/XIdempotencyKey", "X-Idempotency-Key"))
        for ref, name in wanted:
            if ref not in existing_refs and name not in existing_names:
                params.append({"$ref": ref})

        # Standard error responses for protected routes.
        responses = op.setdefault("responses", {})
        if is_data_plane or is_admin:
            for code, summary in (
                ("401", "Missing/malformed Authorization header."),
                ("403", "Invalid token."),
            ):
                if code not in responses:
                    responses[code] = {
                        "description": summary,
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                        },
                    }


def build_openapi_spec(app: FastAPI) -> dict[str, Any]:
    """Build the enriched OpenAPI spec for *app*.

    Calls FastAPI's ``get_openapi`` to discover all registered routes,
    then injects the dual security schemes, standard tenant headers, the
    error envelope schema, the ``/mcp`` mounted route, and the
    ``schema_version`` extension.
    """
    version = _service_version()
    spec = get_openapi(
        title="tapps-brain HTTP API",
        version=version,
        description=(
            "HTTP + MCP contract for the tapps-brain memory service "
            "(ADR-007 Postgres-only, ADR-010 multi-tenant project_id).\n\n"
            "**Transport.**  REST data plane (``/v1/*``, ``/snapshot``, "
            "``/info``) and admin plane (``/admin/*``) use bearer-token "
            "auth; the MCP transport at ``/mcp`` uses the same data-plane "
            "token plus ``X-Project-Id``.\n\n"
            "**Tenancy.**  Every data-plane and MCP request must carry "
            "``X-Project-Id`` (ADR-010); ``X-Agent-Id`` is optional and "
            'defaults to ``"unknown"``.\n\n'
            "**Errors.**  All error responses share the envelope "
            '``{"error": <taxonomy_code>, "detail": <text>}``; see '
            "``tapps_brain.errors`` for the canonical code list."
        ),
        routes=app.routes,
    )

    spec["info"]["x-schema-version"] = _bundled_schema_version()

    components = spec.setdefault("components", {})
    components.setdefault("securitySchemes", {}).update(_security_schemes())
    components.setdefault("parameters", {}).update(_common_parameters())
    schemas = components.setdefault("schemas", {})
    schemas.setdefault("Error", _error_envelope_schema())
    schemas.setdefault("Info", _info_response_schema())
    schemas.setdefault("MemoryVersion", _memory_version_schema())
    schemas.setdefault("RecallResponse", _recall_response_schema())

    paths = spec.setdefault("paths", {})
    for path, methods in list(paths.items()):
        if isinstance(methods, dict):
            _enrich_path(path, methods)

    # Document the ASGI-mounted /mcp route — invisible to FastAPI's introspection.
    paths.setdefault("/mcp", _mcp_path_definition())

    # Routes that hand-build JSONResponse with non-200 statuses don't get
    # those statuses into the auto-generated spec.  Inject the contractual
    # 503s explicitly so consumers can rely on them.
    for route_path, method in (("/snapshot", "get"), ("/ready", "get")):
        op = paths.get(route_path, {}).get(method)
        if isinstance(op, dict):
            op.setdefault("responses", {}).setdefault(
                "503",
                {
                    "description": (
                        "Degraded — store unavailable (snapshot) or DB unreachable (ready)."
                    ),
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                    },
                },
            )

    # /v1/reinforce returns 404 when the key is missing (service ``not_found``).
    reinforce_op = paths.get("/v1/reinforce", {}).get("post")
    if isinstance(reinforce_op, dict):
        reinforce_op.setdefault("responses", {}).setdefault(
            "404",
            {
                "description": "Memory key not found for this tenant/agent.",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
            },
        )

    # Wire the Info schema into /info if FastAPI generated a generic 200.
    info_path = paths.get("/info")
    if isinstance(info_path, dict):
        get_op = info_path.get("get")
        if isinstance(get_op, dict):
            responses = get_op.setdefault("responses", {})
            ok = responses.setdefault("200", {"description": "Runtime info."})
            ok.setdefault(
                "content",
                {"application/json": {"schema": {"$ref": "#/components/schemas/Info"}}},
            )

    # /v1/recall hand-builds its JSONResponse (no response_model), so FastAPI
    # leaves its 200 schema empty (``{}``). Wire the TAP-6583 provenance
    # fields in explicitly — round 2, MCP payload already carries them.
    # Assigned directly (not setdefault): FastAPI's auto-schema already
    # populates "content" with an empty ``schema: {}``, so a setdefault here
    # would silently no-op, same as the dangling Info schema above.
    recall_op = paths.get("/v1/recall", {}).get("post")
    if isinstance(recall_op, dict):
        ok = recall_op.setdefault("responses", {}).setdefault(
            "200", {"description": "Successful Response"}
        )
        ok["content"] = {
            "application/json": {"schema": {"$ref": "#/components/schemas/RecallResponse"}}
        }

    return spec
