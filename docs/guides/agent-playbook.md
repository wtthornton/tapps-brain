# Agent Playbook — Getting Full Value from tapps-brain

**Audience:** AI coding agents (Claude Code, Cursor, Aider, Codex, custom in-process agents) using tapps-brain via MCP, HTTP, or the in-process `AgentBrain` facade.

**Purpose:** one page that answers *what to call, when, with what arguments, and how to recover when it fails*. The companion docs go deeper on each topic; this one consolidates the decision tree.

---

## 1. Pick your entry point

tapps-brain ships **three first-class interfaces** to the same memory engine. Pick once; the rest of this playbook applies regardless.

| Entry point | When to use | Notes |
|---|---|---|
| **MCP tools** (`brain_recall`, `brain_remember`, …) | Default for any agent driven by an MCP client (Claude Code, Cursor, OpenAI Agents, etc.) | All examples below default to this. Wired via `.mcp.json`. See [`mcp-client-repo-setup.md`](mcp-client-repo-setup.md). |
| **`TappsBrainClient`** (Python, sync/async) | Out-of-process agents that aren't MCP-native | Auto-attaches `X-Idempotency-Key` on write tools when `TAPPS_BRAIN_IDEMPOTENCY=1`. See [`client.py`](../../src/tapps_brain/client.py) and [`client.md`](client.md). |
| **`AgentBrain` facade** (in-process) | Python agents embedded with tapps-brain installed | Only place where `set_task_context()` correlates `recall` ↔ `learn_from_success`. See [`agent-integration.md`](agent-integration.md). |

**Consequences of the choice.** The MCP wrapper for `brain_recall` and `brain_remember` is intentionally narrow — when you need structured pre-filters (`filter_tier`, `filter_tags`, `include_stale`) reach for `memory_search` / `memory_entries_by_tag`, which are the full surface. The facade exposes the widest knob set in-process.

---

## 2. The session loop (what to call when)

```
┌────────────────────────────────────────────────────────────────┐
│  TURN 1 (session start)                                         │
│   1. brain_recall(query=<user's opening topic>)                 │
│      └─ primes the context window with relevant past decisions  │
│                                                                  │
│  EVERY NON-TRIVIAL DECISION                                     │
│   2. brain_recall(query=<the decision>)                         │
│      └─ check before deviating from an existing pattern         │
│                                                                  │
│  AFTER USING A RECALLED MEMORY                                  │
│   3. memory_reinforce(key=<recalled_key>)                       │
│      └─ banks a positive signal; cheap                          │
│                                                                  │
│  EMPTY/UNHELPFUL RECALL ON A TOPIC THE BRAIN SHOULD KNOW        │
│   4. feedback_gap(query=<your query>)                           │
│      └─ signals what to fill                                    │
│                                                                  │
│  RECALLED MEMORY IS WRONG / STALE / CONTRADICTS THE CODE        │
│   5. feedback_issue(entry_key=<key>, issue="…")                 │
│   6. brain_forget(key=<key>)  ← only when fully contradicted   │
│                                                                  │
│  WHEN A DECISION CARRIES NON-OBVIOUS RATIONALE                  │
│   7. brain_remember(fact=…, tier=…, [share_with=…])             │
│                                                                  │
│  AFTER SHIPPING A WIN                                           │
│   8. brain_learn_success(description=…, task_id=…)              │
│                                                                  │
│  AFTER A BLIND ALLEY                                            │
│   9. brain_learn_failure(description=…, error=…)                │
└────────────────────────────────────────────────────────────────┘
```

`SessionStart` hooks (see [`claude-code-hooks.md`](claude-code-hooks.md)) automate step 1 for you in Claude Code.

---

## 3. What to remember (and what NOT to)

### Save when

- A decision carries **rationale** that isn't obvious from the code (the *why*).
- The user **corrects** your approach or teaches a non-obvious rule.
- A debug session reveals a **subtle invariant** or surprising constraint.

### Do NOT save

- **Code patterns / file paths / module layout** — derivable by reading the repo.
- **Git history, recent diffs, who-changed-what** — `git log` / `git blame` are authoritative.
- **Ephemeral task state, current-conversation context, debug fix recipes** — these belong in `TodoWrite` or the commit message.
- **Anything containing secrets, tokens, or PII.**
- **Code itself** — the brain stores knowledge, not source.

### Tier selection (default `repo-brain` profile)

| Tier | Half-life | Use for |
|---|---|---|
| `architectural` | 180 days | Tech stack, framework choices, API contracts, ADR-level decisions |
| `pattern` | 60 days | Naming conventions, code style, file organisation, reusable patterns |
| `procedural` | 30 days | How-to knowledge, build steps, deploy procedures, runbooks |
| `context` | 14 days | Current task state, recent session-scoped decisions |

If unsure, default to `pattern`. Pick by **how long you'd want the fact to survive**, not by topic. Custom profiles may add `ephemeral`/`personal`/etc. — `memory_profile_onboarding` returns the active profile's layer stack.

### Tagging conventions

- `critical` / `security` — boost recall ranking. Use sparingly.
- `tapps_lookup_docs(library)` for docs facts; tag with the library name.
- `memory_list_tags` before inventing a new tag — avoids synonym sprawl.

---

## 4. Sharing across agents (Hive scope)

`brain_remember` has two ways to publish beyond your private store:

| Goal | Call shape |
|---|---|
| Keep private | `brain_remember(fact=…)` (default `agent_scope="private"`) |
| Share with my groups (CSV from `TAPPS_BRAIN_GROUPS`) | `brain_remember(fact=…, share=True)` |
| Share org-wide | `brain_remember(fact=…, share_with="hive")` |
| Share with one named group | `brain_remember(fact=…, share_with="<group>")` |
| Explicit Hive namespace | `brain_remember(fact=…, agent_scope="private"\|"domain"\|"hive"\|"group:<name>")` — wins over `share`/`share_with` (TAP-989) |

`memory_group` is a **project-local partition** orthogonal to the Hive scope axis — only set it when you need group-filtered retrieval *within one project*. See [`memory-scopes.md`](memory-scopes.md) for the conceptual split.

---

## 5. Knowing what to expect (response shapes & failure modes)

### `brain_recall`

- **Returns:** `list[dict]` — each dict has `key`, `value`, `tier`, `score`, `agent_scope`, `tags`, `confidence`, `failed_approaches` (when non-empty).
- **Empty list ≠ store empty.** The MCP wrapper hides the diagnostics envelope. If a recall feels suspiciously empty, call `memory_recall(query=<same query>)` once — it returns a `RecallResult` with `empty_reason` (one of: `store_empty`, `below_threshold`, `scope_filtered`, `rag_blocked`, `circuit_breaker_open`, …). See [`recall_diagnostics.py`](../../src/tapps_brain/recall_diagnostics.py).
- **Quality warning.** When the diagnostics circuit breaker is not `CLOSED`, the result also carries `quality_warning`. Treat recalls as advisory in that state; consider re-running with fewer filters.

### `brain_remember`

- **Returns:** `{saved: true, key: "<content-key>"}`.
- **Supersession candidate.** If a similar active entry exists, the response carries `supersession_candidate: "<old-key>"`. Confirm with a second call: `brain_remember(fact=…, supersedes=<old-key>)` (service layer) or `memory_supersede(old_key=…, new_value=…)`.
- **Idempotency.** When `TAPPS_BRAIN_IDEMPOTENCY=1`, pass `_meta.idempotency_key=<uuid>` to make duplicate writes safe. `TappsBrainClient` auto-attaches a UUID for every write tool. Replays return the original status and body.

### Error taxonomy (HTTP / MCP)

| Error code | When you see it | What to do |
|---|---|---|
| `brain_degraded` (503) | Quality circuit breaker is OPEN | Backoff, retry — recalls work; degraded ranking |
| `brain_rate_limited` (429) | Sliding-window cap exceeded | Honour `retry_after`; reduce write burst |
| `project_not_registered` (403) | `X-Project-Id` not in registry (strict mode) | Call `tapps-brain project register` or ask operator |
| `invalid_request` (400) | Bad args (e.g. unknown `agent_scope` value) | Inspect `data.valid_values`; fix and retry |
| `idempotency_conflict` (409) | Same key, different body | Generate a fresh UUID for the new write |
| `not_found` (404) | `key` doesn't exist | Recall first to discover keys |
| `internal_error` (500) | Server bug | Retry once; report if persistent |

Full table: [`errors.md`](errors.md).

---

## 6. Profiles — see the brain you're talking to

`profile_info` is a one-shot self-introspection. Call once per new repo to see:

- Layer stack (the tiers + their half-lives)
- Scoring weights (relevance / confidence / recency / frequency)
- GC thresholds and consolidation rules

`memory_profile_onboarding` returns a markdown summary tailored to the active profile — read it once and you know how *this* brain wants to be used.

---

## 7. Knowledge-Graph tools (EPIC-076, deployed brains only)

When the deployed brain has the KG enabled, these tools become available (several are deferred-loaded on the `full` profile — still callable via `tools/call`):

- `brain_record_event` — atomic write of an `ExperienceEvent` + optional memory/entity/edge/evidence in one transaction.
- `brain_query_events` — **v3.24.0+** read back stored event payloads (`quality_metric`, etc.) by `event_type` and optional `entity_id` (file path). REST: `POST /v1/experience:query`.
- `brain_record_events_batch` — N events in one MCP round-trip.
- `brain_resolve_entity` — resolve a named entity to a stable UUID before writing edges.
- `brain_get_neighbors` — 1-hop or 2-hop neighbourhood around an entity (structure only — not event payloads).
- `brain_explain_connection` — shortest path (≤3 hops) between two entities.
- `brain_record_feedback` — `edge_helpful` / `edge_misleading` signal for KG edges.

Use KG tools when reasoning about **relationships between things** (e.g. "what other workflows touched this database?"). Use `brain_query_events` when you need **metrics or audit payloads** written by `brain_record_event`. For plain "did we decide X" lookups, stay with `brain_recall`. Full reference: [`knowledge-graph.md`](knowledge-graph.md), [`kg-experience-flow.md`](kg-experience-flow.md).

---

## 8. Anti-patterns to avoid

- **Calling `brain_recall` with no query on every turn.** It's not free; recall once per non-trivial decision, not per token.
- **Saving every comment as a memory.** Save knowledge, not noise. Three corroborating recalls > one verbose dump.
- **Using `memory_delete` instead of `brain_forget`.** Forget is archive (reversible); delete is permanent. Default to forget.
- **Hand-rolling an `X-Idempotency-Key` per request.** Let `TappsBrainClient` (or your MCP transport) attach one. Reusing a key across different writes triggers `idempotency_conflict` (409).
- **Reading `TAPPS_BRAIN_OPERATOR_TOOLS=1` and expecting effect on the standard MCP server.** It's a no-op there by design (STORY-070.9) — operator tools only exist on `tapps-brain-operator-mcp`.
- **Treating Hive `namespace` as project `memory_group`.** They are unrelated; see [`memory-scopes.md`](memory-scopes.md).

---

## 9. Related docs (read in this order)

1. [`mcp-client-repo-setup.md`](mcp-client-repo-setup.md) — wire `.mcp.json` for your repo.
2. [`mcp-tools-for-agents.md`](mcp-tools-for-agents.md) — every tool, with "when I'd reach for it" notes.
3. [`llm-brain-guide.md`](llm-brain-guide.md) — short tier reference and write examples.
4. [`kg-experience-flow.md`](kg-experience-flow.md) — record events, query metrics, explore KG neighbours.
5. [`agent-integration.md`](agent-integration.md) — in-process `AgentBrain` facade detail.
6. [`memory-scopes.md`](memory-scopes.md) — project group vs Hive vs profile (don't confuse them).
7. [`profile-catalog.md`](profile-catalog.md) — built-in profiles and their layer stacks.
8. [`errors.md`](errors.md) — full error taxonomy + retry semantics.
9. [`claude-code-hooks.md`](claude-code-hooks.md) — the SessionStart hook that auto-primes recall on turn 1.
10. [`hive.md`](hive.md), [`hive-vs-federation.md`](hive-vs-federation.md) — cross-agent and cross-project sharing.
