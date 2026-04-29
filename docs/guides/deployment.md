# tapps-brain Deployment Guide

This guide covers deploying tapps-brain as a **shared networked service** —
one container that speaks both the HTTP data-plane and the MCP Streamable-HTTP
transport — consumable by AgentForge workers, Claude Code sessions, and any
agent wired via `AGENT.md`.

> **STORY-070.15** — Unified binary model (v3.6+).  A single `tapps-brain`
> container replaces the previous `tapps-brain-http` + `tapps-brain-operator-mcp`
> two-container layout.  See [Migration 3.5 → 3.6](migration-3.5-to-3.6.md) and
> [Migration 3.6 → 3.7](migration-3.6-to-3.7.md) if you are upgrading.

> **v3.7.0+** — `TAPPS_BRAIN_ADMIN_TOKEN` is **required** when the operator MCP
> transport is enabled (`TAPPS_BRAIN_MCP_HTTP_PORT > 0`, the default).
> Container refuses to start without it. See [Migration 3.6 → 3.7](migration-3.6-to-3.7.md).

> **ADR-010 / EPIC-069** — The brain's project registry is fail-closed.
> Every `/mcp` request must carry an `X-Project-Id` header naming a project
> registered via `POST /admin/projects`. One-time setup per deployment —
> see [Migration 3.6 → 3.7](migration-3.6-to-3.7.md) for the exact command.

---

## Quick Start (Docker Compose)

```bash
# 1. Copy the env template and fill in strong random values
cp docker/.env.example docker/.env
# Edit docker/.env — set the 4 REQUIRED vars (commands inline in the file):
#   TAPPS_BRAIN_DB_PASSWORD       — Postgres owner-role password (bootstrap only)
#   TAPPS_BRAIN_RUNTIME_PASSWORD  — tapps_runtime DML-only role password
#   TAPPS_BRAIN_AUTH_TOKEN        — public bearer token for /mcp/ + /v1/*
#   TAPPS_BRAIN_ADMIN_TOKEN       — admin bearer token for operator MCP :8090

# 2. Build + start the unified stack (Postgres + migrate sidecar + brain + dashboard)
make hive-deploy
# (equivalent: docker compose -p tapps-brain -f docker/docker-compose.hive.yaml \
#              --env-file docker/.env up -d --build)

# 3. Verify all containers are healthy
docker compose -p tapps-brain -f docker/docker-compose.hive.yaml ps
# Expected: tapps-brain-http (healthy), tapps-brain-db (healthy),
#           tapps-brain-migrate (exited 0), tapps-visual (up)

# 4. Smoke-test the HTTP data-plane
curl http://localhost:8080/health
# {"status":"ok","service":"tapps-brain","version":"..."}

# 5. Smoke-test the operator MCP transport (loopback-only by default)
curl -H "Authorization: Bearer $TAPPS_BRAIN_ADMIN_TOKEN" http://127.0.0.1:8090/health
# ok
```

The migrate sidecar (`tapps-brain-migrate`) runs once as the DB owner role, applies schema migrations, creates the least-privilege `tapps_runtime` role with the password from `docker/.env`, and exits. The brain container then starts and connects as `tapps_runtime` — no `ALLOW_PRIVILEGED_ROLE` override, RLS + tenant isolation guards stay on.

---

## Port Map

| Port | Transport | Purpose |
|------|-----------|---------|
| `8080` | HTTP (FastAPI/Uvicorn) | Data-plane, `/mcp`, `/admin/*`, `/health`, `/metrics` |
| `8090` | Streamable-HTTP (FastMCP) | Operator MCP tools (GC, consolidation, relay, migration) |

Both ports are configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `TAPPS_BRAIN_HTTP_PORT` | `8080` | HTTP data-plane port |
| `TAPPS_BRAIN_MCP_HTTP_PORT` | `8090` | Operator MCP transport port (`0` = disabled) |
| `TAPPS_BRAIN_HTTP_HOST` | `0.0.0.0` | HTTP bind address |
| `TAPPS_BRAIN_MCP_HOST` | `0.0.0.0` | MCP bind address |

---

## Shared-Service Pattern

The unified binary model (`tapps-brain serve`) starts both transports in one
process.  This means:

- **One container** to deploy, monitor, and scale.
- **One image** to build and tag.
- **One healthcheck** that reports unhealthy only when *either* transport fails.
- No inter-container coordination required; both transports share in-process
  state (store, Hive connection pool, signal handlers).

```
┌──────────────────────────────────────────────────┐
│  tapps-brain  container                          │
│                                                  │
│  tapps-brain serve                               │
│  ├── Thread: uvicorn (HTTP FastAPI app)  :8080   │
│  │   ├── GET /health                             │
│  │   ├── GET /ready                              │
│  │   ├── GET /metrics                            │
│  │   ├── POST /memory                            │
│  │   └── /mcp  (agent MCP via FastMCP mount)     │
│  │                                               │
│  └── Thread: FastMCP (streamable-http)   :8090   │
│      └── /mcp/  (operator MCP tools)            │
└──────────────────────────────────────────────────┘
```

### Graceful Shutdown

Sending `SIGTERM` (Docker default on `docker stop`) or `SIGINT` stops both
transports cleanly:

1. HTTP adapter (`uvicorn.Server.should_exit = True`) — drains in-flight
   requests before stopping.
2. MCP thread — joined with a 5-second timeout; FastMCP daemon thread exits
   when the process terminates.

---

## AgentForge Integration

Wire the MCP transport into an AgentForge worker's `AGENT.md`:

```yaml
# agentforge-worker/AGENT.md  (partial)

mcp_servers:
  tapps-brain-mcp:
    type: streamable-http
    url: "http://tapps-brain:8090/mcp/"
    # Optional bearer token (TAPPS_BRAIN_ADMIN_TOKEN)
    headers:
      Authorization: "Bearer ${TAPPS_BRAIN_ADMIN_TOKEN}"
```

Or, for the **standard agent MCP** (data-plane `/mcp` — no operator tools):

```yaml
mcp_servers:
  tapps-brain:
    type: streamable-http
    url: "http://tapps-brain:8080/mcp"
    headers:
      Authorization: "Bearer ${TAPPS_BRAIN_AUTH_TOKEN}"
```

### AgentForge Client Snippet

```python
# agentforge_worker/brain_client.py
import httpx

BRAIN_BASE = "http://tapps-brain:8080"
AUTH_TOKEN = os.environ["TAPPS_BRAIN_AUTH_TOKEN"]

def remember(project_id: str, agent_id: str, key: str, value: str) -> None:
    resp = httpx.post(
        f"{BRAIN_BASE}/memory",
        json={"project_id": project_id, "agent_id": agent_id,
              "key": key, "value": value},
        headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        timeout=10,
    )
    resp.raise_for_status()

def recall(project_id: str, agent_id: str, query: str) -> list[dict]:
    resp = httpx.get(
        f"{BRAIN_BASE}/memory/search",
        params={"project_id": project_id, "agent_id": agent_id, "q": query},
        headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["results"]
```

---

## AGENT.md Example

Paste this into the agent's `AGENT.md` to grant read/write memory access via
the standard MCP transport (no operator tools):

```markdown
## MCP Servers

### tapps-brain (shared memory)

- **Type:** streamable-http
- **URL:** `http://tapps-brain:8080/mcp`
- **Auth:** Bearer token via `TAPPS_BRAIN_AUTH_TOKEN`
- **Tools available:** memory_save, memory_recall, memory_search, hive_push,
  hive_search, brain_remember, brain_recall, brain_forget, feedback_rate
```

To grant operator access (GC, consolidation, migration), point to port `8090`
and use `TAPPS_BRAIN_ADMIN_TOKEN` instead.

---

## Running Without Docker

For local development or bare-metal deployments:

```bash
# Install with all server extras
pip install 'tapps-brain[cli,mcp,http]'

# Export required environment variables — single DSN (Hive inherits).
# In production, this DSN should point at the DML-only `tapps_runtime` role.
export TAPPS_BRAIN_DATABASE_URL="postgresql://tapps_runtime:secret@localhost:5432/tapps_brain"
export TAPPS_BRAIN_AUTH_TOKEN="my-auth-token"
export TAPPS_BRAIN_ADMIN_TOKEN="my-admin-token"

# Start both transports in one process
tapps-brain serve \
  --host 0.0.0.0 --port 8080 \
  --mcp-host 0.0.0.0 --mcp-port 8090

# HTTP only (legacy / simpler setup)
tapps-brain serve --host 0.0.0.0 --port 8080
# or: TAPPS_BRAIN_MCP_HTTP_PORT=0 tapps-brain serve
```

---

## Healthcheck

The Dockerfile `HEALTHCHECK` probes both transports; the compose
`healthcheck.test` mirrors this logic:

```
HTTP :8080/health  →  {"status":"ok"}
MCP  :8090/mcp/   →  200 OK
```

If `TAPPS_BRAIN_MCP_HTTP_PORT` is unset or `0`, only the HTTP health endpoint
is probed (MCP transport is disabled).

Use the `/health` endpoint to build your own readiness probe:

```bash
# Kubernetes liveness probe
livenessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 15
  periodSeconds: 10

# Kubernetes readiness probe (checks Postgres connectivity too)
readinessProbe:
  httpGet:
    path: /ready
    port: 8080
  initialDelaySeconds: 15
  periodSeconds: 10
```

---

## Async-Native Write Path (TAPPS_BRAIN_ASYNC_NATIVE)

tapps-brain supports an opt-in async-native Postgres write path for deployments
where HTTP handler concurrency is the bottleneck.

| Variable | Default | Purpose |
|---|---|---|
| `TAPPS_BRAIN_ASYNC_NATIVE` | `0` | Set to `1` to route `AsyncMemoryStore` writes through `AsyncPostgresPrivateBackend` (native async pool) instead of `asyncio.to_thread`. |

**What changes when enabled:**
- `/v1/remember`, `/v1/forget`, `/v1/learn_success`, `/v1/learn_failure` write to
  Postgres without occupying a thread-pool slot for the I/O wait.
- Thread pool threads are released after in-memory cache updates (~0.1 ms) rather
  than after the full Postgres round-trip (~5–20 ms).
- Read paths, reinforce, and batch endpoints are unaffected (still use `to_thread`).

**When to enable:**
- You are running 50+ concurrent agents and observing thread pool saturation
  (high `asyncio.to_thread` queue depth, increased p95 latency under load).
- You have confirmed the flag's behavior against your workload using
  `tests/benchmarks/load_smoke_postgres.py::test_load_smoke_async_comparison`.

**Migration note:**
`TAPPS_BRAIN_ASYNC_NATIVE=0` (the `asyncio.to_thread` path) will be deprecated in the
next minor release. Set `TAPPS_BRAIN_ASYNC_NATIVE=1` now to opt in early and report
issues before the flag is removed. See `docs/engineering/async-performance.md` for
benchmark methodology and result templates.

**Known limitations (EPIC-072):**
- `save_relations` and `append_audit` are no-ops on the async-native path;
  these will be wired in a follow-up story.

---

## Security Considerations

- The HTTP data-plane (`8080`) is protected by `TAPPS_BRAIN_AUTH_TOKEN`.
- The operator MCP transport (`8090`) is protected by `TAPPS_BRAIN_ADMIN_TOKEN`.
- **Port `8090` is bound to `127.0.0.1` by default** in `docker-compose.hive.yaml`
  (TAP-551).  It is not reachable from the internet without setting
  `TAPPS_OPERATOR_MCP_BIND=0.0.0.0` — pair that with a firewall rule or VPN.
  See [Hive Deployment — Operator MCP Port](hive-deployment.md#operator-mcp-port-8090--loopback-only-by-default-tap-551).
- Use TLS termination (nginx, Caddy, cloud load balancer) in front of both
  ports for any external-facing deployment.  See `hive-tls.md`.

---

## See Also

- [Migration 3.5 → 3.6](migration-3.5-to-3.6.md) — upgrading from the
  two-container layout
- [Hive Deployment](hive-deployment.md) — PostgreSQL Hive setup details
- [AgentForge Integration](agentforge-integration.md) — end-to-end wiring
- [HTTP Adapter API](http-adapter.md) — data-plane endpoint reference
- [Hive TLS](hive-tls.md) — TLS termination in front of the stack
