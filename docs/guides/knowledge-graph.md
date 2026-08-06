# Knowledge Graph — Agent Guide

**Audience:** AI coding agents using the `brain_*` knowledge-graph and experience tools on a deployed tapps-brain (EPIC-076 / TAP-1502; query API EPIC-074 / TAP-3157).

**Purpose:** explain when to reach for the KG instead of plain memory, the KG/experience MCP tools (including `brain_query_events` v3.24.0+), the input dataclasses, common patterns, and the constraints that protect KG quality.

For the broader playbook (recall, remember, tier picking, error taxonomy), see [`agent-playbook.md`](agent-playbook.md). For the underlying schema and design rationale, see [`docs/engineering/experience-events.md`](../engineering/experience-events.md) and ADRs [011](../planning/adr/ADR-011-kg-schema-postgres.md), [012](../planning/adr/ADR-012-evidence-required-edges.md), [013](../planning/adr/ADR-013-kg-inherits-memory-lifecycle.md).

---

## 1. When to use the KG (vs. `brain_remember`)

Use plain `brain_remember` when you are recording a **fact** — a decision, convention, or rationale that future agents will recall via text query.

Reach for the KG when you need to reason about **relationships between things**: "what other workflows touched this database?", "what failed approaches connect to this pattern?", "how is module X related to service Y?". The KG stores *entities* (modules, services, workflows, concepts) and *evidence-backed edges* between them, and supports neighbourhood and shortest-path queries that plain text recall cannot answer.

| Question shape | Tool |
|---|---|
| "Did we decide X?" → text recall | [`brain_recall`](agent-playbook.md#brain_recall) |
| "What's connected to entity Y?" → 1- or 2-hop neighbourhood | [`brain_get_neighbors`](#4-brain_get_neighbors) |
| "How does X relate to Y?" → shortest path | [`brain_explain_connection`](#5-brain_explain_connection) |
| "Record this workflow + the entities it touched, atomically" | [`brain_record_event`](#2-brain_record_event) |
| "Read back stored event payloads (metrics, tool-call scores)" | [`brain_query_events`](#3-brain_query_events) |
| "Edge X turned out wrong / right" | [`brain_record_feedback`](#6-brain_record_feedback) |

If a KG tool returns nothing useful, fall back to `brain_recall`. The KG is an *additional* surface, not a replacement.

---

## 2. `brain_record_event`

Writes one `ExperienceEvent` row plus optional **memory + entities + edges + evidence** in a **single Postgres transaction**. Any failure on any side-effect rolls back the entire transaction — including the event row.

### MCP signature

```jsonc
{
  "tool": "brain_record_event",
  "arguments": {
    "event_type": "workflow_completed",         // required — see "event types" below
    "subject_key": "recall-worked-well",        // optional — primary memory key this event relates to
    "utility_score": 0.85,                      // [0, 1], default 0.0
    "payload_json": "{\"steps\": 4}",           // optional JSON-serialised dict
    "entities_json": "[]",                      // optional JSON-serialised list[EntitySpec]
    "edges_json": "[]",                         // optional JSON-serialised list[EdgeSpec]
    "evidence_json": "[]",                      // optional JSON-serialised list[EvidenceSpec]
    "memory_key": "recall-worked-well",         // optional — when memory_key+memory_value both set, atomic memory write
    "memory_value": "Hybrid recall improved precision on architecture queries.",
    "memory_tier": "pattern",                   // default "pattern"
    "session_id": "sess-001",                   // optional
    "workflow_run_id": "wf-001",                // optional
    "agent_id": ""                              // optional — overrides server-level default
  }
}
```

### Response

```json
{
  "event_id": "<uuid>",
  "memory_key": "recall-worked-well" | null,
  "entity_ids": ["<uuid>", ...],
  "edge_ids":   ["<uuid>", ...],
  "evidence_ids": ["<uuid>", ...]
}
```

`entity_ids`, `edge_ids`, `evidence_ids` are returned in the **input order** of the corresponding spec lists.

### `event_type` — supported values

Freeform TEXT (no DB constraint), but the toolchain recognises these:

| `event_type` | When |
|---|---|
| `workflow_completed` | A multi-step pipeline or agent loop finished successfully. |
| `tool_called` | An external tool or API was invoked and returned. |
| `approach_failed` | An attempted approach did not succeed; captures the failure context. |
| `memory_recalled` | A recall query was executed and consumed. |

For anything else, pick a snake_case Object-Action label (e.g. `migration_applied`, `pr_merged`).

### Input spec dataclasses

All four come from [`tapps_brain.experience`](../../src/tapps_brain/experience.py) and are Pydantic models — the MCP wrapper accepts them as JSON.

#### `EntitySpec`

```python
{
  "entity_type": "workflow",          // required — ontology type ("module", "service", "concept", "workflow", ...)
  "canonical_name": "plan_and_implement",  // required
  "aliases": ["plan-and-implement"],  // optional
  "metadata": {"owner": "team-x"},    // optional JSONB
  "confidence": 0.9,                  // [0, 1], default 0.6
  "source": "agent"                   // provenance tag, default "agent"
}
```

Entities are upserted on `(brain_id, entity_type, canonical_name)`. If one already exists with the same canonical name, you get its existing UUID back.

#### `EdgeSpec`

```python
{
  # Endpoints — supply UUIDs OR keys/refs referencing entities in the same event:
  "subject_entity_id": "<uuid>",      // pre-resolved UUID (classic path)
  "object_entity_id": "<uuid>",
  "subject_key": "agentforge",        // canonical_name from same-event entities (v3.25+)
  "object_key": "task-123",
  "subject_ref": {"type": "agent", "id": "ralph"},  // typed disambiguation
  "object_ref": {"entity_type": "task", "canonical_name": "task-123"},
  "predicate": "depends_on",          // required
  "edge_class": "static",             // optional tag
  "layer": "pattern",                 // optional — MemoryTier name
  "profile_name": "repo-brain",       // optional — profile that defined this edge type
  "confidence": 0.8,                  // [0, 1], default 0.6
  "source": "agent",
  "metadata": {}
}
```

Endpoints may be **pre-resolved UUIDs** or **canonical keys** referencing entities
upserted in the same `brain_record_event` / `POST /v1/experience` call (TAP-3248).
When keys cannot be resolved against same-event entities, the edge is skipped and
a `warnings` entry is returned (TAP-2866) — the core event still commits.

For entities defined in a prior request, resolve UUIDs via:

1. **`POST /v1/kg/resolve_entity`** — single `(entity_type, canonical_name)` (TAP-2725).
2. **`POST /v1/kg/resolve_entities`** — batch `entity_refs[]` (TAP-3249).
3. Include entities in the same call and use `subject_key`/`object_key` (simplest).

#### `EvidenceSpec`

Every edge **must** have at least one evidence row (ADR-012). The XOR constraint requires exactly one of `edge_id` or `entity_id` set:

```python
{
  "edge_id": "<uuid>",                // XOR with entity_id
  "entity_id": null,
  "source_type": "agent",             // category — "agent", "doc", "test", "log", ...
  "source_id": "session-abc",         // opaque source system id
  "source_key": "memory-key-xyz",     // key within the source
  "source_uri": "https://...",        // optional URL
  "source_hash": "<sha256>",          // for dedup
  "source_span": "L120-L142",         // span/offset
  "quote": "verbatim excerpt",        // direct quote from the source
  "metadata": {},
  "confidence": 1.0,                  // [0, 1], default 1.0
  "utility_score": 0.7                // optional [0, 1]
}
```

**Why evidence is required:** ADR-012 — bare triples cause silent contradictions, stale inference, and break security auditing. No evidence = no edge.

#### `MemorySpec`

```python
{
  "key": "workflow-result",           // ≤ 128 chars
  "value": "Plan succeeded with 12 recall hits.",  // ≤ 4096 chars
  "tier": "pattern",                  // default "pattern"
  "confidence": 0.8,                  // [0, 1], default 0.8
  "tags": ["workflow"],               // max 10
  "agent_scope": "private"            // default "private"
}
```

The atomic `MemorySpec` write is **minimal** — it uses DB defaults for decay, FSRS, provenance fields. When you need full-featured writes (e.g. explicit `temporal_sensitivity`, `failed_approaches`, `memory_group`), use `brain_remember` separately and reference the resulting key via `subject_key` on a later `brain_record_event`.

### Example: capture a workflow completion with a touched-entity edge

```jsonc
{
  "tool": "brain_record_event",
  "arguments": {
    "event_type": "workflow_completed",
    "utility_score": 0.85,
    "payload_json": "{\"workflow\":\"plan_and_implement\",\"steps\":4}",
    "memory_key": "plan-and-implement-worked",
    "memory_value": "plan_and_implement completed successfully on the auth-rewrite branch.",
    "memory_tier": "pattern",
    "entities_json": "[{\"entity_type\":\"workflow\",\"canonical_name\":\"plan_and_implement\"},{\"entity_type\":\"branch\",\"canonical_name\":\"auth-rewrite\"}]",
    "evidence_json": "[]"
  }
}
```

The response gives you two `entity_ids`. A follow-up call can use those UUIDs in an `EdgeSpec` to record `workflow:plan_and_implement --executed_on--> branch:auth-rewrite`. Don't forget the evidence row on that follow-up edge.

---

## 3. `brain_query_events`

**v3.24.0+ (TAP-3157).** Reads rows from `experience_events` with full `payload` JSONB round-trip. Use for `quality_metric`, `quality_gate_fail`, and `checklist_outcome` events — **not** for KG neighbourhood structure (`brain_get_neighbors` returns edges/entities only).

### MCP signature

```jsonc
{
  "tool": "brain_query_events",
  "arguments": {
    "event_type": "quality_metric",              // required
    "since": "2026-06-01T00:00:00Z",           // optional — inclusive lower bound on event_time
    "until": "2026-06-09T23:59:59Z",           // optional — inclusive upper bound
    "entity_id": "src/tapps_brain/store.py",   // optional — matches payload.file_path OR subject_key (v1)
    "limit": 100                                 // default 100, server cap 500
  }
}
```

### Response shape

```jsonc
{
  "events": [
    {
      "event_id": "uuid",
      "event_type": "quality_metric",
      "payload": { "score": 91.0, "file_path": "...", "gate_passed": true, ... },
      "ts": "2026-06-09T12:00:01.123456+00:00",
      "agent_id": "cursor-agent",
      "session_id": "optional"
    }
  ],
  "count": 1
}
```

REST mirror: `POST /v1/experience:query` with the same JSON body + `X-Project-Id` header. Registered in `full`, `operator` (deferred), and `reviewer` profiles.

See [`experience-events.md`](../engineering/experience-events.md) for the `quality_metric` contract and a record→query smoke example.

---

## 4. `brain_get_neighbors`

Returns the 1- or 2-hop neighbourhood graph around one or more entities in a single SQL round-trip.

### MCP signature

```jsonc
{
  "tool": "brain_get_neighbors",
  "arguments": {
    "entity_ids_json": "[\"<uuid1>\",\"<uuid2>\"]",  // required — JSON array of UUIDs
    "hops": 1,                                       // 1 or 2 (>2 clamped to 2)
    "limit": 20,                                     // max edge rows, capped at 200
    "predicate_filter": "depends_on",                // optional substring filter
    "agent_id": ""
  }
}
```

### Response

```json
{
  "neighbors": [
    {
      "edge_id": "<uuid>",
      "predicate": "depends_on",
      "edge_confidence": 0.82,
      "direction": "out" | "in",
      "neighbor_id": "<uuid>",
      "entity_type": "module",
      "canonical_name": "auth.session",
      "hop": 1
    },
    ...
  ],
  "entity_ids": ["<uuid1>", "<uuid2>"]
}
```

### When I'd use it

- Before changing a module: "what depends on this and what does it depend on?"
- After recalling a concept: "what other entities cluster around this idea?"
- For onboarding-style summaries: "give me the immediate context graph around this service."

### Anti-pattern

Don't fetch huge neighbourhoods to scan visually. If `limit=200` returns 200 edges, narrow with `predicate_filter` or recall the concept first to get a tighter starting set.

---

## 5. `brain_explain_connection`

Finds the **shortest path** (≤ 3 hops) between two entities via BFS over the active edge graph.

### MCP signature

```jsonc
{
  "tool": "brain_explain_connection",
  "arguments": {
    "subject_id": "<uuid>",   // required
    "object_id": "<uuid>",    // required
    "max_hops": 3,            // clamped to [1, 3], default 3
    "agent_id": ""
  }
}
```

### Response

```json
{
  "found": true,
  "hops": 2,
  "subject_id": "<uuid>",
  "object_id": "<uuid>",
  "path": [
    { "entity_id": "<subject_uuid>" },
    { "edge_id": "<uuid>", "predicate": "depends_on", "direction": "out",
      "entity_id": "<intermediate_uuid>", "entity_type": "module", "canonical_name": "auth.session" },
    { "edge_id": "<uuid>", "predicate": "writes_to", "direction": "out",
      "entity_id": "<object_uuid>", "entity_type": "table", "canonical_name": "sessions" }
  ]
}
```

When no path exists within `max_hops`, `found=false` and `path=[]`.

### When I'd use it

- "Why is failing test X related to refactor Y?" — the path is the rationale.
- "Show me how the workflow that broke yesterday touches the database I'm about to migrate."

### Cost note

Each hop does one `get_neighbors` SQL call (limit 50 per step). A `max_hops=3` BFS on a richly connected graph can fan out — use `max_hops=2` first and widen only if needed.

---

## 6. `brain_record_feedback`

Records feedback on **either** a KG edge **or** a private memory entry — the routing depends on which subject identifier is set.

### MCP signature

```jsonc
{
  "tool": "brain_record_feedback",
  "arguments": {
    "feedback_type": "edge_helpful",  // "edge_helpful" | "edge_misleading" (edge path)
                                      // OR any snake_case Object-Action (memory path)
    "edge_id": "<uuid>",              // sets edge-feedback path
    "entry_key": "",                  // sets memory-feedback path (ignored when edge_id set)
    "session_id": "",
    "utility_score": 0.0,             // memory path only
    "details_json": "",               // memory path only — JSON dict
    "agent_id": ""
  }
}
```

### Edge feedback path (`edge_id` set)

Two-phase write:

1. **FeedbackStore audit trail** — lands in `feedback_events` with the edge UUID as `entry_key` so EWMA diagnostics see it.
2. **KG counter + confidence update** — calls `apply_edge_feedback` to bump `useful_access_count`, `positive/negative_feedback_count`, and edge confidence (reduced by ~0.05 per `edge_misleading`).

Accepted `feedback_type` values: `"edge_helpful"` or `"edge_misleading"`. Anything else returns `{"error":"bad_request", ...}`.

### Memory feedback path (`entry_key` set, no `edge_id`)

Routes through `MemoryStore.record_feedback()` as a generic `FeedbackEvent`. Any Object-Action snake_case name is accepted — including the standard `"recall_rated"`, `"gap_reported"`, `"issue_flagged"`.

### Response

```json
{
  "recorded": true,
  "feedback_type": "edge_helpful",
  "edge_id": "<uuid>" | null,
  "entry_key": "..." | null,
  "kg_update": { ... }            // edge path only — counter+confidence delta details
}
```

### When I'd use it

- After `brain_explain_connection` returns a path I actually used to make a decision → `edge_helpful` on each step's `edge_id`.
- When an edge contradicts current code or facts → `edge_misleading`. The confidence drop propagates to future BFS scoring.

---

## 7. Constraints that protect KG quality

| Constraint | What it means for agents |
|---|---|
| **Evidence required for every edge** (ADR-012) | An `EdgeSpec` without a matching `EvidenceSpec` is rejected by the FK constraint — the whole transaction rolls back. Always pair edges with at least one evidence row. |
| **Edges inherit memory lifecycle** (ADR-013) | Edges have `confidence`, `status`, `last_reinforced`, `temporal_sensitivity`, etc. — the same decay and GC logic as `private_memories`. Stale edges drop in score; `edge_misleading` accelerates decay. |
| **Tenant isolation via RLS** (ADR-011) | The recorder sets `app.project_id` automatically; you can never read or write another tenant's edges. Cross-tenant writes fail with a permission error. |
| **Inferred edge confidence cap** | When the recorder is invoked with `evidence_required=False` (an internal path), edge confidence is capped at 0.4. The MCP tool always treats your edges as agent-asserted; supply evidence accordingly. |
| **Single-transaction atomicity** | One `brain_record_event` call is all-or-nothing. A bad `EdgeSpec` UUID rolls back the event itself — there are no orphan events. |
| **Monthly-partitioned event log** | `experience_events` is range-partitioned (`event_time`); the recorder lets Postgres route rows. Pre-created partitions cover 2026-05 through 2027-04 plus a default. No agent action needed. |
| **Predicate registry, open by default** (TAP-5508) | A project may declare a predicate's cardinality via `brain_register_predicate`. Unregistered predicates stay writable — the registry describes predicates, it does not own them. Projects opt into rejection with `kg.strict_predicates`. |

### 7.1 Declaring predicate cardinality (TAP-5508)

Predicates are free-form TEXT. That is fine for exploratory graphs and useless
for ledger questions: you cannot ask "was this order refunded twice" when
nothing declares that `refunded` is functional.

`brain_register_predicate` records what a predicate means for your project:

| Field | Meaning |
|---|---|
| `predicate` | The label, e.g. `refunded` |
| `max_count` | Active objects one subject may hold. `0`/omitted = unbounded; `1` = functional edge |
| `domain_type` | Optional `entity_type` the subject must be |
| `range_type` | Optional `entity_type` the object must be |
| `description` | Free text for humans reading the registry |

`brain_list_predicates` returns the declarations; an empty registry is
`{"count": 0, "predicates": []}`, not an error.

REST equivalents: `POST /v1/kg/predicates:register` and
`POST /v1/kg/predicates:list`.

**Registration is descriptive today.** It does not retroactively validate
existing edges, and nothing rejects a write yet — enforcement of `max_count` on
the upsert path is TAP-5510. Declaring first means you can see what *would* be
rejected before anything is.

**Open by default.** An unregistered predicate stays legal. Existing graphs use
free-form predicates, and a registry that rejected them on introduction would
break every current writer to buy a guarantee nobody has asked for yet. Set
`kg.strict_predicates: true` in the project profile to opt into rejection.

Declarations are tenant-scoped under the same fail-closed RLS as `kg_edges`:
one project can neither read nor overwrite another's.

### 7.2 Checking before you write (TAP-5509)

`brain_kg_check` (REST: `POST /v1/kg/check`) answers whether asserting an edge
would violate a declared cardinality. Sections 3–5 are retrieval; this is a
*decision*, meant to be asked before a side effect.

Request: `subject_key`, `predicate`, optional `object_key`, and optional
`subject_type` / `object_type` to disambiguate a name shared by several types.

Response: `{ "decision": "allow"|"deny", "count", "max_count", "reason" }` —
the count and the limit ride along so a caller can *explain* a refusal rather
than just report one.

| `reason` | Decision | Meaning |
|---|---|---|
| `predicate_not_registered` | allow | Nothing declared, nothing to enforce |
| `predicate_unbounded` | allow | Registered with no `max_count` |
| `subject_not_found` | allow | No edges exist, so no limit can be exceeded |
| `edge_already_asserted` | allow | Re-asserting an existing edge adds no object |
| `within_cardinality` | allow | Under the declared limit |
| `cardinality_exceeded` | deny | At or over the declared limit |

Three properties worth relying on:

1. **Read-only.** Nothing is written, so it is safe to ask speculatively.
2. **A `deny` is a `200`.** The question was answered successfully and the
   answer was no. Non-2xx is reserved for malformed requests, which keeps
   "I could not ask" distinguishable from "I asked, and the answer was no".
3. **Ambiguity is never guessed.** A `subject_key` matching two entity types
   returns `invalid_request` telling you to pass `subject_type` — guessing would
   hand you a verdict about the wrong entity.

### 7.3 Enforcement on write (TAP-5510)

The check above is advisory on its own: an agent that skips it can still write
the edge, so the ledger would guarantee nothing. `upsert_edge` therefore applies
the same rule on the write path.

**Declaring a `max_count` is the request to enforce it.** There is no separate
enforcement switch — if you registered `refunded` with `max_count=1`, a second
distinct object for the same subject is rejected with a `conflict` (409):

```json
{"error": "conflict",
 "message": "predicate 'refunded' declares max_count=1; subject already holds 1 object(s), so this edge would exceed it"}
```

The error names the predicate, the limit, and the current count, because a
rollback the caller cannot explain is a rollback they will simply retry.

**Reinforcement is never blocked.** `upsert_edge` reinforces an existing
`(subject, predicate, object)` triple and returns *before* the gate, so
re-asserting an edge you already wrote always succeeds — even at the limit. It
adds no object.

**A violation rolls back the whole event.** The check runs on the same cursor as
the insert, inside the caller's transaction. `brain_record_event` is already
all-or-nothing, so a rejected edge leaves no partial event and no orphan
entities — and no window where a concurrent writer could slip past a check that
had already passed.

| Mode | Unregistered predicate | Registered, no `max_count` | Registered with `max_count` |
|---|---|---|---|
| **open** (default) | allowed | allowed | enforced |
| **strict** (`kg.strict_predicates: true`) | rejected | allowed | enforced |

Strict mode is resolved from the project profile once per process and **fails
open**: if the profile cannot be read, the project is treated as never having
asked for strict mode. A config error should not start rejecting writes for a
reason unrelated to what was written.

No OWL or SHACL is involved — this is a counted ledger invariant, not a
reasoner.

---

## 8. Failure modes

| Response | Cause | Recovery |
|---|---|---|
| `{"error":"db_unavailable", ...}` | `TAPPS_BRAIN_DATABASE_URL` (or `TAPPS_BRAIN_HIVE_DSN`) not set on the brain process. | Operator config issue. Surface the error; don't retry. |
| `{"error":"bad_request","detail":"feedback_type must be one of [...]"}` (edge feedback) | Used `feedback_type` other than `edge_helpful` / `edge_misleading` with `edge_id` set. | Use one of the two allowed values for edge path, or drop `edge_id` for memory feedback. |
| `{"error":"bad_request","detail":"subject_id and object_id are required."}` | `brain_explain_connection` called with empty IDs. | Resolve entities first via `brain_get_neighbors` or `brain_record_event` (entity upsert). |
| `psycopg.DatabaseError` (event recorder) | Constraint, RLS, or FK violation (e.g. `EdgeSpec` references an unknown UUID, or evidence missing). | Inspect the constraint name in the message. The whole transaction rolled back; no partial state to clean up. |
| `kg_update: "skipped_no_db"` (edge feedback) | FeedbackStore audit succeeded but KG counter step couldn't reach Postgres. | Feedback is still recorded; counter is best-effort and can be backfilled. |

---

## 9. Common patterns

### Capture a successful workflow run and its touched entities

```jsonc
{
  "tool": "brain_record_event",
  "arguments": {
    "event_type": "workflow_completed",
    "utility_score": 0.9,
    "memory_key": "auth-rewrite-complete",
    "memory_value": "auth-rewrite branch shipped via plan_and_implement; 14 tests added, zero regressions.",
    "memory_tier": "pattern",
    "entities_json": "[{\"entity_type\":\"workflow\",\"canonical_name\":\"plan_and_implement\"},{\"entity_type\":\"branch\",\"canonical_name\":\"auth-rewrite\"}]",
    "payload_json": "{\"tests_added\":14,\"duration_min\":42}"
  }
}
```

### Record a failed approach to keep future agents from repeating it

```jsonc
{
  "tool": "brain_record_event",
  "arguments": {
    "event_type": "approach_failed",
    "utility_score": 0.0,
    "memory_key": "avoid-direct-sql-under-load",
    "memory_value": "Direct psycopg pool access fails under 50+ concurrent agents; use HTTP API.",
    "memory_tier": "procedural",
    "payload_json": "{\"approach\":\"direct_sql_pool\",\"reason\":\"pool_timeout\",\"attempt\":2}"
  }
}
```

### "What modules touch the sessions table?"

1. Resolve the `sessions` entity UUID (record it once if missing, capture its `entity_ids[0]`).
2. `brain_get_neighbors(entity_ids_json="[\"<sessions_uuid>\"]", hops=1, predicate_filter="writes_to")`.

### "Why is module X relevant to incident Y?"

`brain_explain_connection(subject_id=<X>, object_id=<Y>, max_hops=2)` — read the `path` array; each step's `predicate` is part of the rationale.

### Mark an edge that turned out wrong

```jsonc
{
  "tool": "brain_record_feedback",
  "arguments": {
    "feedback_type": "edge_misleading",
    "edge_id": "<uuid>",
    "session_id": "sess-001"
  }
}
```

---

## 10. Anti-patterns

- **Don't put plain facts in the KG.** "We use Tailwind for styling" is a `brain_remember(tier="architectural")`, not an entity + edge. The KG is for *relationships*; recall handles facts.
- **Don't write edges without evidence.** Pre-bake an `EvidenceSpec` in every `brain_record_event` that includes an `EdgeSpec`. ADR-012 will reject you otherwise; the rejection rolls back the whole event.
- **Don't construct EdgeSpec UUIDs by hand.** Always get UUIDs from a prior `brain_record_event` response (or from the KG store's resolver). Made-up UUIDs fail FK validation and roll back the transaction.
- **Don't run `brain_explain_connection` blind.** If you don't already have the two entity UUIDs, recall and `brain_get_neighbors` first to discover them.
- **Don't use `brain_record_feedback` with `entry_key` set when you have an `edge_id`.** The router treats `edge_id` as authoritative; `entry_key` is ignored in that case.
- **Don't poll `brain_get_neighbors` with `limit=200, hops=2` looking for "everything".** That's a query-fanout antipattern; narrow with `predicate_filter` or recall first.

---

## 11. Related docs

- [`agent-playbook.md`](agent-playbook.md) — the broader recall/remember/forget decision tree.
- [`kg-experience-flow.md`](kg-experience-flow.md) — populate-then-query: KG neighbours vs `brain_query_events`.
- [`mcp-tools-for-agents.md`](mcp-tools-for-agents.md) — every MCP tool with "when I'd reach for it" notes.
- [`docs/engineering/experience-events.md`](../engineering/experience-events.md) — full `experience_events` schema, `brain_query_events` API, and payload examples.
- [`ADR-011`](../planning/adr/ADR-011-kg-schema-postgres.md) — why the KG lives in Postgres with RLS (not a separate graph DB).
- [`ADR-012`](../planning/adr/ADR-012-evidence-required-edges.md) — why every edge needs evidence.
- [`ADR-013`](../planning/adr/ADR-013-kg-inherits-memory-lifecycle.md) — why entities and edges share the `MemoryEntry` lifecycle fields.
- [`src/tapps_brain/experience.py`](../../src/tapps_brain/experience.py) — full Pydantic spec definitions (the source of truth).
- [`src/tapps_brain/services/kg_service.py`](../../src/tapps_brain/services/kg_service.py) — service-layer functions called by the MCP wrappers.
