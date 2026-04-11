# Operator Runbook — tapps-brain Observability

> **Scope:** tapps-brain v3 (EPIC-061, STORY-061.8).  
> **Audience:** SRE / platform engineers running tapps-brain in production.  
> **Print budget:** ≤ 2 pages.

---

## Golden signals and where to find them

| Signal | OTel instrument | Key labels | Threshold |
|--------|----------------|-----------|-----------|
| Save latency | `tapps_brain.operation.duration` histogram | `operation.type="remember"` | p99 > 500 ms → page |
| Recall latency | `tapps_brain.operation.duration` histogram | `operation.type="recall"` | p99 > 300 ms → page |
| Error rate | `tapps_brain.operation.errors` counter | `error.type` | > 1 % of ops → warn |
| Pool saturation | `tapps_brain.pool.connections_in_use` gauge | — | > 80 % of max → warn |
| Migration lag | `tapps_brain.migration.version` gauge | — | < expected → page |
| Hive round-trip | `tapps_brain.operation.duration` histogram | `operation.type="hive_propagate"` | p99 > 1 s → warn |

Export endpoint: set `OTEL_EXPORTER_OTLP_ENDPOINT` (OTLP gRPC).  
Service identity: `OTEL_SERVICE_NAME` (default `tapps-brain`), `OTEL_SERVICE_VERSION`.

---

## Health and readiness endpoints

See [`k8s-probes.md`](k8s-probes.md) for full probe spec.  
Quick summary:

```
GET /health   → 200 (liveness — no DB call; safe to poll every 5 s)
GET /ready    → 200 ready | 503 degraded (readiness — Postgres ping + migration check)
```

`/ready` JSON on degraded:

```json
{ "status": "degraded", "reason": "db_unavailable", "migration_version": null }
```

---

## Alert runbook — step by step

### ALERT: `TappsBrainHighP99Latency`

**Condition:** `histogram_quantile(0.99, tapps_brain.operation.duration) > 0.5`

1. Check `/ready` — if 503, the DB is the culprit (see *DB down* below).
2. Check `tapps_brain.pool.connections_in_use` — if > 80 % of `TAPPS_BRAIN_POOL_MAX_SIZE`,
   increase pool: `TAPPS_BRAIN_POOL_MAX_SIZE=20` (default 10).
3. Check Postgres slow-query log (`pg_stat_statements`) for lock waits.
4. If pool is fine: check consolidation/GC job — a runaway consolidation scan
   blocks the write path.  Disable with `TAPPS_BRAIN_AUTO_CONSOLIDATE=0` and
   reschedule during low-traffic.

### ALERT: `TappsBrainHighErrorRate`

**Condition:** `rate(tapps_brain.operation.errors[5m]) / rate(tapps_brain.operation.duration_count[5m]) > 0.01`

1. Break down by `error.type` label:
   - `db_error` — connectivity/pool issue; check `/ready` and Postgres logs.
   - `content_blocked` — safety layer is rejecting inputs; check `safety.py` thresholds.
   - `invalid_scope` — caller is passing unknown `agent_scope`; fix the caller config.
   - `write_rules_violation` — Hive write rule rejected; inspect agent group config.
2. For `db_error` spikes: check DSN is correct (`TAPPS_BRAIN_HIVE_DSN`) and
   credentials have not rotated.

### ALERT: `TappsBrainMigrationLag`

**Condition:** `tapps_brain.migration.version < <expected_version>`

1. Confirm the migrator ran: `docker run tapps-brain-migrate` (or the Makefile target
   `make brain-migrate`).
2. Check Postgres migration table: `SELECT * FROM tapps_brain_migrations ORDER BY applied_at DESC LIMIT 5;`
3. If the migration failed mid-way, the DB may be in a partial state.  Check the
   migration log, roll back to the last clean snapshot, and re-apply.
4. Do **not** route traffic to a brain pod whose `/ready` shows `migration_version`
   below expected.

### DB down

1. `/ready` returns 503 with `"reason": "db_unavailable"`.
2. tapps-brain continues to serve cached in-memory results (degraded mode) until
   pool exhaustion.
3. Restore Postgres connectivity.  tapps-brain reconnects automatically via
   `psycopg_pool` retry on next request.
4. Verify `/ready` returns 200 before removing the `degraded` incident.

---

## Environment variables — quick reference

| Variable | Purpose | Default |
|----------|---------|---------|
| `TAPPS_BRAIN_HIVE_DSN` | Postgres DSN for Hive | *required* |
| `TAPPS_BRAIN_FEDERATION_DSN` | Postgres DSN for Federation | optional |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector endpoint | off |
| `OTEL_SERVICE_NAME` | Metric/span service name | `tapps-brain` |
| `OTEL_SERVICE_VERSION` | Metric/span service version | `""` |
| `TAPPS_BRAIN_POOL_MAX_SIZE` | Max Postgres connections | `10` |
| `TAPPS_BRAIN_POOL_IDLE_TIMEOUT` | Idle connection timeout (s) | `300` |
| `TAPPS_BRAIN_AUTO_CONSOLIDATE` | Enable auto-consolidation | `1` |

Full env table: [`docs/guides/env-contract.md`](../guides/env-contract.md).

---

## Non-normative Prometheus rule examples

> These are **illustrative** — adapt thresholds and labels to your environment.
> Not shipped as part of the tapps-brain package.

```yaml
# prometheus-rules-example.yaml  (non-normative)
groups:
  - name: tapps_brain
    rules:
      - alert: TappsBrainHighP99Latency
        expr: |
          histogram_quantile(0.99,
            sum(rate(tapps_brain_operation_duration_bucket[5m])) by (le, operation_type)
          ) > 0.5
        for: 2m
        labels:
          severity: page
        annotations:
          summary: "tapps-brain p99 latency > 500 ms"
          runbook: "https://github.com/your-org/tapps-brain/blob/main/docs/operations/observability-runbook.md"

      - alert: TappsBrainHighErrorRate
        expr: |
          sum(rate(tapps_brain_operation_errors_total[5m])) by (error_type)
          /
          sum(rate(tapps_brain_operation_duration_count[5m]))
          > 0.01
        for: 5m
        labels:
          severity: warn
        annotations:
          summary: "tapps-brain error rate > 1 %"

      - alert: TappsBrainPoolSaturation
        expr: |
          tapps_brain_pool_connections_in_use / tapps_brain_pool_max_size > 0.8
        for: 3m
        labels:
          severity: warn
        annotations:
          summary: "tapps-brain Postgres pool > 80 % utilised"

      - alert: TappsBrainMigrationLag
        expr: tapps_brain_migration_version < 7   # update <expected> each release
        for: 1m
        labels:
          severity: page
        annotations:
          summary: "tapps-brain migration version behind expected"
```

---

## Related

- [`telemetry-policy.md`](telemetry-policy.md) — allowed / forbidden span attributes and metric labels
- [`k8s-probes.md`](k8s-probes.md) — liveness / readiness probe spec
- [`docs/guides/hive-deployment.md`](../guides/hive-deployment.md) — Postgres deployment guide
- [`docs/guides/env-contract.md`](../guides/env-contract.md) — full environment variable table
- [EPIC-061](../planning/epics/EPIC-061.md) — observability epic
- [EPIC-063](../planning/epics/EPIC-063.md) — trust boundaries (DB roles, RLS)
