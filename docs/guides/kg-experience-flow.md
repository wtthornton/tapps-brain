# Knowledge-Graph Populate-then-Retrieve Flow

> **Audience:** Client integrations (agents, tapps-mcp, REST callers) that want to
> write structured knowledge into the KG and then query it.
>
> **Introduced:** TAP-2723

## Overview

The tapps-brain Knowledge Graph (KG) stores entities (named nodes) and edges
(typed relationships between nodes).  Entities are identified by UUID.  To
write an edge you first need UUIDs for both endpoints; to get those UUIDs you
call `brain_resolve_entity` (MCP) or `POST /v1/kg/resolve_entity` (REST).

The populate-then-retrieve flows are:

**KG neighbourhood (structure only):**

```
1. resolve_entity (name → UUID)
2. record_event   (write edge using UUID)
3. get_neighbors  (read neighbourhood by UUID)
```

**Experience event payloads (metrics / audit — since 3.24.0):**

```
1. record_event      (write event + payload)
2. brain_query_events (read payloads by event_type / file_path)
```

Use `brain_query_events` (MCP) or `POST /v1/experience:query` (REST) for
`quality_metric` and other `experience_events` rows — not `brain_get_neighbors`,
which returns KG edge structure only.

## Step 1 — Resolve or create an entity

**MCP:**

```python
result = brain_resolve_entity(entity_type="module", canonical_name="retrieval")
# {"entity_id": "…uuid…", "entity_type": "module", "canonical_name": "retrieval",
#  "created": true, "confidence": 0.6, "reason": "created"}
entity_id = result["entity_id"]
```

**REST:**

```http
POST /v1/kg/resolve_entity
X-Project-Id: my-project
Content-Type: application/json

{"entity_type": "module", "canonical_name": "retrieval"}
```

Response:

```json
{
  "entity_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "entity_type": "module",
  "canonical_name": "retrieval",
  "created": true,
  "confidence": 0.6,
  "reason": "created"
}
```

The call is **idempotent**: two calls with the same `(entity_type, canonical_name)`
always return the same UUID.  `created` is `true` on the first call and `false`
on subsequent ones.

## Step 2 — Record an event with an edge

Use the UUID from step 1 in the `subject_entity_id` / `object_entity_id` fields
of an edge spec.  Both fields must be valid UUIDs — passing a non-UUID returns a
structured `bad_uuid` error instead of a raw Postgres cast failure.

**MCP:**

```python
brain_record_event(
    event_type="tool_called",
    subject_key="memory.recall",
    utility_score=0.8,
    entities=[{"entity_type": "module", "canonical_name": "retrieval"}],
    edges=[{
        "subject_entity_id": entity_id,
        "predicate": "produces",
        "object_entity_id": entity_id,
    }],
)
# {"event_id": "…", "entity_ids": ["…"], "edge_ids": ["…"], "evidence_ids": []}
```

**REST:**

```http
POST /v1/experience
X-Project-Id: my-project
Content-Type: application/json

{
  "event_type": "tool_called",
  "subject_key": "memory.recall",
  "utility_score": 0.8,
  "entities": [{"entity_type": "module", "canonical_name": "retrieval"}],
  "edges": [{
    "subject_entity_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "predicate": "produces",
    "object_entity_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  }]
}
```

The event row plus all *valid* side-effects (optional memory, entities, edges,
evidence) are written in **one Postgres transaction** — a genuine write failure
(e.g. a DB error) rolls back the whole event.  Since 3.22.4, a *malformed*
side-effect spec (an edge missing its UUIDs, or evidence with neither/both of
`edge_id` / `entity_id`) is **skipped, not fatal**: the core event and the valid
side-effects still commit, and the response is `200` with a `warnings` array
(`kind`, `index`, `errors`). A malformed top-level request still returns a typed
`4xx`, never a masked `500`.

## Step 3 — Read back the neighbourhood

Pass the UUID to `brain_get_neighbors` (MCP) or `POST /v1/kg/neighbors` (REST)
to fetch the 1-hop (or 2-hop) neighbourhood.

**MCP:**

```python
result = brain_get_neighbors(
    entity_ids_json='["a1b2c3d4-e5f6-7890-abcd-ef1234567890"]',
    hops=1,
    limit=20,
)
# {"neighbors": [{"edge_id": "…", "predicate": "produces", "hop": 1, ...}],
#  "entity_ids": ["…"]}
```

**REST:**

```http
POST /v1/kg/neighbors
X-Project-Id: my-project
Content-Type: application/json

{
  "entity_ids": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890"],
  "hops": 1,
  "limit": 20
}
```

Response:

```json
{
  "neighbors": [
    {
      "edge_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
      "predicate": "produces",
      "edge_confidence": 0.6,
      "neighbor_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "entity_type": "module",
      "canonical_name": "retrieval",
      "hop": 1
    }
  ],
  "entity_ids": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890"]
}
```

## UUID validation

All KG tools validate UUID fields **before** any Postgres cast.  Passing a
non-UUID string (e.g. a canonical name like `"retrieval"` instead of its UUID)
returns a structured error:

```json
{"error": "bad_uuid", "field": "entity_ids_json[0]",
 "detail": "'entity_ids_json[0]' must be a valid UUID; got 'retrieval'"}
```

Always call `resolve_entity` first to obtain a UUID — never pass a canonical
name directly to a UUID-bound field.

## Available endpoints

| Surface | Resolve entity | Record event | Get neighbours | Explain path |
|---------|---------------|--------------|----------------|--------------|
| MCP     | `brain_resolve_entity` | `brain_record_event` | `brain_get_neighbors` | `brain_explain_connection` |
| REST    | `POST /v1/kg/resolve_entity` | `POST /v1/experience` | `POST /v1/kg/neighbors` | `POST /v1/kg/explain` |

## See also

- `src/tapps_brain/mcp_server/tools_kg.py` — MCP tool implementations
- `src/tapps_brain/services/kg_service.py` — service layer (`resolve_entity`, `record_event`, `get_neighbors`)
- `src/tapps_brain/postgres_kg.py` — Postgres backend (`upsert_entity`, `resolve_entity`)
- `tests/integration/test_http_adapter_kg.py` — integration tests for HTTP adapter UUID validation and populate-then-retrieve flow
