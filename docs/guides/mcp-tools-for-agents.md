# MCP tools for repo-embedded agents

Complete reference for every tool the deployed tapps-brain exposes over MCP (55 tools, verified live against `tapps-brain-http` on 2026-04-17). For each tool: purpose, arguments, and *when I'd actually reach for it* as a repo-embedded coding agent. Tools I genuinely would not use from a coding turn are marked **Not needed** with a reason.

Rule of thumb: prefer the **AgentBrain facade** (`brain_*`) for day-to-day work. Drop to `memory_*` only when the facade doesn't expose the knob I need. Everything else is either operator tooling, bulk jobs, internal sync primitives, or hook-driven.

Notation: `*` marks required args; everything else is optional. `agent_id` appears on most tools (STORY-070.7) and overrides the server-level default for a single call — omit it unless I'm intentionally impersonating another agent.

---

## 1. AgentBrain facade — the daily drivers

Simplified 5-method API on top of `MemoryStore` + `HiveBackend`. This is the primary surface for agents.

### `brain_remember`
Save a memory to the agent's brain.
- Args: `fact*`, `tier` (`architectural`/`pattern`/`procedural`/`context`, default `procedural`), `share` (default `False`), `share_with` (e.g. `"hive"`), `agent_id`.
- **When I use it:** when a decision carries rationale that isn't obvious from the code. Pick the tier by half-life (architectural 180d → context 14d). Never save git-derivable facts or secrets.

### `brain_recall`
Recall memories matching a query.
- Args: `query*`, `max_results` (default 5), `agent_id`.
- **When I use it:** **session start** (with the user's opening topic) and **before any non-trivial decision**. Primary read path.

### `brain_forget`
Archive (not delete) a memory by key.
- Args: `key*`, `agent_id`.
- **When I use it:** when a memory is explicitly contradicted by current code or a newer decision. Reversible — safe to use.

### `brain_learn_success`
Record a successful task outcome.
- Args: `task_description*`, `task_id`, `agent_id`.
- **When I use it:** after shipping a non-obvious win, to bank "X approach worked for Y problem".

### `brain_learn_failure`
Record a failed task outcome to avoid repeating mistakes.
- Args: `description*`, `task_id`, `error`, `agent_id`.
- **When I use it:** after a blind alley — "tried X, didn't work because Y". High ROI; cheap to log.

### `brain_status`
Show agent identity, group memberships, store stats, Hive connectivity.
- Args: `agent_id`.
- **When I use it:** rarely — when recall feels off and I want to confirm my `agent_id`, groups, or Hive health.

---

## 2. Memory CRUD — low-level store

The full knob set behind the facade. Reach here when the facade's defaults don't fit.

### `memory_save`
Save or update a memory entry.
- Args: `key*`, `value*`, `tier` (default `pattern`), `source` (default `agent`), `tags`, `scope` (default `project`), `confidence` (-1 for default), `agent_scope` (default `private`), `source_agent`, `group`, `agent_id`. Supports `_meta.idempotency_key` when `TAPPS_BRAIN_IDEMPOTENCY=1`.
- **When I use it:** when I need to set an explicit `key`, custom `scope` (e.g. `branch`), group membership, or a precise confidence. Otherwise `brain_remember`.

### `memory_get`
Retrieve a single memory entry by key.
- Args: `key*`, `agent_id`.
- **When I use it:** when I already have a key from a prior recall and want the full record — faster than re-searching.

### `memory_delete`
Permanently delete a memory entry.
- Args: `key*`, `agent_id`.
- **When I use it:** rarely — prefer `brain_forget` (archive, reversible). Only delete when something shouldn't exist at all (e.g., accidentally saved PII).

### `memory_search`
Full-text search over memory entries.
- Args: `query*`, `tier`, `scope`, `as_of`, `group`, `since`, `until`, `time_field` (default `created_at`), `agent_id`.
- **When I use it:** when I need time-bounded or scope-filtered hits. For everyday semantic recall, `brain_recall` is better (hybrid ranking).

### `memory_list`
List memory entries with optional filters.
- Args: `tier`, `scope`, `include_superseded`, `group`, `agent_id`.
- **When I use it:** rarely — for auditing what's in a tier or group. Not a coding-turn tool.

### `memory_list_groups`
List distinct project-local memory group names.
- Args: `agent_id`.
- **When I use it:** when setting up or tagging into groups; otherwise **not needed** in single-repo work.

### `memory_recall`
Run auto-recall for a message and return ranked memories.
- Args: `message*`, `group`, `agent_id`.
- **Not needed** — duplicates `brain_recall`. Use the facade.

### `memory_reinforce`
Boost confidence + reset decay on a hit.
- Args: `key*`, `confidence_boost` (default 0.0), `agent_id`. Idempotency-aware.
- **When I use it:** after a recalled memory was directly useful *this turn*. Cheap signal; worth sending.

### `memory_ingest`
Extract and store durable facts from conversation context.
- Args: `context*`, `source` (default `agent`), `agent_scope` (default `private`), `agent_id`.
- **When I use it:** rarely from a coding turn — mostly a bulk/seed operation. Usually **not needed**.

### `memory_supersede`
Create a new version of a memory, preserving the chain.
- Args: `old_key*`, `new_value*`, `key`, `tier`, `tags`, `agent_id`.
- **When I use it:** when a prior decision is revised. Keeps history; don't overwrite via `memory_save` when the old fact is real context.

### `memory_history`
Show the full version chain for a key.
- Args: `key*`, `agent_id`.
- **When I use it:** when a recall returns a superseded entry and I want to see the evolution.

---

## 3. Batch ops — operator territory

### `memory_save_many`
Save multiple entries in one call. Cap: `TAPPS_BRAIN_MAX_BATCH_SIZE` (default 100).
- Args: `entries*` (array of `{key, value, tier?, source?, tags?, scope?, confidence?, agent_scope?, group?}`), `agent_id`.
- **Not needed** — interactive coding writes one fact at a time. Reach here only for a deliberate seed script.

### `memory_recall_many`
Run recall against multiple queries in one call. Cap: 50 reads.
- Args: `queries*` (array of strings), `agent_id`.
- **Not needed** — a coding turn has one question at a time.

### `memory_reinforce_many`
Reinforce many entries at once. Cap: 100.
- Args: `entries*` (array of `{key, confidence_boost?}`), `agent_id`.
- **Not needed** — same reasoning.

---

## 4. Session capture — handled by hooks

### `memory_index_session`
Index session chunks for future search.
- Args: `session_id*`, `chunks*`, `agent_id`.
- **Not needed** — `SessionStart` / `SessionEnd` hooks do this automatically.

### `memory_search_sessions`
Search past session summaries.
- Args: `query*`, `limit` (default 10), `agent_id`.
- **When I use it:** when the user references past work ("what were we doing last time on X").

### `memory_capture`
Extract and persist facts from an agent response.
- Args: `response*`, `source` (default `agent`), `agent_scope` (default `private`), `agent_id`.
- **Not needed** — hook-driven.

### `tapps_brain_session_end`
Record an end-of-session episodic entry.
- Args: `summary*`, `tags`, `daily_note` (default False).
- **Not needed** — hook-driven. Only call manually if I know hooks aren't wired in this environment.

---

## 5. Feedback — closing the quality loop

### `feedback_rate`
Rate a recalled entry (`recall_rated` event).
- Args: `entry_key*`, `rating` (default `helpful`), `session_id`, `details_json`.
- **When I use it:** after using a recalled memory. Takes seconds, feeds the flywheel's Bayesian confidence update.

### `feedback_gap`
Report a knowledge gap (`gap_reported` event).
- Args: `query*`, `session_id`, `details_json`.
- **When I use it:** when `brain_recall` returns nothing useful for a topic the brain *should* know. Signals what to fill.

### `feedback_issue`
Flag a quality issue (`issue_flagged` event).
- Args: `entry_key*`, `issue*`, `session_id`, `details_json`.
- **When I use it:** when a recalled memory is wrong, stale, or contradicts the current code.

### `feedback_record`
Record a generic/custom feedback event.
- Args: `event_type*`, `entry_key`, `session_id`, `utility_score`, `details_json`.
- **Not needed** — prefer the three typed tools above. Only useful for custom event types an operator has defined.

### `feedback_query`
Query recorded feedback events.
- Args: `event_type`, `entry_key`, `session_id`, `since`, `until`, `limit` (default 100).
- **Not needed** — operator/debug surface.

---

## 6. Diagnostics (EPIC-030)

### `diagnostics_report`
Composite quality score, per-dimension scores, EWMA anomaly state, circuit breaker state.
- Args: `record_history` (default True).
- **When I use it:** rarely — when recall quality feels off and I want to check if the circuit breaker is CLOSED/HALF_OPEN/OPEN. Recall results also carry a `quality_warning` when not CLOSED, so I usually don't need to poll.

### `diagnostics_history`
Recent persisted diagnostics snapshots.
- Args: `limit` (default 50).
- **Not needed** — operator dashboarding.

---

## 7. Flywheel (EPIC-031)

### `flywheel_process`
Run the feedback → confidence pipeline.
- Args: `since` (timestamp).
- **Not needed** — scheduled operator job.

### `flywheel_gaps`
Top knowledge gaps as JSON.
- Args: `limit` (default 10), `semantic` (default False).
- **When I use it:** when proactively filling holes — "what's the brain missing about this repo?" Useful when I have slack to document.

### `flywheel_report`
Quality report (markdown + structured summary).
- Args: `period_days` (default 7).
- **Not needed** — operator reporting.

---

## 8. Profiles

### `profile_info`
Active profile name, layer stack, scoring config.
- Args: (none).
- **When I use it:** once per session if I want to know tier half-lives or scoring weights before writing memories.

### `memory_profile_onboarding`
Markdown onboarding guide for the active profile.
- Args: (none).
- **When I use it:** once per new repo — gives the profile's "how to work with this brain" guidance.

### `profile_switch`
Switch to a different built-in profile.
- Args: `name*`.
- **Not needed** — operator decision, not per-turn.

---

## 9. Hive — cross-agent / cross-repo shared memory

### `hive_status`
Namespaces, entry counts, registered agents.
- Args: (none).
- **When I use it:** when I suspect Hive is degraded and shared context is missing from recalls.

### `hive_search`
Search the shared Hive.
- Args: `query*`, `namespace`.
- **When I use it:** when the answer probably isn't in *this* repo — e.g., a cross-cutting pattern another agent already solved.

### `hive_propagate`
Manually promote a local memory to the Hive.
- Args: `key*`, `agent_scope` (default `hive`), `force` (default False), `dry_run` (default False).
- **When I use it:** when I saved something locally first and then realized it belongs org-wide. Usually easier to set `share_with="hive"` on `brain_remember` upfront.

### `hive_push`
Batch-promote many local memories to the Hive.
- Args: `agent_scope` (default `hive`), `push_all`, `tags`, `tier`, `keys` (CSV), `dry_run`, `force`.
- **Not needed** — operator bulk promotion.

### `hive_write_revision`
Current Hive write notification revision (for LISTEN/NOTIFY polling).
- Args: (none).
- **Not needed** — internal sync primitive.

### `hive_wait_write`
Block until the Hive write revision advances or timeout.
- Args: `since_revision` (default 0), `timeout_seconds` (default 10).
- **Not needed** — internal sync primitive for long-poll clients.

---

## 10. Agent registry

### `agent_register`
Register an agent in the Hive.
- Args: `agent_id*`, `profile` (default `repo-brain`), `skills`.
- **Not needed** — bootstrap, done once per agent.

### `agent_create`
Create + register an agent with a validated profile.
- Args: `agent_id*`, `profile` (default `repo-brain`), `skills`.
- **Not needed** — bootstrap.

### `agent_list`
List all registered agents in the Hive.
- Args: (none).
- **When I use it:** rarely — when debugging "why isn't my agent seeing X".

### `agent_delete`
Delete a registered agent.
- Args: `agent_id*`.
- **Not needed** — operator cleanup.

---

## 11. Relations graph

### `memory_relations`
All relations for a given key.
- Args: `key*`, `agent_id`.
- **When I use it:** when exploring a decision and I want its direct edges.

### `memory_relations_get_batch`
Relations for many keys in one call.
- Args: `keys_json*` (JSON array string), `agent_id`.
- **Not needed** — interactive turns don't batch.

### `memory_find_related`
BFS traversal across the relation graph.
- Args: `key*`, `max_hops` (default 2), `agent_id`.
- **When I use it:** when I want the context *around* a decision — what supersedes it, what depends on it, what it enables.

### `memory_query_relations`
Filter relations by subject/predicate/object.
- Args: `subject`, `predicate`, `object_entity`, `agent_id`.
- **When I use it:** when I want a specific predicate (e.g., everything that `supersedes X`).

---

## 12. Audit & tags

### `memory_audit`
Query the audit trail for memory events.
- Args: `key`, `event_type`, `since`, `until`, `limit` (default 50), `agent_id`.
- **Not needed** — operator/forensics.

### `memory_list_tags`
All tags in the store with usage counts.
- Args: `agent_id`.
- **When I use it:** before tagging a new memory — avoids inventing a synonym of an existing tag.

### `memory_update_tags`
Atomically add/remove tags on an entry.
- Args: `key*`, `add`, `remove`, `agent_id`.
- **When I use it:** to mark an entry `critical` or `security` for ranking boost after the fact.

### `memory_entries_by_tag`
All entries carrying a specific tag.
- Args: `tag*`, `tier`, `agent_id`.
- **When I use it:** when browsing by tag (e.g. "show me everything tagged `security`").

---

## Minimal working set

If I could only have six tools for everyday coding turns, this is the set:

1. `brain_recall` — read
2. `brain_remember` — write
3. `memory_reinforce` — confirm a hit was useful
4. `feedback_gap` — signal a miss
5. `hive_search` — reach across repos
6. `memory_find_related` — explore context around a decision

Everything else is either covered by these, handled by hooks, or an operator task.

---

## Related docs

- [docs/guides/mcp-client-repo-setup.md](mcp-client-repo-setup.md) — wiring `.mcp.json` + `.env` for a new repo.
- [docs/guides/claude-code-hooks.md](claude-code-hooks.md) — the SessionStart hook that auto-primes `brain_recall` on turn 1.
- [docs/guides/auto-recall.md](auto-recall.md) — how `memory_recall` ranks and fuses results.
- [docs/guides/hive.md](hive.md) — cross-agent / cross-repo shared memory model.
- [scripts/brain-healthcheck.sh](../../scripts/brain-healthcheck.sh) — `make brain-healthcheck` verifies all of the above is live.
