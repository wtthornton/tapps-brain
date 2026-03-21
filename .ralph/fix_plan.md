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
- [x] EPIC-010: Configurable Memory Profiles (done)
- [x] EPIC-011: Hive — Multi-Agent Shared Brain (done)

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

### EPIC-011: Hive — Multi-Agent Shared Brain (Done ✅)

**Depends on:** EPIC-010 (STORY-010.3) ✅
**Completed:** 2026-03-21
**Design:** `docs/planning/epics/EPIC-011.md`

**Result:** HiveStore, AgentRegistry, PropagationEngine, ConflictPolicy (4 policies), hive-aware recall, 5 MCP tools, 4 CLI commands, schema v7, 71 new tests (62 unit + 9 integration). All backward compatible.

#### 011-A: HiveStore class and SQLite schema
- [x] Create `src/tapps_brain/hive.py` with `HiveStore` class. SQLite at `~/.tapps-brain/hive/hive.db` with WAL mode. Schema: `memories` table with all `MemoryEntry` columns + `namespace TEXT DEFAULT 'universal'` + `source_agent TEXT`. FTS5 index on value + tags. Thread-safe via `threading.Lock`. Commit: `feat(story-011.1): HiveStore class and schema`

#### 011-B: HiveStore CRUD operations
- [x] Implement `HiveStore.save(entry, namespace, source_agent)`, `HiveStore.get(key, namespace)`, `HiveStore.search(query, namespaces, min_confidence)`, `HiveStore.list_namespaces()`. Add unit tests: save/get/search across namespaces, namespace isolation (search ns-A doesn't return ns-B entries). Commit: `feat(story-011.1): HiveStore CRUD operations`

#### 011-C: AgentRegistration model and AgentRegistry
- [x] `AgentRegistration` Pydantic model: `id`, `name`, `profile` (str), `skills` (list[str]), `project_root` (optional str). `AgentRegistry` backed by `~/.tapps-brain/hive/agents.yaml`. Methods: `register(agent)`, `unregister(agent_id)`, `get(agent_id)`, `list_agents()`, `agents_for_domain(domain_name)`. Unit tests. Commit: `feat(story-011.2): agent registry`

#### 011-D: agent_scope field and schema migration
- [x] Add `agent_scope` field to `MemoryEntry` model: `Literal["private", "domain", "hive"]`, default `"private"`. SQLite schema migration: add `agent_scope TEXT DEFAULT 'private'` column to `memories` table. Unit tests for model validation. Commit: `feat(story-011.3): agent_scope field and migration`

#### 011-E: PropagationEngine core logic
- [x] `PropagationEngine` class in `hive.py`. `propagate(entry, agent_id, hive_store)` — saves to Hive if `agent_scope != "private"`. `domain` → namespace = agent profile name; `hive` → namespace = `"universal"`. Auto-propagation config in profile: `hive.auto_propagate_tiers` and `hive.private_tiers`. Unit tests: private stays local, domain goes to profile namespace, hive goes to universal. Commit: `feat(story-011.3): propagation engine`

#### 011-F: Wire propagation into MemoryStore.save()
- [x] `MemoryStore.save()` calls `PropagationEngine.propagate()` when Hive is enabled. `MemoryStore.__init__()` accepts optional `hive_store: HiveStore`. Backward compat: when hive_store is None, no propagation occurs. Unit tests. Commit: `feat(story-011.3): wire propagation into store lifecycle`

#### 011-G: ConflictPolicy enum and resolution logic
- [x] `ConflictPolicy` enum: `last_write_wins`, `source_authority`, `confidence_max`, `supersede`. `HiveStore.save()` checks for existing key before writing and applies policy. `supersede` (default) uses bi-temporal versioning. `source_authority` rejects writes from agents whose profile doesn't match namespace. Audit log records conflict resolutions. Commit: `feat(story-011.4): conflict resolution policies`

#### 011-H: Conflict resolution unit tests
- [x] Unit tests for each policy: two conflicting writes, verify correct winner. Test `supersede` preserves version chain. Test `source_authority` rejects unauthorized writes. Test `confidence_max` keeps higher confidence. Test `last_write_wins` overwrites. Configurable via `hive.conflict_policy` in profile. Commit: `test(story-011.4): conflict resolution tests`

#### 011-I: Hive-aware recall — RecallOrchestrator changes
- [x] `RecallOrchestrator` accepts optional `hive_store: HiveStore`. When enabled, searches: (1) local store, (2) Hive universal namespace, (3) Hive domain namespace matching agent profile. Hive results scored at `hive_recall_weight` (default 0.8, configurable). Results merged and deduplicated by key. `RecallResult` includes `hive_memory_count`. Commit: `feat(story-011.5): hive-aware recall`

#### 011-J: Hive-aware recall — unit tests and store wiring
- [x] `store.recall()` passes Hive store when available. Unit tests: recall finds Hive memory not in local; local outranks Hive for same key; Hive disabled = identical results; `hive_recall_weight=0.5` ranking test. Commit: `test(story-011.5): hive-aware recall tests`

#### 011-K: Hive MCP tools
- [x] MCP tools: `hive_status()` (namespace list, counts, agents), `hive_search(query, namespace)`, `hive_propagate(key, agent_scope)`, `agent_register(agent_id, profile, skills)`, `agent_list()`. All return JSON. Unit tests. Commit: `feat(story-011.6): Hive MCP tools`

#### 011-L: Hive CLI commands
- [x] CLI: `tapps-brain hive status`, `tapps-brain hive search <query>`, `tapps-brain agent register`, `tapps-brain agent list`. Uses existing CLI patterns. Unit tests. Commit: `feat(story-011.6): Hive CLI commands`

#### 011-M: Integration tests — multi-agent round-trip
- [x] Agent A (repo-brain) saves with `agent_scope="hive"` → Agent B (personal-assistant) recalls it. Domain scope isolation: matching profile finds it, non-matching doesn't. Supersede policy preserves version chain. Source_authority rejects unauthorized writes. Auto-propagation for configured tiers. All on real SQLite, cleaned up in fixtures. Commit: `test(story-011.7): multi-agent integration tests`

#### 011-N: Integration tests — backward compat and coverage
- [x] Hive disabled produces identical results to standalone store. `hive_recall_weight` affects ranking. Full lint/type/test pass. Coverage stays at 95%+. Commit: `test(story-011.7): backward compat and coverage validation`

---

### EPIC-012: OpenClaw Integration (High)

**Depends on:** EPIC-010 (STORY-010.3) ✅, benefits from EPIC-011
**Target:** 2026-06-15
**Design:** `docs/planning/epics/EPIC-012.md`

**Goal:** ContextEngine plugin for OpenClaw with auto-recall/capture hooks, pre-compaction flush, Markdown import, PyPI publish, and ClawHub skill packaging.

#### 012-A: Markdown import module — parser core
- [ ] Create `src/tapps_brain/markdown_import.py` with `import_memory_md(path, store) -> int`. Parse markdown headings into keys (slugified), body into values. Tier inference from heading levels: H1/H2 → architectural, H3 → pattern, H4+ → procedural. Deduplication by key. Commit: `feat(story-012.1): markdown import parser`

#### 012-B: Daily note import and workspace importer
- [ ] Add `import_openclaw_workspace(workspace_dir, store) -> dict` to `markdown_import.py`. Parse `memory/YYYY-MM-DD.md` daily notes as context-tier entries with date extraction from filename. Return counts: `memory_md`, `daily_notes`, `skipped`. Commit: `feat(story-012.1): daily note import and workspace importer`

#### 012-C: Markdown import unit tests
- [ ] Unit tests: import sample MEMORY.md with H1-H4 headings → correct tiers. Import twice → no duplicates. Daily note date extraction. Edge cases: empty files, malformed markdown, missing MEMORY.md. Commit: `test(story-012.1): markdown import unit tests`

#### 012-D: OpenClaw plugin directory and manifest
- [ ] Create `openclaw-plugin/` directory: `plugin.json` (ContextEngine slot), `package.json`, `tsconfig.json`, `README.md`. Minimal TypeScript skeleton in `src/index.ts` that exports hook stubs. Commit: `feat(story-012.2): openclaw plugin skeleton`

#### 012-E: Bootstrap hook — spawn MCP and first-run import
- [ ] Implement `bootstrap` hook in `src/index.ts`: spawn `tapps-brain-mcp` as child process, import MEMORY.md on first run via `memory_import` MCP tool, run initial `recall()` for session primer. Read `--project-dir` from OpenClaw workspace path. Commit: `feat(story-012.2): bootstrap hook with MCP spawn`

#### 012-F: Auto-recall via ingest hook
- [ ] Implement `ingest` hook in `src/index.ts`: receive user message, call `memory_recall(message)` via MCP, inject `memory_section` into context as system prefix, respect token budget, track injected keys for dedup within session. Commit: `feat(story-012.3): auto-recall ingest hook`

#### 012-G: Auto-capture via afterTurn hook
- [ ] Implement `afterTurn` hook in `src/index.ts`: receive agent response, call `memory_capture(response)` via MCP. Rate limit: max once every 3 turns (turn counter in plugin state). Log captured keys. Commit: `feat(story-012.4): auto-capture afterTurn hook`

#### 012-H: Pre-compaction flush via compact hook
- [ ] Implement `compact` hook in `src/index.ts`: receive context being compacted, call `memory_ingest(context)` + `memory_index_session(session_id, chunks)` via MCP. Session ID from OpenClaw session identifier. Only process non-persisted context. Commit: `feat(story-012.5): pre-compaction compact hook`

#### 012-I: Markdown import integration tests
- [ ] Integration tests with real SQLite: import mock MEMORY.md with multiple heading levels, verify entries with correct tiers. Idempotency: import twice, no duplicates. Daily notes with real date extraction. File in `tests/integration/test_openclaw_integration.py`. Commit: `test(story-012.7): markdown import integration tests`

#### 012-J: Recall + capture round-trip integration test
- [ ] Integration test: save memory → recall via RecallOrchestrator → capture response with new facts → verify new entries created. Tests the full loop that ContextEngine hooks exercise. Commit: `test(story-012.7): recall capture round-trip integration`

#### 012-K: OpenClaw documentation update
- [ ] Update `docs/guides/openclaw.md` with ContextEngine plugin instructions alongside existing MCP sidecar docs. Cover: install, bootstrap, auto-recall, auto-capture, pre-compaction, profile switching, Hive integration. Commit: `docs(story-012.7): openclaw guide with ContextEngine plugin`

#### 012-L: pyproject.toml metadata for PyPI
- [ ] Add `project.urls` (homepage, repository, documentation, changelog) to `pyproject.toml`. Verify `uv build` produces clean wheel and sdist. Test install from wheel works. Commit: `feat(story-012.6): pyproject.toml metadata for PyPI`

#### 012-M: ClawHub skill directory and SKILL.md
- [ ] Create `openclaw-skill/` with `SKILL.md` (YAML frontmatter: all MCP tools, triggers, capabilities, permissions) and `openclaw.plugin.json` (auto-configures MCP server). Commit: `feat(story-012.6): ClawHub skill directory`

#### 012-N: Version consistency check
- [ ] Add unit test in `tests/unit/test_version_consistency.py` that verifies version string matches across `pyproject.toml`, `openclaw-skill/SKILL.md`, `openclaw-plugin/package.json`, and `openclaw-skill/openclaw.plugin.json`. Commit: `test(story-012.6): version consistency check`

#### 012-O: PyPI publish preparation
- [ ] Create `scripts/publish-checklist.md` documenting manual PyPI publish process. Verify install from wheel works end-to-end: `pip install dist/*.whl && tapps-brain --version && tapps-brain-mcp --help`. Commit: `docs(story-012.6): PyPI publish checklist`

#### 012-P: ClawHub submission preparation
- [ ] Create `openclaw-skill/README.md` for ClawHub listing. Document submission process in `docs/guides/clawhub-submission.md`. Verify skill directory matches ClawHub schema requirements. Commit: `docs(story-012.6): ClawHub submission guide`

#### 012-Q: Final validation and STATUS.md update
- [ ] Run full test suite, verify coverage >= 95%. Run lint and type checks. Update `docs/planning/STATUS.md` to mark EPIC-012 done. Update `__init__.py` exports if new public API surfaces were added. Commit: `chore(epic-012): final validation and status update`

## Notes

- **One task per loop.** Each task is sized for ~15 min. If a task is too large, split it and check off the part you finished.
- **EPIC-011** tasks are sequential through 011-F (foundation → schema → propagation → wiring). After 011-F, tasks 011-G/H and 011-I/J can be done in parallel. 011-K and 011-L are independent. 011-M and 011-N come last.
- **EPIC-012** tasks: 012-A → 012-B → 012-C (markdown import, sequential). 012-D → 012-E (plugin skeleton). 012-F, 012-G, 012-H (hooks, parallel after 012-E). 012-I, 012-J (integration tests). 012-K (docs). 012-L through 012-P (distribution, mostly independent). 012-Q last.
- Always cross-check **`docs/planning/epics/`** before starting a task.
- Maintain **95%** test coverage; run full lint / type / test suite before committing.
- After completing a task, update this file: change `- [ ]` to `- [x]`.
