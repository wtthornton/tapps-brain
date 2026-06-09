# HTTP Adapter

> For full HTTP adapter documentation see the OpenAPI spec at `docs/generated/openapi.yaml` and the source at `src/tapps_brain/http_adapter.py`. This page is the agent-facing summary.

The tapps-brain HTTP adapter is the language-neutral entrypoint to the brain. It runs alongside the MCP server (or standalone) inside the `tapps-brain-http` container at `:8080`, and requires `TAPPS_BRAIN_DATABASE_URL` to be set.

See [agentforge-integration.md](agentforge-integration.md) for end-to-end wiring examples.

## Auth

All routes under `/v1/*` and `/admin/*` require a Bearer token. The data-plane token is set via `TAPPS_BRAIN_AUTH_TOKEN`; the admin token via `TAPPS_BRAIN_ADMIN_TOKEN`. Pass the appropriate token in the `Authorization` header.

```
Authorization: Bearer <TAPPS_BRAIN_AUTH_TOKEN>
X-Project-Id: <project-id>            # required on /v1/*
X-Agent-Id:   <agent-id>              # optional, defaults to "unknown"
X-Idempotency-Key: <UUID>             # optional; replays previous response within 24 h when TAPPS_BRAIN_IDEMPOTENCY=1
```

## Probe & info routes (no auth)

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET | Liveness — always 200 if the process is up. |
| `/healthz` | GET | Phased readiness probe — 200/503 with detailed JSON body (v3.19.0+). Add `?deep=1` for a write-path probe (v3.22.4+). |
| `/ready` | GET | Readiness — 200 when the DB is reachable, 503 when degraded. |
| `/metrics` | GET | Prometheus-format scrape (use `TAPPS_BRAIN_METRICS_TOKEN` to gate). |
| `/openapi.json` | GET | Auto-generated OpenAPI spec for every public route. |
| `/v1/tools/list` | GET | Static MCP-tool catalog snapshot with ETag + Cache-Control (v3.19.0+). |

### `/healthz` — phased readiness body (TAP-1970, v3.19.0+)

`GET /healthz` returns 200 when every phase is green, 503 when any is not.
The body shape is a strict superset of the pre-v3.19.0 `{status, detail}`
envelope — Docker healthchecks (`curl -f /healthz`) still flip correctly.

```jsonc
// 200 OK — happy path
{
  "ok":            true,
  "db_ok":         true,           // Postgres reachable + migrated
  "mcp_ok":        true,           // MCP ASGI sub-app mounted
  "queue_depth":   0,              // write + read in-flight ops
  "circuit_state": "closed",       // closed | degraded | open | half_open
  "brain_version": "3.24.0"        // matches pyproject.toml
}
```

Consumers (notably `BrainBridge`, `tapps doctor`) can distinguish "DB
unreachable" from "MCP cold-starting" from "drain queue flooded" without
scraping `/metrics`. DSN strings are never echoed in the body.

#### `?deep=1` — write-path probe (TAP-2866, v3.22.4+)

The shallow `/healthz` only verifies the DB is reachable + migrated, so a broken
core write path (e.g. a missing `experience_events` partition) could still read
green. `GET /healthz?deep=1` additionally probes the `POST /v1/experience` write
path and adds two fields; `ok` is ANDed with `experience_writable`:

```jsonc
{
  "ok":                  true,
  "db_ok":               true,
  "mcp_ok":              true,
  "queue_depth":         0,
  "circuit_state":       "closed",
  "brain_version":       "3.24.0",
  "experience_writable": true,                  // experience_events table + partitions present
  "experience_detail":   "ready (13 partitions)"
}
```

The same signal is exported as the `tapps_brain_experience_writable` gauge
(1 = writable, 0 = broken) for alerting without the query param.

### `/v1/tools/list` — static catalog with HTTP cache headers (TAP-1843, TAP-1971, v3.19.0+)

A pre-built JSON snapshot of the MCP tool catalog written at container
startup — no Python serialisation, no MCP framework round-trip. Carries
HTTP cache headers so clients can implement conditional fetch:

| Header | Value | Notes |
|---|---|---|
| `ETag` | `W/"<sha256:16>"` | Weak validator over the JSON payload; per-cache-slot (per-profile when `X-Brain-Profile` is set). |
| `Cache-Control` | `public, max-age=300` | Matches the 300 s server-side cache TTL (TAP-1833). |
| `X-Brain-Version` | `3.24.0` | Lets clients invalidate caches when the brain version changes. |
| `X-Catalog-Generated-At` | `<iso8601>` | Snapshot-build timestamp from the lifespan hook. |

Conditional fetch:

```bash
# First call — full payload + ETag
curl -is http://localhost:8080/v1/tools/list | head
# HTTP/1.1 200 OK
# ETag: W/"3f5e9b0e7d4a1c2e"
# Cache-Control: public, max-age=300
# X-Brain-Version: 3.24.0
# X-Catalog-Generated-At: 2026-05-17T20:18:00Z
# ...

# Subsequent call — short-circuit when the catalog hasn't changed
curl -is http://localhost:8080/v1/tools/list \
     -H 'If-None-Match: W/"3f5e9b0e7d4a1c2e"' | head -3
# HTTP/1.1 304 Not Modified
# ETag: W/"3f5e9b0e7d4a1c2e"
# Cache-Control: public, max-age=300
```

The default `tools/list` MCP payload returns the 8-tool **eager** catalog
(`brain_recall`, `brain_remember`, `brain_status`, `brain_get_neighbors`,
`brain_explain_connection`, `memory_search`, `memory_find_related`,
`hive_search`). Non-daily-driver tools carry `defer_loading: true` in
`mcp_profiles.yaml` and remain callable via `tools/call`; clients can opt
into Anthropic Tool Search BETA (header
`advanced-tool-use-2025-11-20`) to discover deferred tools on demand
(TAP-1985). See [mcp-client-repo-setup.md § profile wire contract](mcp-client-repo-setup.md#profile-wire-contract-stable-across-tapps-brain-3x--tap-1579).

## Data-plane routes (Bearer auth)

These are the surface AgentForge / NLTlabsPE / any non-MCP consumer should target. Every operation has a REST counterpart so consumers don't need the `tapps_brain` Python wheel for runtime work.

| Route | Method | Purpose | MCP equivalent |
|---|---|---|---|
| `/v1/remember` | POST | Save a memory entry. | `brain_remember` / `memory_save` |
| `/v1/recall` | POST | Recall memories matching a query (single). | `brain_recall` |
| `/v1/recall:batch` | POST | Recall against multiple queries in one request. | `memory_recall_many` |
| `/v1/forget` | POST | Archive a memory by key (status flip — not destructive). | `brain_forget` |
| `/v1/reinforce` | POST | Boost an entry's confidence. | `memory_reinforce` |
| `/v1/learn_success` | POST | Record a successful task outcome (procedural memory + `success` tag). | `brain_learn_success` |
| `/v1/learn_failure` | POST | Record a failed task outcome (procedural memory + `failure` tag). | `brain_learn_failure` |
| `/v1/remember:batch` | POST | Save up to N entries in one round trip (cap via `TAPPS_BRAIN_MAX_BATCH_SIZE`). | `memory_save_many` |
| `/v1/reinforce:batch` | POST | Reinforce up to N entries in one round trip. | `memory_reinforce_many` |
| `/info` | GET | Runtime build info + flags. | — |
| `/snapshot` | GET | Live read-only `VisualSnapshot` (TTL-cached). | — |

### Knowledge graph & experience routes (v3.19.0+ / v3.24.0+)

| Route | Method | Purpose | MCP equivalent |
|---|---|---|---|
| `/v1/experience` | POST | Record one experience event (+ optional KG side-effects). | `brain_record_event` |
| `/v1/experience:batch` | POST | Record up to N events (per-event transactions). | `brain_record_events_batch` |
| `/v1/experience:query` | POST | **v3.24.0+** — query `experience_events` by `event_type`, time range, optional `entity_id`. | `brain_query_events` |
| `/v1/kg/resolve_entity` | POST | Resolve `(entity_type, canonical_name)` → entity UUID. | `brain_resolve_entity` |
| `/v1/kg/resolve_entities` | POST | **v3.25+** — batch-resolve `entity_refs[]` → `entity_ids` + `results`. | — |
| `/v1/kg/neighbors` | POST | 1–2 hop neighbourhood around entity UUIDs. | `brain_get_neighbors` |
| `/v1/kg/explain` | POST | Shortest path between two entity UUIDs (≤ 3 hops). | `brain_explain_connection` |
| `/v1/kg/feedback` | POST | `edge_helpful` / `edge_misleading` on a KG edge. | `brain_record_feedback` |

```jsonc
// POST /v1/experience:query (v3.24.0+)
{ "event_type": "quality_metric",              // required
  "since"?: "2026-06-01T00:00:00Z",
  "until"?: "2026-06-09T23:59:59Z",
  "entity_id"?: "src/tapps_brain/store.py",   // payload.file_path OR subject_key
  "limit"?: 100 }                              // cap 500
// → { "events": [{event_id, event_type, payload, ts, agent_id, session_id?}], "count": N }
```

```jsonc
// POST /v1/kg/resolve_entities (v3.25+)
{ "entity_refs": [
    {"entity_type": "module", "canonical_name": "retrieval"},
    {"type": "agent", "id": "ralph"}
  ]}
// → { "entity_ids": ["uuid-1", "uuid-2"],
//      "results": [{entity_id, entity_type, canonical_name, created}, ...] }
```

```jsonc
// POST /v1/kg/neighbors — entity_refs resolved in input order, merged after entity_ids
{ "entity_ids": ["uuid-existing"],           // optional — UUID strings only
  "entity_refs": [{"type": "file", "id": "src/foo.py"}],  // optional
  "hops"?: 1, "limit"?: 20 }
// → { "neighbors": [...], "entity_ids": ["uuid-existing", "uuid-resolved"] }
```

See [`knowledge-graph.md`](knowledge-graph.md), [`kg-experience-flow.md`](kg-experience-flow.md), and [`experience-events.md`](../engineering/experience-events.md).

### Body shapes (single-entry routes)

```jsonc
// POST /v1/remember
{ "key": str, "value": str,
  "tier"?: "architectural"|"pattern"|"procedural"|"context",
  "tags"?: [str], "scope"?: str, "confidence"?: float,
  "agent_scope"?: "private"|"domain"|"hive"|"group:<name>",
  "group"?: str }

// POST /v1/recall
{ "query": str, "max_results"?: int = 5, "include_stale"?: bool = false,
  "filter_tier"?: str, "filter_tags"?: [str], "filter_tags_any"?: [str],
  "filter_memory_class"?: str }
// → { "results": [{key, value, tier, confidence, tags, …}], "query": str }

// POST /v1/forget
{ "key": str }
// → { "forgotten": bool, "key": str, "reason"?: "not_found" }

// POST /v1/learn_success
{ "task_description": str, "task_id"?: str }
// → { "learned": true, "key": str }

// POST /v1/learn_failure
{ "description": str, "task_id"?: str, "error"?: str }
// → { "learned": true, "key": str }

// POST /v1/reinforce
{ "key": str, "confidence_boost"?: float }
```

Single-entry routes cap the request body at 64 KiB. Batch routes cap at 10 MiB.

### Error envelope

All routes return errors as a flat JSON object — never wrapped in `detail`:

```jsonc
{ "error": "bad_request", "detail": "X-Project-Id header is required." }
```

Common `error` codes: `bad_request` (400), `unauthorized` (401), `forbidden` (403), `not_found` (404), `payload_too_large` (413), `service_unavailable` (503), `db_unavailable` (503).

## Admin routes (admin Bearer token)

Project registration / approval / token rotation. See [`docs/guides/onboarding.md`](onboarding.md) and the OpenAPI spec for shapes.

## Migrating from the wheel to HTTP-only

Consumers vendoring `tapps_brain-X.Y.Z-py3-none-any.whl` can now drop the wheel entirely and call REST instead. The mapping is one-to-one against `AsyncTappsBrainClient` / `AgentBrain`. See [`agentforge-integration.md`](agentforge-integration.md) for an end-to-end migration example.
