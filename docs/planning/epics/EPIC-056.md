---
id: EPIC-056
title: "Declarative Group Membership & Expert Publishing"
status: planned
priority: high
created: 2026-04-08
tags: [groups, experts, hive, declarative, propagation]
---

# EPIC-056: Declarative Group Membership & Expert Publishing

## Context

Today Hive groups exist (`create_group()`, `add_group_member()`) but are **imperatively managed** — callers must explicitly create groups and add members via API calls. There is no declarative way to say "this agent belongs to these groups" or "this agent is an expert whose knowledge should be org-wide."

The target architecture requires:
1. **Agents declare group memberships** in their configuration — tapps-brain handles the rest
2. **Expert agents publish to the Hive automatically** — domain knowledge is org-wide without manual propagation calls
3. **Same agent archetype across projects** shares group knowledge but keeps local memory private
4. **An agent can be in multiple groups** (frontend-dev is in both `dev-pipeline` and `frontend-guild`)

**Design principle:** The agent (or its host like AgentForge) declares identity and affiliations. tapps-brain resolves membership, routes saves to the right scopes, and merges recalls transparently. No imperative group management code in callers.

**Depends on:** EPIC-053 (agent identity), EPIC-054 (backend abstraction)
**Enables:** EPIC-057 (unified API that hides all of this)

## Success Criteria

- [ ] `MemoryStore` accepts `groups=["dev-pipeline", "frontend-guild"]` at construction
- [ ] `MemoryStore` accepts `expert_domains=["css", "react"]` at construction
- [ ] Groups are auto-created in Hive if they don't exist
- [ ] Agent is auto-added to declared groups on store initialization
- [ ] Saves with `agent_scope="group:dev-pipeline"` are automatically routed to that group's namespace
- [ ] Expert agent saves with tier `architectural` or `pattern` are auto-published to Hive with `agent_scope="hive"`
- [ ] Recall merges local + group + hive results transparently (weighted)
- [ ] Group membership persists across store restarts (stored in Hive backend, not in-memory)
- [ ] Profile YAML supports `groups` and `expert_domains` fields

## Stories

### STORY-056.1: Declarative group membership on MemoryStore

**Status:** planned
**Effort:** M
**Depends on:** EPIC-053 (agent identity), EPIC-054.5 (backend factory)
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/hive.py`, `src/tapps_brain/profile.py`
**Verification:** `pytest tests/unit/test_declarative_groups.py -v --tb=short -m "not benchmark"`

#### Why

AgentForge's `AGENT.md` will declare `groups: [dev-pipeline, frontend-guild]`. tapps-brain must accept this declaration and handle all the Hive plumbing — group creation, membership registration, and namespace routing — without the caller writing imperative code.

#### Acceptance Criteria

- [ ] `MemoryStore(agent_id="frontend-dev", groups=["dev-pipeline", "frontend-guild"], hive_store=hive)` accepted
- [ ] On construction: for each declared group, call `hive.create_group()` if not exists, then `hive.add_group_member()`
- [ ] Idempotent — re-constructing with same groups does not duplicate membership
- [ ] Groups stored on `AgentRegistration.groups` field in the registry
- [ ] `MemoryStore.groups` property returns the declared list
- [ ] Profile YAML `hive.groups` field supported as alternative to constructor param
- [ ] If `hive_store` is None (no Hive), `groups` param is accepted but no-op (local-only mode)

---

### STORY-056.2: Expert domain declaration and auto-publishing

**Status:** planned
**Effort:** L
**Depends on:** STORY-056.1
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/hive.py` (`PropagationEngine`)
**Verification:** `pytest tests/unit/test_expert_publishing.py -v --tb=short -m "not benchmark"`

#### Why

When a security expert agent saves an `architectural` memory about "always use parameterized queries," that knowledge should automatically appear in the Hive for all agents. Today this requires explicit `hive_propagate()` calls. Expert publishing should be automatic based on the agent's declared domains and the memory's tier.

#### Acceptance Criteria

- [ ] `MemoryStore(agent_id="sql-expert", expert_domains=["sql", "database"], hive_store=hive)` accepted
- [ ] `expert_domains` stored on `AgentRegistration` in registry
- [ ] When an expert agent saves a memory with tier `architectural` or `pattern`:
  - Memory is auto-propagated to Hive with `agent_scope="hive"`
  - Memory is tagged with expert domains (e.g., `tags=["expert:sql", "expert:database"]`)
  - Propagation uses existing `PropagationEngine` — no new code path
- [ ] Tiers `procedural`, `context`, `ephemeral` are NOT auto-published (too noisy)
- [ ] Auto-publish can be disabled per-save via `auto_publish=False` parameter
- [ ] Profile YAML `hive.expert_domains` field supported
- [ ] Non-expert agents (empty `expert_domains`) never auto-publish — they use explicit `agent_scope`

---

### STORY-056.3: Group-scoped save routing

**Status:** planned
**Effort:** M
**Depends on:** STORY-056.1
**Context refs:** `src/tapps_brain/store.py` (`save` method), `src/tapps_brain/agent_scope.py`
**Verification:** `pytest tests/unit/test_group_routing.py -v --tb=short -m "not benchmark"`

#### Why

Today an agent must explicitly specify `agent_scope="group:dev-pipeline"` on every save that should be shared with the group. With declarative membership, tapps-brain can offer a simpler API: `save(scope="group")` shares with all declared groups, or `save(scope="group:dev-pipeline")` targets a specific one.

#### Acceptance Criteria

- [ ] `save(agent_scope="group")` (without group name) propagates to ALL declared groups
- [ ] `save(agent_scope="group:dev-pipeline")` propagates to that specific group only
- [ ] `save(agent_scope="group:unknown")` raises `ValueError` if agent is not a member
- [ ] `save(agent_scope="private")` stays local only (default, unchanged)
- [ ] `save(agent_scope="hive")` propagates to org-wide Hive (unchanged)
- [ ] `save(agent_scope="domain")` propagates to all agents with same profile (unchanged)
- [ ] Group saves include `source_agent` and `memory_group` for provenance
- [ ] Group saves respect conflict policy (per-group or global default)

---

### STORY-056.4: Cross-project group resolution

**Status:** planned
**Effort:** M
**Depends on:** STORY-056.1, EPIC-055.4 (Postgres groups)
**Context refs:** `src/tapps_brain/hive.py` (group methods), `src/tapps_brain/federation.py`
**Verification:** `pytest tests/integration/test_cross_project_groups.py -v --tb=short -m "not benchmark"`

#### Why

`frontend-dev` in project-1 and `frontend-dev` in project-2 both declare `groups: [frontend-guild]`. They are different agent instances with different local memory but share the same group. The Hive must resolve this: both see group memories, both can contribute.

#### Acceptance Criteria

- [ ] Group membership is by `(agent_id, project_root)` — same agent_id in different projects are distinct members
- [ ] `search_with_groups("frontend-guild")` returns memories from all members across projects
- [ ] Agent registry tracks `project_root` per registration — distinguishes same-name agents across projects
- [ ] Group-scoped search supports `project_filter` to scope results to specific projects if desired
- [ ] Memory provenance includes `project_root` so consumers know origin
- [ ] Works with both SQLite backend (same host) and Postgres backend (multi-host)

---

### STORY-056.5: Group-aware and expert-aware recall

**Status:** planned
**Effort:** L
**Depends on:** STORY-056.2, STORY-056.3, STORY-056.4
**Context refs:** `src/tapps_brain/retrieval.py`, `src/tapps_brain/fusion.py`, `src/tapps_brain/recall.py`
**Verification:** `pytest tests/unit/test_group_aware_recall.py -v --tb=short -m "not benchmark"`

#### Why

This is the "hide complexity" payoff for recall. When `frontend-dev` calls `recall("how to handle authentication")`, tapps-brain should transparently:
1. Search **local** agent memory (private work context)
2. Search **group** memories (dev-pipeline workflow knowledge, frontend-guild patterns)
3. Search **Hive expert** memories (security-expert's auth guidance)
4. Fuse results with configurable weights and return a single ranked list

The agent never thinks about scopes — it just asks a question and gets the best answer from all available knowledge.

#### Acceptance Criteria

- [ ] `recall()` automatically searches: local → groups → hive (in that order)
- [ ] Results fused via `ReciprocaRankFusion` (existing `fusion.py`) with scope-based weights:
  - Local weight: configurable, default 0.5 (most relevant — agent's own context)
  - Group weight: configurable, default 0.3 (workflow knowledge)
  - Hive weight: configurable, default 0.2 (expert knowledge, broader but less specific)
- [ ] Weights configurable via profile YAML `hive.recall_weights: {local: 0.5, group: 0.3, hive: 0.2}`
- [ ] Duplicate suppression: if same memory exists in local and group (e.g., via propagation), highest-scoring instance wins
- [ ] Expert memories tagged with `expert:*` get a relevance boost when query matches the domain
- [ ] Recall result includes `source_scope` field (`local`, `group:<name>`, `hive`) for transparency
- [ ] Agent can opt out of group/hive recall via `recall(scope="local")` for isolated queries
- [ ] Performance: group+hive recall adds <50ms to local-only recall (connection reuse, not new connections)

---

### STORY-056.6: Profile YAML schema extension

**Status:** planned
**Effort:** S
**Depends on:** STORY-056.1
**Context refs:** `src/tapps_brain/profile.py`, `src/tapps_brain/profiles/repo-brain.yaml`
**Verification:** `pytest tests/unit/test_profile.py -v --tb=short -m "not benchmark"`

#### Why

The profile system already configures decay, scoring, and hive behavior. Groups and expert domains should be configurable in the same place — either in the built-in profiles or in project-level `profile.yaml` overrides.

#### Acceptance Criteria

- [ ] Profile YAML schema extended with:
  ```yaml
  hive:
    groups: ["dev-pipeline", "frontend-guild"]  # declared group memberships
    expert_domains: ["css", "react"]            # expert publishing domains
    recall_weights:
      local: 0.5
      group: 0.3
      hive: 0.2
    auto_publish_tiers: ["architectural", "pattern"]  # tiers that experts auto-publish
  ```
- [ ] Built-in profiles set sensible defaults (e.g., `repo-brain` has empty groups/domains)
- [ ] Project-level `profile.yaml` can override groups and domains
- [ ] `MemoryStore` constructor params override profile values (runtime > config)
- [ ] Profile validation: `groups` must be list of strings, `expert_domains` must be list of strings
- [ ] `recall_weights` must sum to 1.0 (validated with clear error message)

## Priority Order

| Order | Story | Rationale |
|-------|-------|-----------|
| 1 | STORY-056.1 | Declarative membership is the foundation |
| 2 | STORY-056.6 | Profile schema enables YAML-based config |
| 3 | STORY-056.2 | Expert publishing builds on membership |
| 4 | STORY-056.3 | Group-scoped saves build on membership |
| 5 | STORY-056.4 | Cross-project resolution (needs groups working first) |
| 6 | STORY-056.5 | Unified recall merges all scopes (needs everything above) |
