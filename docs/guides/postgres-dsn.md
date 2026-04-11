# PostgreSQL DSN & Connection Pool Reference

This guide lists every environment variable that controls tapps-brain's
PostgreSQL connections, pool sizing, and related runtime behaviour.

## Env-var table

| Variable | Meaning | Example | Default | Required (prod) | Required (dev) |
|---|---|---|---|---|---|
| `TAPPS_BRAIN_DATABASE_URL` | Unified v3 DSN — private memory + fallback for Hive. `postgres://` or `postgresql://` scheme required. | `postgres://tapps:s3cr3t@db:5432/tapps` | — | ✅ | optional |
| `TAPPS_BRAIN_HIVE_DSN` | Hive shared-store DSN. Used by `create_hive_backend()` and `resolve_hive_backend_from_env()`. Falls back to `TAPPS_BRAIN_DATABASE_URL` when not set in some contexts. | `postgres://tapps:s3cr3t@db:5432/tapps_hive` | — | ✅ | optional |
| `TAPPS_BRAIN_FEDERATION_DSN` | Cross-project Federation DSN. Used by `create_federation_backend()`. | `postgres://tapps:s3cr3t@db:5432/tapps_fed` | — | if using federation | optional |
| `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` | Set `true` to run pending Hive schema migrations on startup. | `true` | `false` | ✅ first deploy | optional |
| `TAPPS_BRAIN_HIVE_POOL_MIN` | Minimum connections kept open in the pool. | `2` | `2` | no | no |
| `TAPPS_BRAIN_HIVE_POOL_MAX` | Maximum simultaneous connections from the pool. | `20` | `10` | tune for workload | no |
| `TAPPS_BRAIN_HIVE_CONNECT_TIMEOUT` | Seconds to wait when acquiring a connection from the pool. | `10` | `5` | no | no |
| `TAPPS_BRAIN_HIVE_POOL_IDLE_TIMEOUT` | Seconds before an idle connection is evicted from the pool. Set `0` to disable eviction. | `600` | `300` | no | no |
| `TAPPS_BRAIN_AGENT_ID` | Agent identity string. Scopes private memory rows and Hive propagation. | `claude-code` | — | ✅ | ✅ |
| `TAPPS_BRAIN_PROJECT_DIR` | Project root path — used to derive the stable `project_id` hash. | `/home/user/myrepo` | `cwd` | ✅ | ✅ |
| `TAPPS_BRAIN_GROUPS` | CSV group memberships for Hive group propagation. | `dev-pipeline,frontend-guild` | — | if using groups | no |
| `TAPPS_BRAIN_EXPERT_DOMAINS` | CSV domains for auto-publish to Hive. | `css,react` | — | if using expert publish | no |
| `TAPPS_BRAIN_STRICT` | When `1`, missing DSN exits with a clear error (stderr + non-zero). **Not setting this is not for production** — use non-strict only for local dev. | `1` | `0` | ✅ production | no |

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

- [Hive Deployment Guide](hive-deployment.md) — Docker Compose, managed Postgres
- [Hive Operations](hive-operations.md) — migration runbook, monitoring
- [ADR-007: Postgres-Only Backends](../planning/adr/ADR-007-postgres-only-no-sqlite.md)
