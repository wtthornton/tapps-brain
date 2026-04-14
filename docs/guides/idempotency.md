# Idempotency Keys for Write Operations

**Feature flag:** `TAPPS_BRAIN_IDEMPOTENCY=1` (default OFF)

**Story:** EPIC-070 STORY-070.5

---

## Overview

Idempotency keys let clients safely retry `POST /v1/remember` and
`POST /v1/reinforce` requests after a network failure without risk of
double-inserting entries.

When the feature is enabled, the server stores a hash of each write response
in a Postgres table (`idempotency_keys`).  A duplicate request bearing the
same `X-Idempotency-Key` within 24 hours receives the original response
without re-executing the write.

---

## Prerequisites

1. Apply migration `010_idempotency_keys.sql` to your private schema:

   ```bash
   TAPPS_BRAIN_AUTO_MIGRATE=1 tapps-brain serve
   ```

   or apply manually:

   ```bash
   psql "$TAPPS_BRAIN_DATABASE_URL" \
     -f src/tapps_brain/migrations/private/010_idempotency_keys.sql
   ```

2. Enable the feature flag in your environment:

   ```bash
   export TAPPS_BRAIN_IDEMPOTENCY=1
   ```

---

## HTTP API

### `POST /v1/remember`

Save a memory entry.

**Request headers**

| Header | Required | Description |
|--------|----------|-------------|
| `X-Project-Id` | ✓ | Project identifier |
| `X-Agent-Id` | optional | Agent identifier (default: `"unknown"`) |
| `X-Idempotency-Key` | optional | UUID for deduplication |
| `Authorization` | when configured | Bearer token |

**Request body** (JSON)

```json
{
  "key":         "my-memory-key",
  "value":       "The value to store",
  "tier":        "pattern",
  "source":      "agent",
  "tags":        ["tag1", "tag2"],
  "scope":       "project",
  "confidence":  -1.0,
  "agent_scope": "private",
  "group":       null
}
```

`key` and `value` are required; all other fields are optional.

**Response** — 200 OK

```json
{
  "status": "saved",
  "key": "my-memory-key",
  "tier": "pattern",
  "confidence": 0.8,
  "memory_group": null
}
```

When replaying an idempotent response, the server adds:

```
Idempotency-Replayed: true
```

---

### `POST /v1/reinforce`

Reinforce an existing memory entry.

**Request headers** — same as `/v1/remember`.

**Request body** (JSON)

```json
{
  "key":              "my-memory-key",
  "confidence_boost": 0.1
}
```

`key` is required; `confidence_boost` defaults to `0.0`.

**Response** — 200 OK

```json
{
  "status":       "reinforced",
  "key":          "my-memory-key",
  "confidence":   0.9,
  "access_count": 4
}
```

---

## MCP API

When `TAPPS_BRAIN_IDEMPOTENCY=1`, the `memory_save` and `memory_reinforce`
MCP tools support `_meta.idempotency_key` in the JSON-RPC envelope:

```json
{
  "method": "tools/call",
  "params": {
    "name": "memory_save",
    "arguments": {
      "key":   "my-key",
      "value": "My value"
    },
    "_meta": {
      "idempotency_key": "550e8400-e29b-41d4-a716-446655440000"
    }
  }
}
```

A duplicate `idempotency_key` within 24 hours returns the stored response
without re-executing the write.

---

## Idempotency Key Rules

- Keys are scoped per `(project_id, idempotency_key)` — keys do not collide across tenants.
- Keys expire after **24 hours**.  After expiry, the same key may be reused.
- Use UUIDs (RFC 4122 v4) for keys.  Any non-empty string is accepted, but UUIDs are recommended.
- The server stores the full response body (up to 64 KiB).  If a response body
  exceeds this limit, the key is not stored and the request is not deduplicated.

---

## TTL Sweep

Expired keys are deleted by the GC sweep, which runs as part of
`maintenance_gc` (the `gc_run` MCP operator tool and `tapps-brain maintenance gc` CLI).

To trigger manually:

```python
from tapps_brain.idempotency import sweep_expired_keys
deleted = sweep_expired_keys()
print(f"Swept {deleted} expired idempotency keys")
```

Or with a custom TTL:

```python
from tapps_brain.idempotency import IdempotencyStore
with IdempotencyStore(dsn) as store:
    deleted = store.sweep_expired(ttl_hours=48)
```

---

## Architecture

The `idempotency_keys` table lives in the **private schema** alongside
`private_memories` and `project_profiles`.  Migration 010 creates it.

```
idempotency_keys
  key             TEXT  — client-supplied idempotency key
  project_id      TEXT  — tenant identifier
  response_json   TEXT  — full response body (≤64 KiB)
  response_status INT   — HTTP status code
  created_at      TIMESTAMPTZ  — expiry anchor
  PRIMARY KEY (key, project_id)
```

The `IdempotencyStore` class (`tapps_brain.idempotency`) manages all reads
and writes.  It degrades gracefully if migration 010 has not been applied:
`check()` returns `None` (treated as a miss) and `save()` is a no-op.

---

## Disabling

Remove `TAPPS_BRAIN_IDEMPOTENCY=1` from your environment.  The table
remains in Postgres but is not read or written.
