# Gated learning + mission-scoped memory contract (TAP-5539)

Request/response shapes for **AgentForge BrainBridge** to bind against.

> **Status: partially implemented.** `POST /v1/learning:promote` and
> `POST /v1/learning:demote` are **live** (TAP-5542, migration 030). Everything
> else below is still proposed — treat a call to those endpoints as a 404 until
> the story named in its section is marked Done. This document exists so AF can
> build against a fixed contract in parallel with brain-side implementation
> rather than after it.

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

### `POST /v1/learning:promote` — TAP-5542 (**live**)

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `key` | string | required | Memory key to promote |
| `signal` | string | required | `eval` \| `human`. No other value accepted. |
| `actor` | string | required | Eval run id, or human identifier |
| `evidence` | string | optional | Free text or eval artifact reference; recorded in the audit log |

Returns `409` when the entry is already `approved`, `404` when the key is
unknown, `400` on a bad `signal` or an empty `actor`. Promoting a `demoted`
entry is allowed and clears `demotion_reason`. Success body:

```json
{"promoted": true, "key": "...", "learning_status": "approved",
 "promoted_by": "...", "promoted_at": "...", "promotion_signal": "eval",
 "demotion_reason": null}
```

Errors use the standard envelope — `{"error": "conflict" | "not_found" |
"invalid_request", "message": "..."}`.

MCP equivalent: `brain_promote_learning`. Exposed in the `full`, `operator`,
and `agent_brain` profiles; deliberately **not** in `coder` — a coding agent
approving its own learnings is the gate approving itself.

Note on scope: this is a *provenance* gate, not an authorization gate. It
records who approved a learning and on what signal; it does not verify that the
caller is entitled to be that actor. Deployments that need that should gate the
endpoint at the auth layer.

### `POST /v1/learning:demote` — TAP-5542 (**live**)

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `key` | string | required | Memory key to demote |
| `reason` | string | required | Why; stored in `demotion_reason` |

Returns `404` for an unknown key and `400` for an empty `reason`. Promotion
provenance is retained, so an audit can still see which approval was withdrawn.

### Re-saving an approved entry

Approval is bound to content. A save that changes an entry's `value` resets it
to `candidate` and clears the promotion provenance; a metadata-only save (same
value) keeps the approval. Without this, anyone who can save could launder new
content through an old approval.

### `POST /v1/recall:tool_paths` — TAP-5545 (**live**)

The call AF's `learning_injection` makes. Returns tool paths that previously
succeeded for a task type.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `task_type` | string | required | Task classifier the path succeeded on |
| `limit` | int | `5` | 1–50; outside that range is a `400` |
| `learning_status` | string | `"approved"` | Defaults to approved-only. Pass `"any"` to include candidates. Any other value is a `400`. |
| `min_confidence` | float | unset | Optional floor; omitted means no confidence filter |

**The default is `approved`.** A consumer that wants candidates must ask for
them explicitly, so the safe path is the default path.

**`demoted` is never returned**, not even under `learning_status: "any"`.
Demotion is a withdrawal; no opt-in brings a withdrawn learning back.

**Approval alone is not enough.** An entry also has to carry the tool-path tag
convention — a `fleet:learning` tag or any `tool:*` tag — mirroring the gate in
AF's `candidates_from_tool_path_learnings`. An approved architectural decision
is a valid learning but not a tool path, and injecting it into a routing
decision would be noise.

**Fail-closed, not fail-as-error.** When nothing approved matches, the response
is `200` with `count: 0` and an empty `tool_paths` — never a `404`, and never a
silent downgrade to candidates. A caller that asked for validated learnings and
got unvetted ones cannot tell the difference, which is worse than getting
nothing.

Success body:

```json
{"count": 1, "learning_status": "approved",
 "tool_paths": [{"key": "...", "value": "...", "tags": ["tool:ruff"],
                 "confidence": 0.8, "project_id": "...",
                 "learning_status": "approved", "promoted_by": "eval-run-1",
                 "promoted_at": "...", "promotion_signal": "eval"}]}
```

Item fields are shaped for AF's `candidates_from_tool_path_learnings`
(`value` / `tags` / `confidence` / `project_id`), so the consumer needs no
translation layer. `promoted_by` and `promotion_signal` ride along so an audit
of a bad injection can see what approved it.

MCP equivalent: `brain_recall_tool_paths`. Exposed in `full`, `operator`,
`coder`, and `agent_brain`. Unlike promote/demote it **is** in `coder` —
reading approved tool paths is the consumption side of the gate, not the
approval side. It is absent from `reviewer`, which reviews diffs and has no
routing decision to make.

### `POST /v1/mission/state:set` and `:get` — TAP-5544 (**live**)

Mission/run-scoped shared state, so a fresh worker can pick up a mission without
inheriting another agent's trajectory.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `mission_id` | string | required | Scope key. Lowercase slug (`[a-z0-9][a-z0-9_-]*`) — no dots. |
| `kind` | string | required | `contract` \| `findings` \| `knowledge` — a closed set |
| `value` | object | required on set | JSON payload; round-trips structurally |
| `run_id` | string | optional | Narrows scope within a mission |

Stored as a `scope="mission"` memory entry whose `mission_id` is the isolation
key (migration 031), following the `scope=branch` precedent — a `MemoryScope`
value plus a companion field that is required when that scope is set — rather
than adding a parallel store.

**Isolation.** Two missions under one `project_id` cannot read each other. This
is enforced twice on purpose: the storage key is mission-namespaced
(`mission.<mission_id>[.<run_id>].<kind>`), *and* the entry's own `mission_id`
is re-checked after the read, so a caller that guesses or forges another
mission's key still gets a miss rather than that mission's state. A DB CHECK
makes a `mission`-scoped row without a `mission_id` unrepresentable.

Note that the RLS policy from migration 009 gates on `project_id` only, so it
cannot provide mission isolation — the mission predicate does.

**A missing slot is `200` with `{"found": false, "value": null}`, not a `404`.**
"This mission has not parked its contract yet" is the normal state for a worker
picking up a mission, and it needs to be distinguishable from a failed call
without a try/except.

`mission_id` and `run_id` are validated against the key-slug rule and rejected
with a `400` when they do not match — including uppercase. They are not
silently lowercased, because folding `M-1` onto `m-1` would merge two distinct
missions into one slot.

Dots are rejected inside `mission_id` / `run_id` even though the key grammar
allows them, because `.` is the separator the key is composed with. Permitting
it makes the composition ambiguous — `mission_id="m.1"` with no run and
`mission_id="m", run_id="1"` both render `mission.m.1.<kind>`. Reads survive
that (the post-read `mission_id` check rejects both directions), but the *write*
does not: the second mission's save would overwrite the first's row, after which
neither mission can read its own state.

MCP equivalents: `brain_mission_state_set` / `brain_mission_state_get`, in the
`full`, `operator` and `agent_brain` profiles. Absent from `coder`: a
repo-embedded coding agent works a task, not a mission, so it has nothing to
park.

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
