# Migration Guide: tapps-brain 3.5.x → 3.6

This guide covers the breaking changes and migration steps required when
upgrading from tapps-brain **3.5.x** (two-container deployment) to **3.6**
(unified one-container deployment, STORY-070.15).

---

## What Changed

### Unified container (STORY-070.15)

| 3.5.x | 3.6 |
|-------|-----|
| Two containers: `tapps-brain-http` (port 8080) + `tapps-brain-operator-mcp` (port 8090) | One container: `tapps-brain` (ports 8080 + 8090) |
| Started with `tapps-brain-http` script | Started with `tapps-brain serve` |
| Operator MCP required a separate `command:` override | Operator MCP enabled via `TAPPS_BRAIN_MCP_HTTP_PORT=8090` |
| `tapps-visual` depended on `tapps-brain-http` | `tapps-visual` now depends on `tapps-brain` |

### Docker Compose service name change

The `tapps-brain-http` and `tapps-brain-operator-mcp` services in
`docker-compose.hive.yaml` have been merged into a single `tapps-brain`
service.

If you reference the old service names in:
- `docker compose exec tapps-brain-http ...`
- `docker compose logs tapps-brain-http`
- Kubernetes Helm charts or Compose overrides

…update them to `tapps-brain`.

### New environment variable: `TAPPS_BRAIN_MCP_HTTP_PORT`

| Variable | 3.5.x | 3.6 |
|----------|-------|-----|
| `TAPPS_BRAIN_MCP_HTTP_PORT` | Not used | Port for operator MCP transport (`0` = disabled) |
| `TAPPS_BRAIN_MCP_HOST` | Not used | Bind address for MCP transport |

The default value is `8090` in the reference `docker-compose.hive.yaml`.
Set it to `0` to run HTTP-only (equivalent to 3.5.x `tapps-brain-http`
behaviour).

### No change to wire protocol

The on-wire protocols for both transports are unchanged:

- HTTP data-plane (`8080`) — same endpoints, same JSON shapes, same auth header.
- Operator MCP (`8090`) — same tools, same Streamable-HTTP spec.

Clients that pointed at `http://tapps-brain-http:8080` only need to update the
hostname to `tapps-brain`.  Clients that pointed at
`http://tapps-brain-operator-mcp:8090` only need to update the hostname.

---

## Migration Steps

### 1. Pull the new image

```bash
docker pull ghcr.io/wtthornton/tapps-brain:3.6  # or your registry
# or rebuild from source:
docker compose -f docker/docker-compose.hive.yaml build tapps-brain
```

### 2. Update your Compose override (if any)

If you have a `docker-compose.override.yaml` that references `tapps-brain-http`
or `tapps-brain-operator-mcp`, rename the service key to `tapps-brain`:

```yaml
# BEFORE (3.5.x override)
services:
  tapps-brain-http:
    environment:
      TAPPS_BRAIN_AUTH_TOKEN: "my-token"
  tapps-brain-operator-mcp:
    environment:
      TAPPS_BRAIN_ADMIN_TOKEN: "my-admin-token"

# AFTER (3.6 override)
services:
  tapps-brain:
    environment:
      TAPPS_BRAIN_AUTH_TOKEN: "my-token"
      TAPPS_BRAIN_ADMIN_TOKEN: "my-admin-token"
      TAPPS_BRAIN_MCP_HTTP_PORT: "8090"
```

### 3. Update `.env` / secrets

No secret file changes required.  The same `tapps_http_auth_token` secret is
consumed by the unified `tapps-brain` service.

### 4. Roll the stack

```bash
docker compose -f docker/docker-compose.hive.yaml down tapps-brain-http tapps-brain-operator-mcp 2>/dev/null || true
docker compose -f docker/docker-compose.hive.yaml up -d tapps-brain
```

Or perform a full stack restart:

```bash
docker compose -f docker/docker-compose.hive.yaml down
docker compose -f docker/docker-compose.hive.yaml up -d
```

### 5. Verify

```bash
# HTTP data-plane
curl http://localhost:8080/health
# {"status":"ok",...}

# Operator MCP transport
curl http://localhost:8090/mcp/
# 200 OK

# Container status
docker compose -f docker/docker-compose.hive.yaml ps
# tapps-brain   running (healthy)
```

---

## Running HTTP-Only (Disabling the MCP Transport)

If you were running `tapps-brain-http` without the operator MCP container and
want to continue HTTP-only, set `TAPPS_BRAIN_MCP_HTTP_PORT=0`:

```yaml
# docker-compose.override.yaml
services:
  tapps-brain:
    environment:
      TAPPS_BRAIN_MCP_HTTP_PORT: "0"
```

Or via env at the shell:

```bash
TAPPS_BRAIN_MCP_HTTP_PORT=0 tapps-brain serve
```

The healthcheck will then probe only port `8080`.

---

## Rollback (Downgrade to 3.5.x)

If you need to roll back to the two-container layout, pin the 3.5.x image and
restore the old Compose file from git:

```bash
git checkout v3.5.x -- docker/docker-compose.hive.yaml docker/Dockerfile.http
docker compose -f docker/docker-compose.hive.yaml up -d
```

Note: the 3.6 schema migrations are backwards-compatible; no data migration is
required for a rollback.

---

## See Also

- [Deployment Guide](deployment.md) — full shared-service pattern reference
- [AgentForge Integration](agentforge-integration.md)
- [Hive Deployment](hive-deployment.md)
