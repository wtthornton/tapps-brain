---
id: EPIC-010
title: "Configurable memory profiles — pluggable layers and scoring"
status: done
priority: critical
created: 2026-03-21
target_date: 2026-05-01
completed: 2026-03-21
tags: [profiles, layers, decay, scoring, configuration]
---

# EPIC-010: Configurable Memory Profiles — Pluggable Layers and Scoring

## Context

tapps-brain's memory tiers (architectural/pattern/procedural/context), half-lives (180/60/30/14 days), and scoring weights (40/30/15/15) are all hardcoded. This limits the system to code-repo use cases. To serve as a universal brain for any AI agent — personal assistants, customer support, home automation, research, project management — the layer definitions, decay parameters, and scoring weights must be configurable via profile files.

The design is detailed in `docs/planning/DESIGN-CONFIGURABLE-MEMORY-PROFILES.md`. This epic implements Phases 1–6 of that design.

Key constraints:
- **Zero behavior change** with default `repo-brain` profile — all existing tests must pass unchanged
- `MemoryTier` enum is kept as a convenience alias
- SQLite schema needs no migration (`tier` is already `TEXT`)
- Profile files are YAML, loaded at `MemoryStore` init time

## Success Criteria

- [x] Profiles are defined in YAML and loaded at store init
- [x] 6 built-in profiles ship as package data: `repo-brain`, `personal-assistant`, `customer-support`, `research-knowledge`, `project-management`, `home-automation`
- [x] Custom layers with custom names, half-lives, and decay models work end-to-end
- [x] Scoring weights are configurable per profile
- [x] Promotion/demotion engine moves memories between layers based on access patterns
- [x] Power-law decay model available alongside exponential
- [x] Importance tags boost effective half-life
- [x] Profile CLI commands and MCP tools for introspection
- [x] All existing tests pass with default profile (backward compatible)
- [x] Coverage stays at 95%+

## Stories

### STORY-010.1: Profile data model and YAML loading

**Status:** done
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/models.py`, `src/tapps_brain/decay.py`, `src/tapps_brain/store.py`
**Verification:** `pytest tests/unit/test_profile.py -v --cov=tapps_brain.profile --cov-report=term-missing`

#### Why

The profile model is the foundation everything else builds on. Without it, nothing is configurable. This story establishes the data model, YAML parsing, validation, and resolution order — but does NOT wire it into the store yet.

#### Acceptance Criteria

- [x] New `src/tapps_brain/profile.py` module with Pydantic v2 models: `LayerDefinition`, `PromotionThreshold`, `ScoringConfig`, `GCConfig`, `RecallProfileConfig`, `LimitsConfig`, `MemoryProfile`
- [x] `MemoryProfile` supports `extends` field for profile inheritance (max depth 3)
- [x] `load_profile(path: Path) -> MemoryProfile` loads and validates a YAML file
- [x] `resolve_profile(project_dir: Path, profile_name: str | None) -> MemoryProfile` implements resolution order: project → user-global → built-in → hardcoded default
- [x] `get_builtin_profile(name: str) -> MemoryProfile` returns a built-in profile by name
- [x] `list_builtin_profiles() -> list[str]` returns available profile names
- [x] Validation: layer names must be unique, scoring weights must sum to ~1.0, half_life >= 1
- [x] Unit tests: load valid YAML, reject invalid YAML, test inheritance merging, test resolution order

---

### STORY-010.2: Ship 6 built-in profiles

**Status:** done
**Effort:** M
**Depends on:** STORY-010.1
**Context refs:** `docs/planning/DESIGN-CONFIGURABLE-MEMORY-PROFILES.md` (Section 7)
**Verification:** `pytest tests/unit/test_profile.py::TestBuiltinProfiles -v`

#### Why

Built-in profiles are the product. Users need ready-to-use presets for common use cases, not just an empty framework. These also serve as documentation-by-example for custom profiles.

#### Acceptance Criteria

- [x] `src/tapps_brain/profiles/` directory with 6 YAML files: `repo-brain.yaml`, `personal-assistant.yaml`, `customer-support.yaml`, `research-knowledge.yaml`, `project-management.yaml`, `home-automation.yaml`
- [x] Files included as package data via `pyproject.toml` build config
- [x] `get_builtin_profile("repo-brain")` returns a profile matching current hardcoded behavior exactly (180/60/30/14 half-lives, 40/30/15/15 weights)
- [x] Each profile loads and validates without errors
- [x] Unit test: each built-in profile loads, has valid layers, weights sum to 1.0
- [x] Unit test: `repo-brain` profile produces identical `DecayConfig` values to current hardcoded defaults

---

### STORY-010.3: Wire profiles into MemoryStore

**Status:** done
**Effort:** L
**Depends on:** STORY-010.2
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/decay.py`, `src/tapps_brain/gc.py`
**Verification:** `pytest tests/ -v --tb=short --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95`

#### Why

This is the critical integration point. The store must load a profile at init, derive `DecayConfig` from it, and use profile layer definitions for tier validation. This story must maintain 100% backward compatibility — the full test suite must pass unchanged.

#### Acceptance Criteria

- [x] `MemoryStore.__init__()` accepts optional `profile: MemoryProfile | None` parameter
- [x] When no profile is provided, loads from `{project_dir}/.tapps-brain/profile.yaml` → `~/.tapps-brain/profile.yaml` → built-in `repo-brain`
- [x] `store.profile` property exposes the active profile for introspection
- [x] `DecayConfig` is derived from profile layer definitions (half-lives, confidence floor/ceilings)
- [x] `GCConfig` thresholds read from profile instead of module constants
- [x] Tier validation on `store.save()` checks against profile layer names (not just `MemoryTier` enum)
- [x] Unknown tier names in existing data fall back gracefully to the lowest half-life layer
- [x] **ALL existing tests pass unchanged** with default `repo-brain` profile
- [x] New integration test: create store with `personal-assistant` profile, save entries with `identity`/`long-term`/`short-term`/`ephemeral` tiers, verify decay uses profile half-lives

---

### STORY-010.4: Configurable scoring weights

**Status:** done
**Effort:** S
**Depends on:** STORY-010.3
**Context refs:** `src/tapps_brain/retrieval.py`, `src/tapps_brain/recall.py`
**Verification:** `pytest tests/unit/test_retrieval.py tests/unit/test_recall.py -v --cov=tapps_brain.retrieval --cov-report=term-missing`

#### Why

Different use cases need different scoring balances. A personal assistant weights recency heavily; a research agent weights relevance. The retriever must read weights from the profile instead of module-level constants.

#### Acceptance Criteria

- [x] `MemoryRetriever.__init__()` accepts optional `scoring_config: ScoringConfig | None`
- [x] When provided, uses `scoring_config` weights instead of `_W_RELEVANCE`, `_W_CONFIDENCE`, `_W_RECENCY`, `_W_FREQUENCY` constants
- [x] `_BM25_NORM_K` and `_FREQUENCY_CAP` also read from `ScoringConfig`
- [x] `MemoryStore` passes `profile.scoring` to its retriever
- [x] `RecallConfig` defaults read from `profile.recall`
- [x] Unit test: retriever with custom weights (recency=0.5) ranks recent entries higher
- [x] Unit test: default `ScoringConfig()` produces identical behavior to current constants

---

### STORY-010.5: Promotion and demotion engine

**Status:** done
**Effort:** L
**Depends on:** STORY-010.3
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/gc.py`, `docs/planning/DESIGN-CONFIGURABLE-MEMORY-PROFILES.md` (Section 6)
**Verification:** `pytest tests/unit/test_promotion.py -v --cov=tapps_brain.promotion --cov-report=term-missing`

#### Why

Configurable layers without promotion/demotion are just renamed tiers. The real value is memories flowing between layers based on usage — a context note accessed 10 times over 30 days should become a pattern. This is what separates a "storage system with labels" from a "learning memory system."

#### Acceptance Criteria

- [x] New `src/tapps_brain/promotion.py` module with `PromotionEngine` class
- [x] `PromotionEngine.check_promotion(entry, profile) -> str | None` returns target tier name if promotion criteria met, else None
- [x] `PromotionEngine.check_demotion(entry, profile) -> str | None` returns target tier name if demotion criteria met, else None
- [x] Promotion criteria from profile: `min_access_count`, `min_age_days`, `min_confidence`
- [x] Demotion criteria: effective confidence near floor AND no access within half-life period
- [x] `store.reinforce()` calls `check_promotion()` after updating access count; if promoted, updates tier and logs to audit JSONL
- [x] GC `identify_candidates()` calls `check_demotion()` before archival; demoted entries get a new tier instead of being archived
- [x] Desirable difficulty bonus: reinforcement boost scales with `(1.0 - decayed_confidence)` — nearly-forgotten memories get bigger boosts
- [x] Stability growth: effective half-life grows with `reinforce_count` via `log1p(reinforce_count) * 0.3` multiplier
- [x] Unit tests: promotion triggers at threshold, no promotion below threshold, demotion on stale high-tier entry, audit log records tier changes

---

### STORY-010.6: Enhanced decay models — power-law and importance tags

**Status:** done
**Effort:** M
**Depends on:** STORY-010.3
**Context refs:** `src/tapps_brain/decay.py`, `docs/planning/DESIGN-CONFIGURABLE-MEMORY-PROFILES.md` (Section 3)
**Verification:** `pytest tests/unit/test_decay.py -v --cov=tapps_brain.decay --cov-report=term-missing`

#### Why

Exponential decay works well for code memories but drops too fast for long-lived knowledge (user identity, established research). Power-law decay has a longer tail — old memories fade more slowly than exponential predicts. Importance tags let critical/safety-tagged memories resist decay.

#### Acceptance Criteria

- [x] `calculate_decayed_confidence()` accepts `decay_model` parameter: `"exponential"` (default, current behavior) or `"power_law"`
- [x] Power-law formula: `C₀ × (1 + t / (k × H))^(-β)` where `β` = `decay_exponent` from layer definition, `k` = scaling constant (default 9)
- [x] When `decay_model="exponential"` and default parameters, behavior is identical to current code
- [x] Importance tags: `effective_half_life = base_half_life * max(importance_multipliers for matching tags)`
- [x] Layer definition's `importance_tags` dict maps tag names to multiplier floats
- [x] `DecayConfig` extended with `decay_model` and `decay_exponent` fields (defaults preserve current behavior)
- [x] Unit test: power-law decay is initially faster but has longer tail than exponential at same half-life
- [x] Unit test: importance tag "critical" with multiplier 2.0 doubles effective half-life
- [x] Unit test: exponential model with default params produces identical output to current code

---

### STORY-010.7: Profile CLI commands and MCP tools

**Status:** done
**Effort:** S
**Depends on:** STORY-010.3
**Context refs:** `src/tapps_brain/cli.py`, `src/tapps_brain/mcp_server.py`
**Verification:** `pytest tests/unit/test_cli.py tests/unit/test_mcp_server.py -v`

#### Why

Users need to see what profile is active, list available profiles, and switch profiles without editing YAML by hand. MCP clients need the same capability via tools.

#### Acceptance Criteria

- [x] CLI: `tapps-brain profile show` — displays active profile name, layer count, layer names + half-lives
- [x] CLI: `tapps-brain profile list` — lists built-in profiles with descriptions
- [x] CLI: `tapps-brain profile set <name>` — writes `profile.yaml` to project dir, confirms switch
- [x] CLI: `tapps-brain profile layers` — shows layer details including promotion/demotion rules
- [x] MCP tool: `profile_info()` — returns active profile name, layers, scoring config as JSON
- [x] MCP tool: `profile_switch(name: str)` — switches profile and returns confirmation
- [x] Unit tests for CLI commands and MCP tools

---

### STORY-010.8: Integration tests — cross-profile round-trip

**Status:** done
**Effort:** M
**Depends on:** STORY-010.4, STORY-010.5, STORY-010.6
**Context refs:** `tests/integration/`
**Verification:** `pytest tests/integration/test_profile_integration.py -v --cov=tapps_brain --cov-report=term-missing`

#### Why

Unit tests validate individual components. Integration tests validate the full round-trip: profile loading → custom layers → decay → scoring → promotion → recall. This catches wiring issues between components.

#### Acceptance Criteria

- [x] Integration test: `personal-assistant` profile — save entries to `identity`/`long-term`/`short-term`/`ephemeral`, verify correct decay rates per layer
- [x] Integration test: promotion — save `short-term` entry, reinforce it 5+ times over 7+ days, verify it promotes to `long-term`
- [x] Integration test: demotion — save `long-term` entry, let it decay to floor, run GC, verify it demotes to `short-term` instead of archiving
- [x] Integration test: power-law decay on `identity` tier — verify it retains higher confidence than exponential at 365 days
- [x] Integration test: importance tags — save entry with `critical` tag, verify doubled half-life in decay calculation
- [x] Integration test: custom scoring weights — `personal-assistant` profile's recency=0.30 ranks recent entries higher than `repo-brain`'s recency=0.15
- [x] Integration test: `repo-brain` profile produces identical recall results to a store with no profile (backward compat)
- [x] All tests use real `MemoryStore` + SQLite (no mocks)
- [x] Overall coverage stays at 95%+

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | 010.1 — Profile data model | M | Foundation: all other stories depend on this |
| 2 | 010.2 — Built-in profiles | M | Product value: presets users can test immediately |
| 3 | 010.3 — Wire into MemoryStore | L | Critical integration: makes profiles functional |
| 4 | 010.4 — Configurable scoring | S | Quick win after 010.3; high user impact |
| 5 | 010.5 — Promotion/demotion | L | Core new feature; can parallel with 010.6 |
| 6 | 010.6 — Enhanced decay models | M | Math change; can parallel with 010.5 |
| 7 | 010.7 — CLI & MCP tools | S | Interface polish; depends on 010.3 |
| 8 | 010.8 — Integration tests | M | Validates full round-trip; depends on 010.4-6 |

## Dependency Graph

```
010.1 (model) → 010.2 (profiles) → 010.3 (wire) ──┬──→ 010.4 (scoring) ──┐
                                                    ├──→ 010.5 (promotion) ├──→ 010.8 (integration)
                                                    ├──→ 010.6 (decay)  ───┘
                                                    └──→ 010.7 (CLI/MCP)
```

## Testability Checkpoints

| After Story | What You Can Test |
|-------------|-------------------|
| 010.2 | Load any profile YAML, inspect layers/weights, validate schema |
| 010.3 | Create a store with any profile, save/search with custom tier names |
| 010.4 | Verify scoring produces different rankings per profile |
| 010.5 | Watch memories promote/demote based on access patterns |
| 010.8 | Full end-to-end with all profiles |
