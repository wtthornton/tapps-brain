# Gated learning + mission-scoped memory contract (TAP-5539)

Request/response shapes for **AgentForge BrainBridge** to bind against.

> **Status: proposed, not yet implemented.** Nothing below is live on
> `tapps-brain` 3.29.0. This document exists so AF can build against a fixed
> contract in parallel with brain-side implementation rather than after it.
> Each section names the story that lands it. Treat a call to any endpoint here
> as a 404 until its story is marked Done.

AF's fleet-learning side channel (TAP-5532) shipped 2026-08-04 and its
`learning_injection` path needs *promoted* tool-path learnings. Brain has no
promotion model today — that gap is what this contract closes.

## Design: promotion is a separate axis from lifecycle

`private_memories` already carries `status` (migration 027) with values
`active | stale | superseded | archived`. That is a **lifecycle** axis: is this
row live?

Promotion is a **trust** axis: has this learning been validated? The two are
independent — an `active` row can be `candidate`, and an `approved` learning can
later go `stale`. Overloading one column would make `approved` and `stale`
mutually exclusive, which is wrong.

So promotion lands as a new `learning_status` column (migration 030, TAP-5542):

| Value | Meaning |
|-------|---------|
| `candidate` | Default for any agent-emitted learning. Not eligible for injection. |
| `approved` | Passed an explicit gate. Eligible for injection. |
| `demoted` | Was approved, then contradicted or decayed. Not eligible. |

Provenance columns land alongside it: `promoted_by`, `promoted_at`,
`promotion_signal`, `demotion_reason`.

### The load-bearing rule

**Frequency alone cannot approve.** `reinforce()` raises confidence and access
count; it must never move `candidate → approved`. Promotion requires an explicit
`signal` of `eval` or `human`. This is the whole point of the epic — a learning
that is merely *frequent* is not thereby *correct*.

Corollary for consumers: do not treat a high-confidence `candidate` as approved.
Filter on `learning_status`, not on confidence.

## Endpoints

Naming follows the existing surface: colon-suffixed verbs for actions
(`/v1/experience:query`, `/v1/documents:search`), path segments for resources.

### `POST /v1/learning:promote` — TAP-5542

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `key` | string | required | Memory key to promote |
| `signal` | string | required | `eval` \| `human`. No other value accepted. |
| `actor` | string | required | Eval run id, or human identifier |
| `evidence` | string | optional | Free text or eval artifact reference |

Returns `409` when the entry is already `approved`, `404` when the key is
unknown. Promoting a `demoted` entry is allowed and clears `demotion_reason`.

### `POST /v1/learning:demote` — TAP-5542

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `key` | string | required | Memory key to demote |
| `reason` | string | required | Why; stored in `demotion_reason` |

### `POST /v1/recall:tool_paths` — TAP-5545

The call AF's `learning_injection` makes. Returns tool paths that previously
succeeded for a task type.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `task_type` | string | required | Task classifier the path succeeded on |
| `limit` | int | `5` | 1–50 |
| `learning_status` | string | `"approved"` | Defaults to approved-only. Pass `"any"` to include candidates. |
| `min_confidence` | float | profile default | Standard recall filter |

**The default is `approved`.** A consumer that wants candidates must ask for
them explicitly, so the safe path is the default path.

### `POST /v1/mission/state:set` and `:get` — TAP-5544

Mission/run-scoped shared state, so a fresh worker can pick up a mission without
inheriting another agent's trajectory.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `mission_id` | string | required | Scope key |
| `kind` | string | required | `contract` \| `findings` \| `knowledge` |
| `value` | object | required on set | JSON payload |
| `run_id` | string | optional | Narrows scope within a mission |

Scoped state is tenant-isolated by the same `(project_id, agent_id)` rules as
private memory; RLS is unchanged.

## What is deliberately not here

1. No OWL/SHACL reasoner, and no Neo4j.
2. No enterprise ontology prerequisite — domain packs (TAP-5546) are optional and versioned.
3. Brain does not invent consumer domain predicates; AF and personal-ops own their packs.
4. Recall is not replaced by graph-only routing.

## Stories

1. TAP-5542 — learning statuses + provenance (migration 030)
2. TAP-5544 — mission-scoped shared state APIs
3. TAP-5545 — similar successful tool-path retrieval
4. TAP-5547 — decay unvalidated and contradicted learnings
5. TAP-5546 — versioned domain packs (optional)

## Refs

1. Epic TAP-5539
2. AF consumers: TAP-5526 (Missions validation loop), TAP-5532 (fleet learning side channel, Done)
3. Contract-doc precedent: `docs/engineering/web-research-contract.md`
4. Lifecycle status column: `src/tapps_brain/migrations/private/027_memory_status.sql`
