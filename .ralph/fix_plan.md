# Ralph Fix Plan — tapps-brain

Aligned with the repo as of **2026-03-21**. For full story text, see `docs/planning/epics/EPIC-*.md`.

**Task sizing:** Each item is scoped to ONE Ralph loop (~15 min). Do one, check it off, commit.

## Completed Epics

- [x] EPIC-001: Test suite quality — A+ (done)
- [x] EPIC-002: Integration wiring (done)
- [x] EPIC-003: Auto-recall orchestrator (done)
- [x] EPIC-004: Bi-temporal fact versioning (done)
- [x] EPIC-005: CLI tool (done)
- [x] EPIC-006: Knowledge Graph (done)
- [x] EPIC-007: Observability (done)
- [x] EPIC-008: MCP Server (done)
- [x] EPIC-009: Multi-Interface Distribution (done)

## High Priority

### EPIC-010: Configurable Memory Profiles (Critical)

**Goal:** Make memory tiers, half-lives, scoring weights, and decay models configurable via YAML profile files. Ship 6 built-in profiles for different use cases. Zero behavior change with default `repo-brain` profile.

**Design doc:** `docs/planning/DESIGN-CONFIGURABLE-MEMORY-PROFILES.md`

#### 010-A: Profile data model — Pydantic models
- [x] Create `src/tapps_brain/profile.py` with Pydantic v2 models: `LayerDefinition`, `PromotionThreshold`, `ScoringConfig`, `GCConfig`, `RecallProfileConfig`, `LimitsConfig`, `MemoryProfile`. Add `extends` field for profile inheritance (max depth 3). Add validation (unique layer names, weights sum to ~1.0, half_life >= 1). Commit: `feat(story-010.1): profile data model`

#### 010-B: Profile loading and resolution
- [x] In `profile.py`, add `load_profile(path)`, `resolve_profile(project_dir, profile_name)` (project → user-global → built-in → hardcoded default), `get_builtin_profile(name)`, `list_builtin_profiles()`. Add unit tests for loading, validation errors, inheritance merging, resolution order. Commit: `feat(story-010.1): profile loading and resolution`

#### 010-C: Ship 6 built-in profile YAML files
- [x] Create `src/tapps_brain/profiles/` directory with `repo-brain.yaml`, `personal-assistant.yaml`, `customer-support.yaml`, `research-knowledge.yaml`, `project-management.yaml`, `home-automation.yaml`. Include as package data in `pyproject.toml`. Commit: `feat(story-010.2): built-in profile YAML files`

#### 010-D: Built-in profile tests
- [x] Add unit tests: each built-in profile loads and validates, weights sum to 1.0, `repo-brain` profile produces identical `DecayConfig` values to current hardcoded defaults. Commit: `test(story-010.2): built-in profile validation tests`

#### 010-E: Wire profile into MemoryStore init
- [x] `MemoryStore.__init__()` accepts optional `profile: MemoryProfile | None`. When not provided, resolves from project dir → user-global → built-in `repo-brain`. Expose `store.profile` property. Derive `DecayConfig` from profile layer definitions. Commit: `feat(story-010.3): wire profile into MemoryStore`

#### 010-F: Profile-driven tier validation and GC config
- [x] `store.save()` validates tier against profile layer names (not just `MemoryTier` enum). Unknown tier names fall back to lowest half-life layer. `GCConfig` thresholds read from profile. ALL existing tests must pass unchanged. Commit: `feat(story-010.3): profile-driven tier validation and GC`

#### 010-G: Wire profile into MemoryStore — integration test
- [x] Add integration test: create store with `personal-assistant` profile, save entries with `identity`/`long-term`/`short-term`/`ephemeral` tiers, verify decay uses profile half-lives. Verify `repo-brain` profile produces identical behavior to no-profile store. Commit: `test(story-010.3): profile integration test`

#### 010-H: Configurable scoring weights
- [x] `MemoryRetriever.__init__()` accepts optional `ScoringConfig`. When provided, uses its weights instead of module constants. `MemoryStore` passes `profile.scoring` to its retriever. `RecallConfig` defaults from `profile.recall`. Add unit tests: custom weights rank differently, default `ScoringConfig()` identical to current constants. Commit: `feat(story-010.4): configurable scoring weights`

#### 010-I: Promotion engine — core logic
- [x] Create `src/tapps_brain/promotion.py` with `PromotionEngine`. `check_promotion(entry, profile)` returns target tier if criteria met (min_access_count, min_age_days, min_confidence). `check_demotion(entry, profile)` returns target tier if stale. Desirable difficulty bonus: reinforce boost scales with `(1.0 - decayed_confidence)`. Stability growth: effective half-life grows with `log1p(reinforce_count) * 0.3`. Add unit tests. Commit: `feat(story-010.5): promotion engine`

#### 010-J: Wire promotion into store lifecycle
- [x] `store.reinforce()` calls `check_promotion()` after updating access count; if promoted, updates tier and logs to audit JSONL. GC `identify_candidates()` calls `check_demotion()` before archival; demoted entries get new tier instead of being archived. Add unit tests. Commit: `feat(story-010.5): wire promotion into store lifecycle`

#### 010-K: Enhanced decay — power-law model
- [x] `calculate_decayed_confidence()` accepts `decay_model` parameter: `"exponential"` (default) or `"power_law"`. Power-law formula: `C₀ × (1 + t / (k × H))^(-β)`. Default params produce identical behavior to current code. Add unit tests: power-law has longer tail, exponential unchanged. Commit: `feat(story-010.6): power-law decay model`

#### 010-L: Enhanced decay — importance tags
- [x] Importance tags: `effective_half_life = base_half_life * max(importance_multipliers)`. Layer definition's `importance_tags` dict maps tag names to multiplier floats. Extend `DecayConfig` with `decay_model` and `decay_exponent` fields. Add unit tests. Commit: `feat(story-010.6): importance tags for decay`

#### 010-M: Profile CLI commands and MCP tools
- [x] CLI: `tapps-brain profile show|list|set|layers`. MCP tools: `profile_info()`, `profile_switch(name)`. Add unit tests. Commit: `feat(story-010.7): profile CLI commands and MCP tools`

#### 010-N: Cross-profile integration tests
- [x] Integration tests: promotion triggers after 5+ reinforcements, demotion on stale entry, power-law vs exponential at 365 days, importance tags doubling half-life, custom scoring weights ranking, `repo-brain` backward compat. All on real SQLite. Coverage stays at 95%+. Commit: `test(story-010.8): cross-profile integration tests`

## High Priority

### EPIC-011: Hive — Multi-Agent Shared Brain (High)

**Depends on:** EPIC-010 (STORY-010.3) ✅
**Target:** 2026-06-01
**Design:** `docs/planning/epics/EPIC-011.md`

**Goal:** Cross-agent memory sharing with domain namespaces, propagation engine, conflict resolution, and hive-aware recall. Backward compatible — single-agent behavior unchanged when Hive is disabled.

#### 011-A: HiveStore class and SQLite schema
- [ ] Create `src/tapps_brain/hive.py` with `HiveStore` class. SQLite at `~/.tapps-brain/hive/hive.db` with WAL mode. Schema: `memories` table with all `MemoryEntry` columns + `namespace TEXT DEFAULT 'universal'` + `source_agent TEXT`. FTS5 index on value + tags. Thread-safe via `threading.Lock`. Commit: `feat(story-011.1): HiveStore class and schema`

#### 011-B: HiveStore CRUD operations
- [ ] Implement `HiveStore.save(entry, namespace, source_agent)`, `HiveStore.get(key, namespace)`, `HiveStore.search(query, namespaces, min_confidence)`, `HiveStore.list_namespaces()`. Add unit tests: save/get/search across namespaces, namespace isolation (search ns-A doesn't return ns-B entries). Commit: `feat(story-011.1): HiveStore CRUD operations`

#### 011-C: AgentRegistration model and AgentRegistry
- [ ] `AgentRegistration` Pydantic model: `id`, `name`, `profile` (str), `skills` (list[str]), `project_root` (optional str). `AgentRegistry` backed by `~/.tapps-brain/hive/agents.yaml`. Methods: `register(agent)`, `unregister(agent_id)`, `get(agent_id)`, `list_agents()`, `agents_for_domain(domain_name)`. Unit tests. Commit: `feat(story-011.2): agent registry`

#### 011-D: agent_scope field and schema migration
- [ ] Add `agent_scope` field to `MemoryEntry` model: `Literal["private", "domain", "hive"]`, default `"private"`. SQLite schema migration: add `agent_scope TEXT DEFAULT 'private'` column to `memories` table. Unit tests for model validation. Commit: `feat(story-011.3): agent_scope field and migration`

#### 011-E: PropagationEngine core logic
- [ ] `PropagationEngine` class in `hive.py`. `propagate(entry, agent_id, hive_store)` — saves to Hive if `agent_scope != "private"`. `domain` → namespace = agent profile name; `hive` → namespace = `"universal"`. Auto-propagation config in profile: `hive.auto_propagate_tiers` and `hive.private_tiers`. Unit tests: private stays local, domain goes to profile namespace, hive goes to universal. Commit: `feat(story-011.3): propagation engine`

#### 011-F: Wire propagation into MemoryStore.save()
- [ ] `MemoryStore.save()` calls `PropagationEngine.propagate()` when Hive is enabled. `MemoryStore.__init__()` accepts optional `hive_store: HiveStore`. Backward compat: when hive_store is None, no propagation occurs. Unit tests. Commit: `feat(story-011.3): wire propagation into store lifecycle`

#### 011-G: ConflictPolicy enum and resolution logic
- [ ] `ConflictPolicy` enum: `last_write_wins`, `source_authority`, `confidence_max`, `supersede`. `HiveStore.save()` checks for existing key before writing and applies policy. `supersede` (default) uses bi-temporal versioning. `source_authority` rejects writes from agents whose profile doesn't match namespace. Audit log records conflict resolutions. Commit: `feat(story-011.4): conflict resolution policies`

#### 011-H: Conflict resolution unit tests
- [ ] Unit tests for each policy: two conflicting writes, verify correct winner. Test `supersede` preserves version chain. Test `source_authority` rejects unauthorized writes. Test `confidence_max` keeps higher confidence. Test `last_write_wins` overwrites. Configurable via `hive.conflict_policy` in profile. Commit: `test(story-011.4): conflict resolution tests`

#### 011-I: Hive-aware recall — RecallOrchestrator changes
- [ ] `RecallOrchestrator` accepts optional `hive_store: HiveStore`. When enabled, searches: (1) local store, (2) Hive universal namespace, (3) Hive domain namespace matching agent profile. Hive results scored at `hive_recall_weight` (default 0.8, configurable). Results merged and deduplicated by key. `RecallResult` includes `hive_memory_count`. Commit: `feat(story-011.5): hive-aware recall`

#### 011-J: Hive-aware recall — unit tests and store wiring
- [ ] `store.recall()` passes Hive store when available. Unit tests: recall finds Hive memory not in local; local outranks Hive for same key; Hive disabled = identical results; `hive_recall_weight=0.5` ranking test. Commit: `test(story-011.5): hive-aware recall tests`

#### 011-K: Hive MCP tools
- [ ] MCP tools: `hive_status()` (namespace list, counts, agents), `hive_search(query, namespace)`, `hive_propagate(key, agent_scope)`, `agent_register(agent_id, profile, skills)`, `agent_list()`. All return JSON. Unit tests. Commit: `feat(story-011.6): Hive MCP tools`

#### 011-L: Hive CLI commands
- [ ] CLI: `tapps-brain hive status`, `tapps-brain hive search <query>`, `tapps-brain agent register`, `tapps-brain agent list`. Uses existing CLI patterns. Unit tests. Commit: `feat(story-011.6): Hive CLI commands`

#### 011-M: Integration tests — multi-agent round-trip
- [ ] Agent A (repo-brain) saves with `agent_scope="hive"` → Agent B (personal-assistant) recalls it. Domain scope isolation: matching profile finds it, non-matching doesn't. Supersede policy preserves version chain. Source_authority rejects unauthorized writes. Auto-propagation for configured tiers. All on real SQLite, cleaned up in fixtures. Commit: `test(story-011.7): multi-agent integration tests`

#### 011-N: Integration tests — backward compat and coverage
- [ ] Hive disabled produces identical results to standalone store. `hive_recall_weight` affects ranking. Full lint/type/test pass. Coverage stays at 95%+. Commit: `test(story-011.7): backward compat and coverage validation`

---

### EPIC-012: OpenClaw Integration (High) — not yet broken into tasks

**Depends on:** EPIC-010 (STORY-010.3) ✅, benefits from EPIC-011
**Target:** 2026-06-15
**Stories:** 012.1–012.7 (7 stories, see `docs/planning/epics/EPIC-012.md`)

ContextEngine plugin for OpenClaw, auto-recall/capture hooks, pre-compaction flush, Markdown import, PyPI publish, and ClawHub skill packaging. Will be broken into Ralph-sized tasks after EPIC-011 is underway.

## Notes

- **One task per loop.** Each task is sized for ~15 min. If a task is too large, split it and check off the part you finished.
- **EPIC-011** tasks are sequential through 011-F (foundation → schema → propagation → wiring). After 011-F, tasks 011-G/H and 011-I/J can be done in parallel. 011-K and 011-L are independent. 011-M and 011-N come last.
- Always cross-check **`docs/planning/epics/`** before starting a task.
- Maintain **95%** test coverage; run full lint / type / test suite before committing.
- After completing a task, update this file: change `- [ ]` to `- [x]`.
