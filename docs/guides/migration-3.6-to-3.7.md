# Migration 3.6 → 3.7

Upgrading an existing v3.6.x deployment to v3.7.x (including v3.7.2). Three concrete changes; the rest is backwards compatible.

## 1. `mcp+stdio://` transport removed (v3.7.0)

The stdio subprocess transport is gone. Two CLI entry points also removed from `pyproject.toml`:

| Removed | Replacement |
|---|---|
| `tapps-brain-mcp` | `tapps-brain serve` (unified binary — both transports, one process) |
| `tapps-brain-operator-mcp` | `tapps-brain serve` with `TAPPS_BRAIN_MCP_HTTP_PORT` |
| Client URL `mcp+stdio://localhost` | `mcp+http://<host>:8080` or `http://<host>:8080` |

**Why.** The deployment model is "one container serves ~20 agents on the box." stdio = one subprocess per agent = 20 isolated processes with no shared pool; HTTP is the correct transport. See the [deployment guide](deployment.md).

**What will break.** Any client code that constructs `TappsBrainClient("mcp+stdio://...")` raises `ValueError: Unsupported URL scheme` at call time. Any shell script invoking `tapps-brain-mcp` or `tapps-brain-operator-mcp` gets `command not found`.

**Fix.** Point clients at the deployed HTTP container. If you were running your own `tapps-brain-mcp` process per agent, delete that orchestration and run one `docker-tapps-brain-http:3.7.2` container on the host.

## 2. Operator MCP requires `TAPPS_BRAIN_ADMIN_TOKEN` (v3.7.0)

The operator MCP transport on port `:8090` refuses to start without an admin token. A bearer-token ASGI middleware validates every `/mcp/*` request; unauthenticated callers get `401 {"error":"unauthorized"}`.

If your compose starts the unified binary with `TAPPS_BRAIN_MCP_HTTP_PORT > 0` and the env var is blank, the container will `exit 1` at boot with a clear error.

**Fix.** Set `TAPPS_BRAIN_ADMIN_TOKEN` in `.env` (use `openssl rand -hex 32` to generate). If you don't need the operator transport, set `TAPPS_BRAIN_MCP_HTTP_PORT=0` to disable it.

## 3. Client + brain MCP path collapsed back to `/mcp` (v3.7.3)

v3.7.2 worked around a 404 by pointing `TappsBrainClient` at `/mcp/mcp` — the path that emerged from FastMCP's inner `streamable_http_path = /mcp` mounted at `/mcp` by the HTTP adapter. v3.7.3 (TAP-509) collapses the public path back to a single `/mcp` by pinning FastMCP's inner path to `/`. Both client and brain must move together: a v3.7.2 client will 404 against a v3.7.3+ brain, and vice versa.

**Fix.** Upgrade the brain image and the vendored wheel together to `>= 3.7.3`. Hand-rolled HTTP callers must POST to `/mcp` (not `/mcp/mcp`).

## Wheel ↔ image version lock

From v3.7.0 onward the `docker-tapps-brain-http` image embeds a `LABEL tapps_brain_version=<X.Y.Z>` that matches the embedded wheel. Assert the invariant at AgentForge bootstrap:

```python
# Any service that also vendors the tapps-brain wheel
import tapps_brain
wheel_ver = tapps_brain.__version__   # e.g. "3.7.2"
image_label = subprocess.run(
    ["docker", "inspect", "tapps-brain-http", "--format",
     "{{index .Config.Labels \"tapps_brain_version\"}}"],
    capture_output=True, text=True, check=True,
).stdout.strip()
assert wheel_ver == image_label, (
    f"Brain drift: wheel={wheel_ver} image_label={image_label}. "
    f"Rebuild image or re-vendor wheel — see docs/guides/migration-3.6-to-3.7.md"
)
```

Drift between wheel and image caused the TAP-499 crash-loop. Fail loud on mismatch instead of debugging a `MigrationDowngradeError` later.

## Project registration prerequisite (from v3.5, reinforced)

The brain's project registry is fail-closed (ADR-010). Every `/mcp` request must carry `X-Project-Id` naming a registered project. Register at deployment time:

```bash
ADMIN=$TAPPS_BRAIN_ADMIN_TOKEN
PROFILE=$(docker exec tapps-brain-http python3 -c \
  "from tapps_brain.profile import get_builtin_profile; import json; \
   print(json.dumps(get_builtin_profile('repo-brain').model_dump(mode='json')))")

curl -s -X POST http://127.0.0.1:8080/admin/projects \
  -H "authorization: Bearer $ADMIN" \
  -H 'content-type: application/json' \
  -d "$(printf '{"project_id":"<your-project>","profile":%s,"approved":true,"source":"admin"}' "$PROFILE")"
```

`TappsBrainClient` sets `X-Project-Id` automatically from its `project_id` constructor arg (or `TAPPS_BRAIN_PROJECT` env var).

## Checklist

- [ ] Delete any `tapps-brain-mcp` / `tapps-brain-operator-mcp` script invocations
- [ ] Replace `mcp+stdio://` URLs with `mcp+http://<host>:8080` or `http://<host>:8080`
- [ ] Set `TAPPS_BRAIN_ADMIN_TOKEN` in `.env` (or set `TAPPS_BRAIN_MCP_HTTP_PORT=0`)
- [ ] Pin `docker-tapps-brain-http:3.7.2` (no `:latest`)
- [ ] Vendor `tapps_brain-3.7.2-py3-none-any.whl`; tighten constraint to `>=3.7.2,<3.8`
- [ ] Register your project via `POST /admin/projects` on first deploy
- [ ] Add wheel↔image version-match assertion in service bootstrap
