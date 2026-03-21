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
- [ ] Create `src/tapps_brain/profile.py` with Pydantic v2 models: `LayerDefinition`, `PromotionThreshold`, `ScoringConfig`, `GCConfig`, `RecallProfileConfig`, `LimitsConfig`, `MemoryProfile`. Add `extends` field for profile inheritance (max depth 3). Add validation (unique layer names, weights sum to ~1.0, half_life >= 1). Commit: `feat(story-010.1): profile data model`

#### 010-B: Profile loading and resolution
- [ ] In `profile.py`, add `load_profile(path)`, `resolve_profile(project_dir, profile_name)` (project → user-global → built-in → hardcoded default), `get_builtin_profile(name)`, `list_builtin_profiles()`. Add unit tests for loading, validation errors, inheritance merging, resolution order. Commit: `feat(story-010.1): profile loading and resolution`

#### 010-C: Ship 6 built-in profile YAML files
- [ ] Create `src/tapps_brain/profiles/` directory with `repo-brain.yaml`, `personal-assistant.yaml`, `customer-support.yaml`, `research-knowledge.yaml`, `project-management.yaml`, `home-automation.yaml`. Include as package data in `pyproject.toml`. Commit: `feat(story-010.2): built-in profile YAML files`

#### 010-D: Built-in profile tests
- [ ] Add unit tests: each built-in profile loads and validates, weights sum to 1.0, `repo-brain` profile produces identical `DecayConfig` values to current hardcoded defaults. Commit: `test(story-010.2): built-in profile validation tests`

#### 010-E: Wire profile into MemoryStore init
- [ ] `MemoryStore.__init__()` accepts optional `profile: MemoryProfile | None`. When not provided, resolves from project dir → user-global → built-in `repo-brain`. Expose `store.profile` property. Derive `DecayConfig` from profile layer definitions. Commit: `feat(story-010.3): wire profile into MemoryStore`

#### 010-F: Profile-driven tier validation and GC config
- [ ] `store.save()` validates tier against profile layer names (not just `MemoryTier` enum). Unknown tier names fall back to lowest half-life layer. `GCConfig` thresholds read from profile. ALL existing tests must pass unchanged. Commit: `feat(story-010.3): profile-driven tier validation and GC`

#### 010-G: Wire profile into MemoryStore — integration test
- [ ] Add integration test: create store with `personal-assistant` profile, save entries with `identity`/`long-term`/`short-term`/`ephemeral` tiers, verify decay uses profile half-lives. Verify `repo-brain` profile produces identical behavior to no-profile store. Commit: `test(story-010.3): profile integration test`

#### 010-H: Configurable scoring weights
- [ ] `MemoryRetriever.__init__()` accepts optional `ScoringConfig`. When provided, uses its weights instead of module constants. `MemoryStore` passes `profile.scoring` to its retriever. `RecallConfig` defaults from `profile.recall`. Add unit tests: custom weights rank differently, default `ScoringConfig()` identical to current constants. Commit: `feat(story-010.4): configurable scoring weights`

#### 010-I: Promotion engine — core logic
- [ ] Create `src/tapps_brain/promotion.py` with `PromotionEngine`. `check_promotion(entry, profile)` returns target tier if criteria met (min_access_count, min_age_days, min_confidence). `check_demotion(entry, profile)` returns target tier if stale. Desirable difficulty bonus: reinforce boost scales with `(1.0 - decayed_confidence)`. Stability growth: effective half-life grows with `log1p(reinforce_count) * 0.3`. Add unit tests. Commit: `feat(story-010.5): promotion engine`

#### 010-J: Wire promotion into store lifecycle
- [ ] `store.reinforce()` calls `check_promotion()` after updating access count; if promoted, updates tier and logs to audit JSONL. GC `identify_candidates()` calls `check_demotion()` before archival; demoted entries get new tier instead of being archived. Add unit tests. Commit: `feat(story-010.5): wire promotion into store lifecycle`

#### 010-K: Enhanced decay — power-law model
- [ ] `calculate_decayed_confidence()` accepts `decay_model` parameter: `"exponential"` (default) or `"power_law"`. Power-law formula: `C₀ × (1 + t / (k × H))^(-β)`. Default params produce identical behavior to current code. Add unit tests: power-law has longer tail, exponential unchanged. Commit: `feat(story-010.6): power-law decay model`

#### 010-L: Enhanced decay — importance tags
- [ ] Importance tags: `effective_half_life = base_half_life * max(importance_multipliers)`. Layer definition's `importance_tags` dict maps tag names to multiplier floats. Extend `DecayConfig` with `decay_model` and `decay_exponent` fields. Add unit tests. Commit: `feat(story-010.6): importance tags for decay`

#### 010-M: Profile CLI commands and MCP tools
- [ ] CLI: `tapps-brain profile show|list|set|layers`. MCP tools: `profile_info()`, `profile_switch(name)`. Add unit tests. Commit: `feat(story-010.7): profile CLI commands and MCP tools`

#### 010-N: Cross-profile integration tests
- [ ] Integration tests: promotion triggers after 5+ reinforcements, demotion on stale entry, power-law vs exponential at 365 days, importance tags doubling half-life, custom scoring weights ranking, `repo-brain` backward compat. All on real SQLite. Coverage stays at 95%+. Commit: `test(story-010.8): cross-profile integration tests`

## Planned (not yet broken into tasks)

### EPIC-011: Hive — Multi-Agent Shared Brain (High)

**Depends on:** EPIC-010 (STORY-010.3)
**Target:** 2026-06-01
**Stories:** 011.1–011.7 (7 stories, see `docs/planning/epics/EPIC-011.md`)

Adds shared HiveStore at `~/.tapps-brain/hive/`, agent registry, propagation engine (private/domain/hive scopes), conflict resolution, hive-aware recall, and MCP tools.

### EPIC-012: OpenClaw Integration (High)

**Depends on:** EPIC-010 (STORY-010.3), benefits from EPIC-011
**Target:** 2026-06-15
**Stories:** 012.1–012.7 (7 stories, see `docs/planning/epics/EPIC-012.md`)

ContextEngine plugin for OpenClaw, auto-recall/capture hooks, pre-compaction flush, Markdown import, PyPI publish, and ClawHub skill packaging.

## Notes

- **One task per loop.** Each task is sized for ~15 min. If a task is too large, split it and check off the part you finished.
- **EPIC-010** tasks are sequential through 010-G (foundation → profiles → wiring). After 010-G, tasks 010-H through 010-L can be done in any order. 010-M and 010-N come last.
- Always cross-check **`docs/planning/epics/`** before starting a task.
- Maintain **95%** test coverage; run full lint / type / test suite before committing.
- After completing a task, update this file: change `- [ ]` to `- [x]`.
