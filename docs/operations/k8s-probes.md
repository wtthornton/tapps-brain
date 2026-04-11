# Kubernetes Liveness and Readiness Probes

tapps-brain's HTTP adapter exposes two dedicated probe endpoints that map
directly to Kubernetes probe semantics. Use these instead of the full
`run_health_check()` diagnostic when low latency is critical.

---

## Endpoints

| Endpoint | Purpose | DB call | Kubernetes probe |
|----------|---------|---------|-----------------|
| `GET /health` | Liveness | **No** | `livenessProbe` |
| `GET /ready` | Readiness | **Yes** — Postgres ping | `readinessProbe` |

### `/health` — liveness

Returns **200 OK** with a JSON body as long as the process is alive.
**Never** touches Postgres or any external dependency.

```json
{
  "status": "ok",
  "service": "tapps-brain",
  "version": "3.0.0"
}
```

Use as `livenessProbe`. A non-200 response should be treated as a crashed
process (restart it).

### `/ready` — readiness

Performs a Postgres connection probe and checks the highest applied schema
migration version.

**200 OK — ready:**

```json
{
  "status": "ready",
  "migration_version": 7,
  "detail": "ready (migration_version=7)"
}
```

**503 Service Unavailable — degraded:**

```json
{
  "status": "degraded",
  "migration_version": null,
  "detail": "db_error: connection refused"
}
```

Use as `readinessProbe`. Kubernetes will stop routing traffic to pods
returning 503 until they become ready again.

---

## 503 vs 500

| Status | Meaning | Action |
|--------|---------|--------|
| **200** | DB reachable and migrated | Traffic allowed |
| **503** | DB unreachable *or* no DSN configured | Traffic held; retry |
| **500** | Bug in the probe itself (unexpected exception) | File a bug; restart pod |

`/ready` deliberately returns **503** (not 500) for expected degraded states
(DB down, network partition, missing DSN).  This tells Kubernetes the pod is
temporarily unavailable without indicating a code bug.

`/health` never returns 503 or 500 — any non-200 from `/health` means the
HTTP server itself crashed, which should trigger a restart.

---

## Probe routes are always public

Probe endpoints (`/health`, `/ready`, `/metrics`) do **not** require an
`Authorization` header, even when `TAPPS_BRAIN_HTTP_AUTH_TOKEN` is
configured.  Kubernetes orchestrators cannot inject tokens into liveness and
readiness probes, so requiring auth would break the pod lifecycle.

---

## Kubernetes manifest snippet

```yaml
# Minimal deployment probe config for tapps-brain
containers:
  - name: tapps-brain
    image: your-registry/tapps-brain:3.0.0
    ports:
      - containerPort: 8080
    env:
      - name: TAPPS_BRAIN_DATABASE_URL
        valueFrom:
          secretKeyRef:
            name: tapps-brain-secrets
            key: database-url
      - name: TAPPS_BRAIN_HTTP_HOST
        value: "0.0.0.0"
      - name: TAPPS_BRAIN_HTTP_PORT
        value: "8080"

    # Liveness: restart the pod if the HTTP server dies.
    # No DB dependency — cheap, frequent.
    livenessProbe:
      httpGet:
        path: /health
        port: 8080
      initialDelaySeconds: 5
      periodSeconds: 10
      failureThreshold: 3

    # Readiness: hold traffic until Postgres is reachable and migrations applied.
    # More expensive than liveness — probe less frequently.
    readinessProbe:
      httpGet:
        path: /ready
        port: 8080
      initialDelaySeconds: 10
      periodSeconds: 15
      failureThreshold: 5
      successThreshold: 1
```

### Tuning guidelines

| Field | Recommendation | Reason |
|-------|---------------|--------|
| `livenessProbe.periodSeconds` | 10 | Fast; probe is free (no DB). |
| `readinessProbe.periodSeconds` | 15 | DB ping adds ~5 ms; avoid saturating pool. |
| `readinessProbe.initialDelaySeconds` | 10–30 | Allow time for DB init / schema migration. |
| `readinessProbe.failureThreshold` | 5 | Tolerate brief connection blips. |

---

## Starting the HTTP adapter

The adapter is opt-in. Start it alongside your workload:

```python
from tapps_brain.http_adapter import HttpAdapter

adapter = HttpAdapter(host="0.0.0.0", port=8080)
adapter.start()   # non-blocking daemon thread
```

Or via context manager:

```python
with HttpAdapter(host="0.0.0.0", port=8080) as adapter:
    # your workload runs here
    ...
```

When neither `TAPPS_BRAIN_DATABASE_URL` nor `TAPPS_BRAIN_HIVE_DSN` is set,
`/ready` returns 503 immediately (no DB to probe).

---

## Related

- [`docs/guides/observability.md`](../guides/observability.md) — full health
  report fields, diagnostics, metrics.
- [`docs/guides/hive-deployment.md`](../guides/hive-deployment.md) — Docker /
  Compose setup for Postgres.
- STORY-061.4 / STORY-061.5 (tapps-brain EPIC-061) — probe implementation.
