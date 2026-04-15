# tapps-brain Deployment Guide

This guide covers deploying tapps-brain as a **shared networked service** —
one container that speaks both the HTTP data-plane and the MCP Streamable-HTTP
transport — consumable by AgentForge workers, Claude Code sessions, and any
agent wired via `AGENT.md`.

> **STORY-070.15** — Unified binary model (v3.6+).  A single `tapps-brain`
> container replaces the previous `tapps-brain-http` + `tapps-brain-operator-mcp`
> two-container layout.  See [Migration 3.5 → 3.6](migration-3.5-to-3.6.md) if
> you are upgrading from an older stack.

---

## Quick Start (Docker Compose)

```bash
# 1. Create secrets directory and files
mkdir -p docker/secrets
echo "your-secure-hive-password"   > docker/secrets/tapps_hive_password.txt
echo "your-secure-http-auth-token" > docker/secrets/tapps_http_auth_token.txt

# 2. (Optional) copy and edit the env file
cp docker/.env.example docker/.env
# Edit docker/.env — set TAPPS_BRAIN_DATABASE_URL, TAPPS_BRAIN_AUTH_TOKEN, etc.

# 3. Start the stack
docker compose -f docker/docker-compose.hive.yaml up -d

# 4. Verify both transports are healthy
docker compose -f docker/docker-compose.hive.yaml ps
# Expected: tapps-brain (healthy), tapps-hive-db (healthy)

# 5. Smoke-test the HTTP data-plane
curl http://localhost:8080/health
# {"status":"ok",...}

# 6. Smoke-test the operator MCP transport
curl http://localhost:8090/mcp/
# MCP Streamable-HTTP endpoint ready
```

The migration sidecar (`tapps-hive-migrate`) runs once, applies all pending
Hive schema migrations, then exits.  The database container stays running.

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

# Export required environment variables
export TAPPS_BRAIN_DATABASE_URL="postgresql://tapps:secret@localhost:5432/tapps_brain"
export TAPPS_BRAIN_HIVE_DSN="postgresql://tapps:secret@localhost:5432/tapps_hive"
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

## Security Considerations

- The HTTP data-plane (`8080`) is protected by `TAPPS_BRAIN_AUTH_TOKEN`.
- The operator MCP transport (`8090`) is protected by `TAPPS_BRAIN_ADMIN_TOKEN`.
- **Do not expose port `8090` to the internet** — it grants GC, consolidation,
  and migration capabilities.
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
