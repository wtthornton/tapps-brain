# Remote MCP integration (Streamable HTTP)

This guide describes how remote agents (AgentForge, OpenClaw, etc.) connect to
a deployed tapps-brain over the MCP **Streamable HTTP** transport introduced
in EPIC-070. For the local in-process stdio transport, see `mcp.md`.

## Why Streamable HTTP

Stdio transport requires one `docker run -i tapps-brain-mcp` subprocess per
client session. That doesn't scale for remote agents: every connection pays a
container-cold-start cost, there is no shared embedding cache or DB pool, and
you can't front it with a load balancer. Streamable HTTP keeps one long-lived
FastAPI/Uvicorn process serving many concurrent agents over a single
`POST /mcp` endpoint with the exact same JSON-RPC envelope and tool surface as
stdio -- transport parity is guaranteed by `tests/test_http_mcp_parity.py`.

## Endpoint and headers

- **Endpoint:** `POST /mcp` on the brain (default `http://<host>:8080/mcp`).
- **Required headers:**
  - `Authorization: Bearer <TAPPS_BRAIN_AUTH_TOKEN>`
  - `X-Project-Id: <project-slug>` -- tenant selector (see below).
  - `X-Agent-Id: <agent-id>` -- identifies the calling agent for audit / rate
    limiting.
- **Optional headers:**
  - `Mcp-Session-Id: <uuid>` -- set by stateful clients to resume a session.
  - `Origin: <origin>` -- required when called from a browser; must be in
    `TAPPS_BRAIN_ALLOWED_ORIGINS` (DNS-rebinding protection per MCP spec).

## Auth model

Two independent bearer tokens:

| Token                       | Scope                         |
|-----------------------------|-------------------------------|
| `TAPPS_BRAIN_AUTH_TOKEN`    | Data plane + `/mcp`           |
| `TAPPS_BRAIN_ADMIN_TOKEN`   | `/admin/*` (tenancy ops etc.) |

Tokens are accepted **only** in the `Authorization` header -- never in query
strings. If `TAPPS_BRAIN_ADMIN_TOKEN` is unset, `/admin/*` returns 503 rather
than silently reusing the data-plane token.

## Tenant resolution

`X-Project-Id` is resolved against the Postgres project registry introduced in
EPIC-069 / ADR-010. The filesystem `profile.yaml` path is deprecated for
deployed brains -- every request must carry an on-wire project id, and the
registry row decides which profile/limits apply. Unknown project ids return
`403` with `ProjectNotRegisteredError`.

## Session lifecycle

FastMCP Streamable HTTP is **stateless by default**: each request is
self-contained and the brain does not persist per-session state between calls.
Clients that need a sticky session (for long-running subscriptions, SSE
notifications, etc.) can set `Mcp-Session-Id` to a stable UUID; the server's
`session_manager` will reuse the same session object while that id is live.
Because `session_manager` is per-process, the container runs **one Uvicorn
worker**; scale horizontally with more replicas behind a load balancer that
pins `Mcp-Session-Id` via a hash header.

## Error handling

- **JSON-RPC errors** (tool-level, validation, etc.) are returned inside the
  standard JSON-RPC `error` envelope with HTTP `200`.
- **Transport-level errors** map to HTTP status codes:
  - `401` -- missing or malformed `Authorization`.
  - `403` -- invalid token, unknown project, or disallowed `Origin`.
  - `429` -- rate limit exceeded (see `rate_limiter.py`).
  - `503` -- admin token not configured, or readiness check failing.

## Migration from stdio `docker run`

**Before** -- one container per session, no pooling:

```bash
# AgentForge spawned this per conversation.
docker run --rm -i \
  -e TAPPS_BRAIN_DATABASE_URL=... \
  tapps-brain-mcp
```

**After** -- one long-lived container, many agents:

```bash
# Operator brings up the brain once:
docker compose -f docker/docker-compose.hive.yaml up -d

# Every agent points at the same URL:
export TAPPS_BRAIN_URL=http://brain.internal:8080/mcp
export TAPPS_BRAIN_AUTH_TOKEN=...
python examples/agentforge-client.py
```

The client-side change is swapping
`mcp.client.stdio.stdio_client` for
`mcp.client.streamable_http.streamablehttp_client` and passing
`Authorization` + `X-Project-Id` + `X-Agent-Id` headers.

## Example client

See [`examples/agentforge-client.py`](../../examples/agentforge-client.py)
for a minimal end-to-end round trip (`initialize` -> `memory_save` ->
`memory_search` -> `memory_recall` -> close).

## Rate limits and observability

- **Rate limits** are enforced per `(project_id, agent_id)` by
  `src/tapps_brain/rate_limiter.py`. Exceeded quotas return HTTP `429` with
  a `Retry-After` header.
- **Tracing** -- the adapter honors the W3C `traceparent` header and emits
  server spans via `src/tapps_brain/otel_tracer.py`. Install the `[otel]`
  extra and set the standard `OTEL_EXPORTER_OTLP_*` env vars to ship spans to
  your collector.
- **Logs** are structured JSON via `structlog`; each request line includes
  `project_id`, `agent_id`, `mcp_session_id`, and the JSON-RPC `method`.
