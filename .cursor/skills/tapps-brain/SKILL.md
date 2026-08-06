---
name: tapps-brain
description: Persistent cross-session memory + knowledge graph for AI coding agents. Use when you need to recall prior decisions before making a non-trivial choice, save the rationale behind a decision so it survives the session, share findings across agents in a project (Hive), or record an experience event / KG triple. Talk to the deployed brain at http://127.0.0.1:8080/mcp/ via MCP tools — never via raw psycopg.
version: "3.30.0"
origin: tapps-brain
---

# /tapps-brain — Persistent Cross-Session Memory

A thin trigger for the deployed tapps-brain memory service. **Routes you to the canonical docs** rather than restating them.

## When to reach for this skill

- Starting a session in a repo that's wired to tapps-brain (`.mcp.json` references `tapps-brain-http` or a `brain_*` tool).
- About to make a non-trivial design choice — recall first, decide informed.
- A debug session reveals a subtle invariant worth saving for future sessions.
- The user asks "what did we decide about X" / "why is Y the way it is" / "have we seen this before."
- Multiple agents in the same project need to coordinate — Hive (shared memory).
- A task succeeded or failed in a way worth teaching future sessions (`brain_learn_success` / `brain_learn_failure`).
- Adding or querying experience events / KG triples (`brain_record_event` / `brain_record_events_batch`, `brain_query_events`, `brain_record_feedback`).

## Core operations

| You want to | Tool | Notes |
|---|---|---|
| Recall prior context | `brain_recall(query=...)` | BM25 + pgvector hybrid. Returns ranked memories with `quality_warning` when the diagnostics circuit is non-CLOSED. |
| Save a decision + rationale | `brain_remember(fact=..., tier=...)` | The rationale is the memory-worthy part, not the decision itself. Pick a tier (see below). **Read `status` — a 200 does not mean persisted** (see below). |
| Protect neighbours from supersede | `brain_remember(..., supersede="key-scoped")` | v3.29.0+. Default `"global"` lets a save close the validity interval of a textually similar entry in the same tier under *any* key. Use `"key-scoped"` for key-spaces of independent facts (one row per distinct thing), where topical similarity between neighbours is expected and is not a contradiction. |
| Share across agents | `brain_remember(..., agent_scope="hive")` | Modern primary param. `agent_scope` accepts `private` (default) / `domain` / `hive` / `group:<name>`. The legacy `share=True` / `share_with="hive"` flags still work but `agent_scope` supersedes them. Hive is a feature of the brain, not a separate service. |
| Teach from an outcome | `brain_learn_success(...)` / `brain_learn_failure(...)` | AgentBrain facade — records what worked / what to avoid so future recalls surface it. Pass `failed_approaches=[...]` on `brain_remember` for inline anti-patterns. |
| Query stored metrics / events | `brain_query_events(event_type=..., entity_id=...)` | v3.24.0+ — read back `experience_events.payload` (e.g. `quality_metric` scores by `file_path`). Not `brain_get_neighbors` (KG structure only). REST: `POST /v1/experience:query`. |
| Read only *validated* learnings | `brain_recall_tool_paths(task_type=...)` | v3.30.0+. Returns learnings that are `approved` **and** tagged `fleet:learning` / `tool:*`. Fail-closed: nothing approved yields `count: 0`, never a fallback to candidates and never a 404. `demoted` entries are never returned. REST: `POST /v1/recall:tool_paths`. |
| Approve / withdraw a learning | `brain_promote_learning(...)` / `brain_demote_learning(...)` | v3.30.0+. Only an explicit `eval` or `human` signal promotes — frequency never does, and `reinforce()` deliberately leaves `learning_status` alone. Absent from the `coder` profile: an agent approving its own learnings is the gate approving itself. |
| Park state for a mission | `brain_mission_state_set(...)` / `brain_mission_state_get(...)` | v3.30.0+. `kind` is `contract` / `findings` / `knowledge`. Two missions under one `project_id` cannot read each other. An unwritten slot is `found: false`, not a 404. REST: `POST /v1/mission/state:set` / `:get`. |
| Ask before an action (ledger) | `brain_kg_check(subject_key=..., predicate=...)` | v3.30.0+. Read-only allow/deny against declared predicate cardinality, with `count` + `max_count` + `reason` so you can explain a refusal. A `deny` is a `200`. Declare limits with `brain_register_predicate`. REST: `POST /v1/kg/check`. |

The eager `tools/list` catalog on the Docker reference stack returns **all** profile tools (defer_loading disabled) — 84 on the `full` profile. Daily-driver tools include `brain_recall`, `brain_remember`, `brain_status`, `brain_get_neighbors`, `brain_explain_connection`, `memory_search`, `memory_find_related`, `hive_search`, plus v3.24 helpers `brain_query_events`, `brain_profile_get`, `brain_profile_set` when using the `full` or `coder` profile.

### The save response contract (v3.28.3+)

`brain_remember` / `POST /v1/remember` returns a status you must read. A `200`
is not proof the write landed:

| `status` | Meaning |
|---|---|
| `saved` | Durable under the key you asked for. |
| `coalesced` | Folded onto another row. Carries `persisted: false` and `coalesced_into: <other key>` — **your key does not exist**. |

A save that closes a neighbouring entry's validity interval (removing it from
recall) lists those keys in `invalidated`. If you see keys there you did not
expect, the entries were superseded on similarity — pass
`supersede="key-scoped"` to stop that, or raise the tier's
`conflict_check.per_tier` threshold in the profile.

Before v3.28.3 a coalesced write reported `"saved"` while echoing a foreign key,
so acknowledged writes could silently not persist (TAP-5614).

## Pick a tier

| Tier | Half-life | What it's for |
|---|---|---|
| `architectural` | 180d | System decisions, tech-stack choices, infra contracts |
| `pattern` | 60d | Coding conventions, API shapes, design patterns |
| `procedural` | 30d | Workflows, build/deploy commands, runbooks |
| `context` | 14d | Session-scope facts; use sparingly |

Tag important entries with `critical` or `security` for ranking boost.

## Do NOT save

- Code patterns / file paths / module layout — derivable by reading the repo
- Git history, recent diffs, who-changed-what — `git log` / `git blame` are authoritative
- Ephemeral task state, current-conversation context, debug fix recipes — these belong in `TodoWrite` or the commit message
- Anything with secrets, tokens, or PII

## Decision: should I write to memory?

```
Did the user teach a non-obvious rule?              → YES (feedback)
Was a decision made WITH RATIONALE that isn't       → YES (architectural / pattern)
  obvious from the code or the PR body?
Did a debug session reveal a subtle invariant?      → YES (pattern, tag: critical)
Is this a TODO / next-step / "remember to do X"?    → NO (use TodoWrite)
Is this re-derivable by reading the repo?           → NO
Does this duplicate a CHANGELOG / CLAUDE.md entry?  → NO
```

## Error shapes to know

- **`bad_json`** (TAP-1967/1968/1969, v3.19.0+) — malformed `*_json` argument on `brain_record_event` / `brain_record_events_batch` / `brain_get_neighbors` / `brain_record_feedback`. Permanent (non-retryable) caller bug. Surface `data.field` + `data.detail`.
- **`bad_uuid`** (TAP-2726, v3.21.0+) — KG tools (`brain_get_neighbors`, `brain_explain_connection`, `brain_resolve_entity`) reject non-UUID entity ids. Permanent caller bug; check the id you passed.
- **`out_of_profile`** denial (TAP-1972, v3.19.0+) — error data includes `suggested_profile` when non-null; retry with `X-Brain-Profile: <suggested_profile>` to self-route.
- **HTTP 422** (TAP-2865, v3.22.1+) — malformed REST request payloads now return a typed `422` envelope instead of a masked `500`. Fix the request body, don't retry verbatim.

## Wire setup for a new repo

See [`docs/guides/mcp-client-repo-setup.md`](../../../docs/guides/mcp-client-repo-setup.md) — covers `.mcp.json`, per-repo bearer token, profile selection, and the SessionStart hook that auto-primes recall on turn 1.

## Upgrade local Docker (agents)

When the user asks to deploy or upgrade the **local** brain stack:

```bash
make dev-deploy              # default: code-only wheel + http image + smoke (~3–8 min)
MIGRATE=1 make dev-deploy    # when SQL under src/tapps_brain/migrations/ changed
make hive-deploy             # first-time only, or nginx/visual image changes
```

`docker/.env` must include non-empty `TAPPS_BRAIN_ALLOWED_ORIGINS` (compose sets `STRICT=1`). Without it the container crash-loops on `:8080`. Env-only change: `docker compose -p tapps-brain -f docker/docker-compose.hive.yaml up -d --no-deps --force-recreate tapps-brain-http`.

## Verify a running deployment

From the tapps-brain repo root (token in `.env` or `docker/.env`):

```bash
make brain-smoke-live     # canonical post-deploy HTTP gate
make brain-healthcheck    # live MCP round-trip (server-mode OK if IDE is bridge-only)
```

`make hive-smoke` boots an **isolated** stack on alternate ports and tears it down — use that in CI, not against the live `:8080` server.

## References

- **Canonical agent guide**: [`docs/guides/llm-brain-guide.md`](../../../docs/guides/llm-brain-guide.md) — when to remember / recall / share, full tier guide, MCP tool examples.
- **AgentForge integrators**: [`docs/guides/agentforge-integration.md`](../../../docs/guides/agentforge-integration.md) — Python `AgentBrain` SDK + HTTP `BrainBridge` paths.
- **Error taxonomy**: [`docs/guides/errors.md`](../../../docs/guides/errors.md) — HTTP + JSON-RPC envelopes, retry policy.
- **HTTP surface**: [`docs/guides/http-adapter.md`](../../../docs/guides/http-adapter.md) — `/healthz` phased body, `/v1/tools/list` ETag headers, `/v1/experience:query`.
- **Experience events + query API**: [`docs/engineering/experience-events.md`](../../../docs/engineering/experience-events.md) — `quality_metric` contract, `brain_query_events` filters.
- **KG populate-then-query flow**: [`docs/guides/kg-experience-flow.md`](../../../docs/guides/kg-experience-flow.md) — when to use neighbours vs event query.
- **Deploy + upgrade**: [`docs/guides/dev-docker-loop.md`](../../../docs/guides/dev-docker-loop.md) — `make dev-deploy` (fast), `MIGRATE=1`, `ALLOWED_ORIGINS` requirement. Production: [`hive-deployment.md`](../../../docs/guides/hive-deployment.md).
- **Latest release notes**: [`CHANGELOG.md`](../../../CHANGELOG.md#3300--2026-08-06) — current at v3.30.0.
