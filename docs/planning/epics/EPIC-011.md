---
id: EPIC-011
title: "Hive — multi-agent shared brain with domain namespaces"
status: done
priority: high
created: 2026-03-21
completed: 2026-03-21
target_date: 2026-06-01
tags: [hive, multi-agent, shared-memory, domains, propagation]
---

# EPIC-011: Hive — Multi-Agent Shared Brain with Domain Namespaces

## Context

tapps-brain currently serves one agent per project. But AI agent setups increasingly involve multiple specialized agents — a dev agent, a personal assistant, a home automation agent — all serving the same user. These agents need to share common knowledge (user identity, preferences) while keeping domain-specific knowledge isolated (code patterns vs. IoT device state).

The existing federation system (`federation.py`) shares memories across *projects* via explicit publish/subscribe. The Hive extends this to share memories across *agents* on the same machine, with automatic propagation based on agent scope.

Key concepts from the design doc (`DESIGN-CONFIGURABLE-MEMORY-PROFILES.md`, Section 8):
- **Three-level hierarchy:** Hive (universal) → Domain (per-skill) → Agent-private
- **`agent_scope` field:** `private` | `domain` | `hive`
- **Domain namespaces:** Agents write to their own domain, can read from any
- **Conflict resolution:** supersede (default), source-authority, confidence-max, last-write-wins
- **Hive-aware recall:** Merges local + Hive results with configurable weight

This epic depends on EPIC-010 (profiles) for per-agent profile support.

## Success Criteria

- [x] Hive store exists at `~/.tapps-brain/hive/hive.db` with namespace and agent columns
- [x] Agents register with ID, profile, and skills
- [x] Memories propagate to the Hive based on `agent_scope`
- [x] Domain namespaces isolate skill-specific knowledge
- [x] Recall merges local + Hive results with configurable weight
- [x] Conflict resolution handles concurrent writes from multiple agents
- [x] Existing single-agent behavior is unchanged when Hive is disabled
- [x] Coverage stays at 95%+

## Stories

### STORY-011.1: Hive store and schema

**Status:** done
**Effort:** L
**Depends on:** EPIC-010 (STORY-010.3)
**Context refs:** `src/tapps_brain/federation.py`, `src/tapps_brain/persistence.py`
**Verification:** `pytest tests/unit/test_hive.py -v --cov=tapps_brain.hive --cov-report=term-missing`

#### Why

The Hive needs its own SQLite store with namespace-aware schema. This is the foundation that all other Hive stories build on. It follows the same patterns as `FederatedStore` but adds namespace and agent columns.

#### Acceptance Criteria

- [x] New `src/tapps_brain/hive.py` module with `HiveStore` class
- [x] SQLite database at `~/.tapps-brain/hive/hive.db` with WAL mode
- [x] Schema: `memories` table with all `MemoryEntry` columns + `namespace TEXT DEFAULT 'universal'` + `source_agent TEXT`
- [x] FTS5 index covering value + tags (for Hive-wide search)
- [x] `HiveStore.save(entry, namespace, source_agent)` — writes to Hive DB
- [x] `HiveStore.search(query, namespaces, min_confidence)` — searches across specified namespaces
- [x] `HiveStore.get(key, namespace)` — retrieves by key within a namespace
- [x] `HiveStore.list_namespaces()` — returns active namespace names
- [x] Thread-safe via `threading.Lock` (same pattern as `MemoryStore`)
- [x] Unit tests: save/search/get across namespaces, namespace isolation (search in ns-A doesn't return ns-B entries)

---

### STORY-011.2: Agent registry

**Status:** done
**Effort:** S
**Depends on:** STORY-011.1
**Context refs:** `src/tapps_brain/hive.py`
**Verification:** `pytest tests/unit/test_hive.py::TestAgentRegistry -v`

#### Why

The Hive needs to know which agents exist, what profiles they use, and what skills they have. This determines which domain namespaces each agent can write to and which it subscribes to.

#### Acceptance Criteria

- [x] `AgentRegistration` Pydantic model: `id`, `name`, `profile` (str), `skills` (list[str]), `project_root` (optional str)
- [x] `AgentRegistry` class backed by `~/.tapps-brain/hive/agents.yaml`
- [x] `registry.register(agent)` — adds/updates an agent
- [x] `registry.unregister(agent_id)` — removes an agent
- [x] `registry.get(agent_id) -> AgentRegistration | None`
- [x] `registry.list_agents() -> list[AgentRegistration]`
- [x] `registry.agents_for_domain(domain_name) -> list[AgentRegistration]` — returns agents whose profile matches the domain
- [x] Unit tests: register, unregister, list, domain lookup

---

### STORY-011.3: Agent scope field and propagation engine

**Status:** done
**Effort:** M
**Depends on:** STORY-011.1, STORY-011.2
**Context refs:** `src/tapps_brain/models.py`, `src/tapps_brain/store.py`
**Verification:** `pytest tests/unit/test_hive.py::TestPropagation -v`

#### Why

This is the core mechanism: when an agent saves a memory, the propagation engine decides whether it stays private or flows up to the Hive. Without this, the Hive is just an empty database.

#### Acceptance Criteria

- [x] `agent_scope` field added to `MemoryEntry` model: `Literal["private", "domain", "hive"]`, default `"private"`
- [x] SQLite schema migration: add `agent_scope TEXT DEFAULT 'private'` column to `memories` table
- [x] `PropagationEngine` class in `src/tapps_brain/hive.py`
- [x] `PropagationEngine.propagate(entry, agent_id, hive_store)` — saves to Hive if `agent_scope != "private"`
- [x] `agent_scope="domain"` → saved to `namespace=agent.profile` in Hive
- [x] `agent_scope="hive"` → saved to `namespace="universal"` in Hive
- [x] `MemoryStore.save()` calls propagation engine when Hive is enabled
- [x] Auto-propagation config in profile: `hive.auto_propagate_tiers` lists tiers that auto-propagate; `hive.private_tiers` lists tiers that never propagate
- [x] Unit tests: private stays local, domain goes to profile namespace, hive goes to universal
- [x] Backward compat: when Hive is disabled (default), no propagation occurs; existing behavior unchanged

---

### STORY-011.4: Conflict resolution

**Status:** done
**Effort:** M
**Depends on:** STORY-011.3
**Context refs:** `src/tapps_brain/hive.py`, `src/tapps_brain/store.py`
**Verification:** `pytest tests/unit/test_hive.py::TestConflictResolution -v`

#### Why

When multiple agents write to the Hive, conflicts are inevitable. Agent A says "we use PostgreSQL" and Agent B says "we use MySQL" — the Hive needs a principled way to resolve this. The default (supersede) preserves history via bi-temporal versioning.

#### Acceptance Criteria

- [x] `ConflictPolicy` enum: `last_write_wins`, `source_authority`, `confidence_max`, `supersede`
- [x] `HiveStore.save()` checks for existing key in the target namespace before writing
- [x] `last_write_wins`: overwrites if new entry is more recent
- [x] `source_authority`: accepts write only if source agent's profile matches the namespace domain; otherwise rejects with logged warning
- [x] `confidence_max`: keeps the version with higher confidence
- [x] `supersede` (default): uses bi-temporal versioning — marks old version with `invalid_at`, creates new version with `superseded_by` link
- [x] Conflict policy configurable in profile: `hive.conflict_policy`
- [x] Audit log records conflict resolutions with both versions
- [x] Unit tests: each policy with two conflicting writes, verify correct winner

---

### STORY-011.5: Hive-aware recall

**Status:** done
**Effort:** L
**Depends on:** STORY-011.3
**Context refs:** `src/tapps_brain/recall.py`, `src/tapps_brain/retrieval.py`
**Verification:** `pytest tests/unit/test_hive.py::TestHiveRecall tests/unit/test_recall.py -v`

#### Why

This is the user-facing payoff. When an agent runs `recall()`, it should seamlessly return relevant memories from both its local store and the Hive. A dev agent asking "what database do we use?" should find the answer whether it was stored locally or by another agent.

#### Acceptance Criteria

- [x] `RecallOrchestrator` accepts optional `hive_store: HiveStore` parameter
- [x] When Hive is enabled, recall searches: (1) local store, (2) Hive universal namespace, (3) Hive domain namespace matching agent profile
- [x] Hive results scored at `hive_recall_weight` multiplier (default 0.8, configurable in profile)
- [x] Results merged and deduplicated by key (highest score wins)
- [x] Token budget applies to merged results (not per-source)
- [x] `RecallResult` includes `hive_memory_count` field for observability
- [x] `store.recall()` convenience method passes Hive store when available
- [x] When Hive is disabled, recall behavior is identical to current code
- [x] Unit test: recall finds memory from Hive that doesn't exist locally
- [x] Unit test: local memory with same key outranks Hive memory (higher score)
- [x] Unit test: Hive disabled produces identical results to current behavior

---

### STORY-011.6: Hive MCP tools

**Status:** done
**Effort:** S
**Depends on:** STORY-011.5
**Context refs:** `src/tapps_brain/mcp_server.py`
**Verification:** `pytest tests/unit/test_mcp_server.py::TestHiveTools -v`

#### Why

MCP clients need to interact with the Hive directly — checking status, searching the shared brain, managing agent registration. These tools make the Hive visible and manageable from any MCP-connected agent.

#### Acceptance Criteria

- [x] MCP tool: `hive_status()` — returns Hive DB stats (namespace list, entry counts per namespace, registered agents)
- [x] MCP tool: `hive_search(query, namespace)` — searches Hive with optional namespace filter
- [x] MCP tool: `hive_propagate(key, agent_scope)` — manually propagates an existing local memory to the Hive
- [x] MCP tool: `agent_register(agent_id, profile, skills)` — registers current agent
- [x] MCP tool: `agent_list()` — lists registered agents
- [x] All tools return JSON; errors return `{"error": "...", "message": "..."}`
- [x] Unit tests for each tool

---

### STORY-011.7: Integration tests — multi-agent round-trip

**Status:** done
**Effort:** L
**Depends on:** STORY-011.4, STORY-011.5
**Context refs:** `tests/integration/`
**Verification:** `pytest tests/integration/test_hive_integration.py -v --cov=tapps_brain.hive --cov-report=term-missing`

#### Why

The Hive involves multiple stores, propagation, conflict resolution, and cross-store recall. Integration tests validate the full multi-agent workflow with real SQLite databases.

#### Acceptance Criteria

- [x] Integration test: Agent A (repo-brain) saves "We use PostgreSQL" with `agent_scope="hive"` → Agent B (personal-assistant) recalls "database" → finds it
- [x] Integration test: Agent A saves to `domain` scope → only agents with matching profile find it; other agents don't
- [x] Integration test: Agent A and Agent B both write conflicting values for same key → `supersede` policy preserves both with version chain
- [x] Integration test: `source_authority` policy — dev agent's write to `repo-brain` domain accepted; calendar agent's write rejected
- [x] Integration test: recall with Hive disabled produces identical results to standalone store
- [x] Integration test: auto-propagation — save `architectural` tier entry (in `auto_propagate_tiers`), verify it appears in Hive; save `context` tier entry (in `private_tiers`), verify it stays local
- [x] Integration test: `hive_recall_weight=0.5` — local results rank higher than identical Hive results
- [x] All tests use real SQLite databases (no mocks), cleaned up in fixtures
- [x] Overall coverage stays at 95%+

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | 011.1 — Hive store | L | Foundation: database, schema, basic CRUD |
| 2 | 011.2 — Agent registry | S | Quick follow-up: who's in the Hive |
| 3 | 011.3 — Propagation engine | M | Core mechanism: how data flows to the Hive |
| 4 | 011.4 — Conflict resolution | M | Safety: handle concurrent writes correctly |
| 5 | 011.5 — Hive-aware recall | L | User-facing payoff: seamless cross-agent search |
| 6 | 011.6 — Hive MCP tools | S | Interface: make Hive visible to MCP clients |
| 7 | 011.7 — Integration tests | L | Validation: full multi-agent round-trip |

## Dependency Graph

```
EPIC-010.3 (profiles wired)
    │
    └──→ 011.1 (hive store) → 011.2 (registry) → 011.3 (propagation) ──┬──→ 011.4 (conflicts) ──┐
                                                                         │                        ├──→ 011.7 (integration)
                                                                         └──→ 011.5 (recall) ─────┘
                                                                                    │
                                                                                    └──→ 011.6 (MCP tools)
```

## Testability Checkpoints

| After Story | What You Can Test |
|-------------|-------------------|
| 011.1 | Hive DB exists, save/search/get work across namespaces |
| 011.2 | Register agents, list them, look up by domain |
| 011.3 | Save a memory with agent_scope="hive", verify it appears in Hive DB |
| 011.5 | Ask Agent B to recall something Agent A stored — it finds it |
| 011.7 | Full multi-agent workflow: register, save, propagate, conflict, recall |
