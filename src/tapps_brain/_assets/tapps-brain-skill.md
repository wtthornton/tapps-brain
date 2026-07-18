---
name: tapps-brain
description: Persistent cross-session memory + knowledge graph for AI coding agents. Use when you need to recall prior decisions before making a non-trivial choice, save the rationale behind a decision so it survives the session, share findings across agents in a project (Hive), or record an experience event / KG triple. Talk to the deployed brain at http://127.0.0.1:8080/mcp/ via MCP tools — never via raw psycopg.
version: "3.25.0"
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
| Save a decision + rationale | `brain_remember(fact=..., tier=...)` | The rationale is the memory-worthy part, not the decision itself. Pick a tier (see below). |
| Share across agents | `brain_remember(..., agent_scope="hive")` | Modern primary param. `agent_scope` accepts `private` (default) / `domain` / `hive` / `group:<name>`. The legacy `share=True` / `share_with="hive"` flags still work but `agent_scope` supersedes them. Hive is a feature of the brain, not a separate service. |
| Teach from an outcome | `brain_learn_success(...)` / `brain_learn_failure(...)` | AgentBrain facade — records what worked / what to avoid so future recalls surface it. Pass `failed_approaches=[...]` on `brain_remember` for inline anti-patterns. |
| Query stored metrics / events | `brain_query_events(event_type=..., entity_id=...)` | v3.24.0+ — read back `experience_events.payload` (e.g. `quality_metric` scores by `file_path`). Not `brain_get_neighbors` (KG structure only). REST: `POST /v1/experience:query`. |

The eager `tools/list` catalog returns 8 daily-driver tools (`brain_recall`, `brain_remember`, `brain_status`, `brain_get_neighbors`, `brain_explain_connection`, `memory_search`, `memory_find_related`, `hive_search`). Non-daily-driver tools (`brain_learn_success`/`brain_learn_failure`, `brain_record_event`/`brain_record_events_batch`, `brain_query_events`, `brain_resolve_entity`, `brain_export`, `recall_quality_metrics`, `brain_audit_consumers`, the `memory_*`/`feedback_*` family) are deferred-loaded — still callable via `tools/call` and reachable via Anthropic Tool Search BETA (`advanced-tool-use-2025-11-20` header) — TAP-1985.

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

See [`docs/guides/mcp-client-repo-setup.md`](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/mcp-client-repo-setup.md) — covers `.mcp.json`, per-repo bearer token, profile selection, and the SessionStart hook that auto-primes recall on turn 1.

## Verify a running deployment

From the tapps-brain repo root (token in `.env` or `docker/.env`):

```bash
make brain-smoke-live     # canonical post-deploy HTTP gate
make brain-healthcheck    # live MCP round-trip (server-mode OK if IDE is bridge-only)
```

`make hive-smoke` boots an **isolated** stack on alternate ports and tears it down — use that in CI, not against the live `:8080` server.

## References

- **Canonical agent guide**: [`docs/guides/llm-brain-guide.md`](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/llm-brain-guide.md) — when to remember / recall / share, full tier guide, MCP tool examples.
- **AgentForge integrators**: [`docs/guides/agentforge-integration.md`](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/agentforge-integration.md) — Python `AgentBrain` SDK + HTTP `BrainBridge` paths.
- **Error taxonomy**: [`docs/guides/errors.md`](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/errors.md) — HTTP + JSON-RPC envelopes, retry policy.
- **HTTP surface**: [`docs/guides/http-adapter.md`](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/http-adapter.md) — `/healthz` phased body, `/v1/tools/list` ETag headers, `/v1/experience:query`.
- **Experience events + query API**: [`docs/engineering/experience-events.md`](https://github.com/wtthornton/tapps-brain/blob/main/docs/engineering/experience-events.md) — `quality_metric` contract, `brain_query_events` filters.
- **KG populate-then-query flow**: [`docs/guides/kg-experience-flow.md`](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/kg-experience-flow.md) — when to use neighbours vs event query.
- **Deploy + upgrade**: [`docs/guides/hive-deployment.md`](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/hive-deployment.md) — `make hive-deploy`, `BRAIN_VERSION` bump.
- **Latest release notes**: [`CHANGELOG.md`](https://github.com/wtthornton/tapps-brain/blob/main/CHANGELOG.md#3250--2026-06-24) — current at v3.25.0.
