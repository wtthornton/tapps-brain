# Environment Variable Reference

This is the **canonical environment variable contract** for tapps-brain v3.
It lists every variable that the library, CLI, and MCP server read at runtime.

> **Quick link:** README and AGENTS.md point here as the authoritative env-var table.
> A ready-made template lives in `.env.example` at the repo root.

## Full env-var table

| Variable | Meaning | Example | Default | Required (prod) | Required (dev) |
|---|---|---|---|---|---|
| **Identity & paths** | | | | | |
| `TAPPS_BRAIN_AGENT_ID` | Agent identity string. Scopes private memory rows and Hive propagation. | `claude-code` | — | ✅ | ✅ |
| `TAPPS_BRAIN_PROJECT_DIR` | Project root path — used to derive the stable `project_id` hash. Defaults to `cwd`. | `/home/user/myrepo` | `cwd` | ✅ | ✅ |
| **Postgres DSNs** | | | | | |
| `TAPPS_BRAIN_DATABASE_URL` | Unified v3 DSN — private memory + fallback for Hive. `postgres://` or `postgresql://` scheme required. | `postgres://tapps:s3cr3t@db:5432/tapps` | — | ✅ | optional |
| `TAPPS_BRAIN_HIVE_DSN` | Hive shared-store DSN. Falls back to `TAPPS_BRAIN_DATABASE_URL` when not set in some contexts. | `postgres://tapps:s3cr3t@db:5432/tapps_hive` | — | ✅ | optional |
| `TAPPS_BRAIN_FEDERATION_DSN` | Cross-project Federation DSN. Used by `create_federation_backend()`. | `postgres://tapps:s3cr3t@db:5432/tapps_fed` | — | if using federation | optional |
| **Migrations & strict mode** | | | | | |
| `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` | Set `true` to run pending Hive schema migrations on startup. | `true` | `false` | ✅ first deploy | optional |
| `TAPPS_BRAIN_STRICT` | When `1`, missing DSN exits with a clear error (stderr + non-zero). **Not setting this is not for production.** | `1` | `0` | ✅ production | no |
| **Pool sizing** | | | | | |
| `TAPPS_BRAIN_HIVE_POOL_MIN` | Minimum connections kept open in the pool. | `2` | `2` | no | no |
| `TAPPS_BRAIN_HIVE_POOL_MAX` | Maximum simultaneous connections from the pool. | `20` | `10` | tune for workload | no |
| `TAPPS_BRAIN_HIVE_CONNECT_TIMEOUT` | Seconds to wait when acquiring a connection from the pool. | `10` | `5` | no | no |
| `TAPPS_BRAIN_HIVE_POOL_IDLE_TIMEOUT` | Seconds before an idle connection is evicted. Set `0` to disable eviction. | `600` | `300` | no | no |
| **Groups & expert domains** | | | | | |
| `TAPPS_BRAIN_GROUPS` | CSV group memberships for Hive group propagation. | `dev-pipeline,frontend-guild` | — | if using groups | no |
| `TAPPS_BRAIN_EXPERT_DOMAINS` | CSV domains for auto-publish to Hive expert namespace. | `css,react` | — | if using expert publish | no |
| **HTTP adapter** | | | | | |
| `TAPPS_BRAIN_HTTP_AUTH_TOKEN` | Bearer token required on protected HTTP adapter routes. Omit to disable auth (development only). | `my-secret-token` | — | ✅ if HTTP on | no |
| **MCP feature flags** | | | | | |
| `TAPPS_BRAIN_OPERATOR_TOOLS` | Set `1` to register advanced/maintenance MCP tools (consolidation, GC, export, eval). Not for regular agent sessions. Equivalent to `--enable-operator-tools` CLI flag. | `1` | `0` | operator sessions | no |

## DSN format

Both `postgres://` and `postgresql://` schemes are accepted:

```
postgres://[user[:password]@][host][:port]/[database]
```

Examples:

```bash
# Local development (no password)
TAPPS_BRAIN_HIVE_DSN="postgres://localhost/tapps_dev"

# Production with credentials
TAPPS_BRAIN_HIVE_DSN="postgres://tapps_runtime:s3cr3t@db.internal:5432/tapps_hive"

# Unix socket
TAPPS_BRAIN_HIVE_DSN="postgres:///tapps_hive"
```

> **Malformed DSN:** A URL that does not begin with `postgres://` or
> `postgresql://` raises `ValueError` immediately with an `ADR-007`
> reference. The error is logged to stderr and never contains the raw DSN
> (secrets are not leaked to logs).

## Pool sizing guidance

| Workload | `POOL_MIN` | `POOL_MAX` | Notes |
|---|---|---|---|
| Single developer / CLI | 1 | 5 | Rarely needs many simultaneous connections |
| Small team (≤ 10 agents) | 2 | 20 | Default max fits most cases |
| Medium team (10–50 agents) | 4 | 50 | Tune against `pg_stat_activity` |
| Large multi-host cluster | 5 | 100 | Consider PgBouncer in transaction mode |

Pool saturation is exposed in the health/readiness JSON (`hive.pool_saturation`,
`0.0` – `1.0`). Alert when sustained saturation exceeds `0.8`.

## Health JSON fields (v3)

The `/ready` endpoint and `tapps-brain health` CLI command return a JSON report.
New fields added in **v3 (EPIC-059.7)**:

| JSON path | Type | Description |
|---|---|---|
| `hive.pool_saturation` | `float \| null` | Fraction of `POOL_MAX` currently in use |
| `hive.migration_version` | `int \| null` | Highest applied Hive schema migration version |

Example:

```json
{
  "status": "ok",
  "hive": {
    "status": "ok",
    "connected": true,
    "entries": 1234,
    "agents": 3,
    "pool_saturation": 0.2,
    "migration_version": 2
  }
}
```

## See also

- [`.env.example`](../../.env.example) — ready-made template at repo root
- [Agent integration guide](agent-integration.md) — AgentBrain API + env-var usage examples
- [Hive Deployment Guide](hive-deployment.md) — Docker Compose, managed Postgres
- [Hive Operations](hive-operations.md) — migration runbook, monitoring
- [MCP operator tools](mcp.md#operator-tools-advancedmaintenance) — when to use `TAPPS_BRAIN_OPERATOR_TOOLS`
- [ADR-007: Postgres-Only Backends](../planning/adr/ADR-007-postgres-only-no-sqlite.md)
