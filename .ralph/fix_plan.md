# Ralph Fix Plan — tapps-brain

Aligned with the repo as of **2026-03-22** (updated with BUG-002 from deep review). For full story text, see `docs/planning/epics/EPIC-*.md`.

**Task sizing:** Each item is scoped to ONE Ralph loop (~15 min). Do one, check it off, commit.

## Active — BUG-001: Pre-Review Critical Fixes

**Depends on:** EPIC-016 ✅
**Target:** Before EPIC-017 starts
**Source:** External code review (TappsMCP maintainer, 2026-03-21)

**Goal:** Fix 7 concrete bugs and regressions found during tapps-brain code review. These are targeted fixes, not full-file reviews — do them before the comprehensive EPIC-017+ review cycle. Each is one Ralph loop.

### Phase 1: Correctness Bugs (sequential — highest impact)

#### BUG-001-B: `decay_config_from_profile` uses `dict[str, Any]` — type safety regression
- [x] In `decay.py:decay_config_from_profile()`, `legacy: dict[str, Any]` was widened from `dict[str, int]` to silence a type error. The actual values are always `int` (from `layer.half_life_days`). The real issue is that `DecayConfig(**legacy)` splats into a constructor with mixed field types. Fix: keep `dict[str, int]` and use explicit kwargs instead of `**legacy` splat: `DecayConfig(architectural_half_life_days=legacy.get("architectural_half_life_days", 180), ...)`. This restores type safety. Add a unit test verifying `decay_config_from_profile` returns correct types for all fields. Commit: `fix: restore type safety in decay_config_from_profile`

#### BUG-001-C: HiveStore connection leak on exception in MCP handlers
- [x] In `mcp_server.py`, MCP tool handlers `hive_status`, `hive_search`, `hive_propagate` create `HiveStore()` instances and call `.close()` but without `try/finally`. If the operation raises, the SQLite connection leaks. Fix: wrap all `HiveStore()` usage in `try/finally` blocks that call `.close()` in the `finally` clause. Better: add `__enter__`/`__exit__` to `HiveStore` so it can be used as a context manager, then use `with HiveStore() as hive:` in MCP handlers. Add a unit test that mocks HiveStore.search to raise, verify .close() is still called. Commit: `fix: prevent HiveStore connection leak on MCP handler exceptions`

### Phase 2: Type Safety & Robustness (independent — all can run parallel)

#### BUG-001-D: Add structlog warning for silent tier fallback in `_get_half_life`
- [x] In `decay.py:_get_half_life()`, unknown tier strings silently fall back to `context_half_life_days` (14 days). Add `logger.warning("unknown_tier_fallback", tier=tier_str, fallback_days=config.context_half_life_days)` before the fallback return. This makes misconfigured profiles debuggable. Add a unit test that triggers the fallback and verifies the log message. Commit: `fix: log warning on unknown tier fallback in decay`

#### BUG-001-E: Add `server.json` to version consistency test
- [x] `tests/unit/test_version_consistency.py` checks `pyproject.toml` vs `__init__.py` but not `server.json` (which has a hardcoded `"version": "1.1.0"`). Add `server.json` to the consistency check. Verify it matches current version. Fix the version if it has drifted. Commit: `fix: include server.json in version consistency check`

#### BUG-001-F: Narrow bare `except Exception` in MCP Hive tools
- [x] In `mcp_server.py`, MCP hive/registry tool handlers catch bare `except Exception` which swallows unexpected errors silently. Narrow to `except (ValueError, OSError, sqlite3.Error)` and add `logger.exception("hive_tool_error", tool=...)` for observability. Keep the JSON error response format. Add a unit test with a mocked unexpected exception to verify it's not swallowed. Commit: `fix: narrow exception handling in MCP Hive tools`

#### BUG-001-G: Migrate remaining `timezone.utc` to `UTC`
- [x] Search all Python files for `timezone.utc` usage. The test suite partially migrated to `from datetime import UTC` but some files may still use the old pattern. Standardize on `datetime.UTC` (Python 3.11+). If no remaining instances, mark as done. Commit: `chore: standardize on datetime.UTC across codebase`

---

## Active — BUG-002: Source Trust Regression & Uncommitted WIP

**Depends on:** EPIC-016 ✅
**Target:** Before EPIC-017 starts
**Source:** Deep code review (2026-03-22)

**Goal:** The uncommitted source_trust feature (M2) introduces a regression that breaks `test_hive_integration.py::TestBackwardCompat::test_no_hive_store_works_normally`. Fix the regression, commit the feature correctly, and address related issues discovered during review.

### Phase 1: Critical Regression (must fix first)

#### BUG-002-A: Source trust multiplier causes recall failure for agent-sourced memories
- [ ] **Root cause:** `inject_memories()` in `injection.py:100` creates `MemoryRetriever()` without passing `scoring_config`, so the retriever uses `_DEFAULT_SOURCE_TRUST` which penalizes `agent` source to 0.7x. Since `MemoryEntry.source` defaults to `agent`, most memories get their composite score multiplied by 0.7. For marginal scores (short queries like "v1"), this pushes the score below the `_MIN_SCORE = 0.3` cutoff in `injection.py:126`, returning zero results. **Fix:** Pass the store's profile `scoring_config` through to `inject_memories()` and into `MemoryRetriever()`. The `RecallOrchestrator` already has access to the profile — thread `scoring_config` from `recall.py` → `inject_memories()` → `MemoryRetriever()`. Alternatively, adjust `_MIN_SCORE` threshold to account for the trust multiplier (e.g. `_MIN_SCORE = 0.2`), but the cleaner fix is to thread the config. Add a regression test: save with default source, recall short query, verify `memory_count >= 1`. Commit: `fix: thread scoring_config through inject_memories to prevent source trust regression`

#### BUG-002-B: `inject_memories()` ignores profile scoring weights entirely
- [ ] **Related:** Even without the source_trust regression, `inject_memories()` always creates `MemoryRetriever(config=decay_config)` and never passes `scoring_config` from the active profile. This means profile-specific scoring weights (relevance/confidence/recency/frequency) are ignored during injection. Fix: add `scoring_config: ScoringConfig | None = None` parameter to `inject_memories()`, pass it through to `MemoryRetriever()`. Update `RecallOrchestrator.recall()` to pass the profile's scoring config. Commit: `fix: inject_memories respects profile scoring weights`

### Phase 2: Uncommitted WIP Cleanup (independent)

#### BUG-002-C: Commit source_trust feature after regression is fixed
- [ ] The following files have valid uncommitted changes that implement source_trust (M2) and relation_count metrics: `metrics.py` (relation_count field), `profile.py` (ScoringConfig.source_trust), `retrieval.py` (trust multiplier in scoring), `store.py` (count_relations method), `profiles/repo-brain.yaml` (source_trust config + consolidation tuning). After BUG-002-A/B are fixed, stage and commit all source_trust changes together. Also commit `tests/unit/test_source_trust.py`. Commit: `feat: source trust multipliers for per-source scoring (M2)`

#### BUG-002-D: Schema v8 migration — update tests expecting v7
- [ ] The uncommitted changes introduced schema v8 (likely from `relation_count` or `count_relations()` additions). Tests hardcoded to expect `v7` now fail: `test_schema_version` (expects 7, gets 8), `test_stats`/`test_stats_json` (expect "Schema: v7"), `test_migrate` (3 migration tests), `test_agent_create_invalid_profile`. Fix: update all v7 assertions to v8, verify migration path v7→v8 works, add migration test for v7→v8. Commit: `fix: update schema version assertions from v7 to v8`

#### BUG-002-E: Integrity hash mismatch from new fields
- [ ] The 6 `test_verify_integrity.py` failures (`test_valid_entries_pass`, `test_tampered_*`, `test_mixed_*`) show `verified == 0` where entries should verify. The `relation_count` field addition or other model changes altered the hash computation, breaking existing integrity checks. Fix: verify `integrity.py` hash computation includes the correct fields and update the integrity test fixtures to match. Commit: `fix: update integrity hash computation for new model fields`

#### BUG-002-F: Consolidation threshold changes in repo-brain.yaml need justification test
- [ ] The `repo-brain.yaml` changes include `min_access_count` reductions (pattern: 8→5, procedural: 5→3). These make consolidation trigger earlier. Add a test or verify existing tests cover these thresholds to ensure they don't cause premature consolidation. Commit: `test: verify updated consolidation thresholds in repo-brain profile`

---

## Active — EPIC-017: Code Review — Storage & Data Model

**Depends on:** EPIC-016 ✅
**Target:** 2026-04-30

**Goal:** Full code review of core storage layer and data model files. For each file: check for bugs, dead code, security issues, performance problems, incorrect error handling, type safety gaps, and style violations. Fix all issues found. Commit with `review(story-017.N): description of fixes`.

**Review checklist per file:** (1) Correctness: logic bugs, off-by-one, race conditions. (2) Security: injection, unsanitized input, credential leaks. (3) Performance: unnecessary copies, N+1 queries, missing indexes. (4) Dead code: unreachable branches, unused imports/vars. (5) Error handling: swallowed exceptions, missing validation. (6) Type safety: Any casts, missing None checks. (7) Style: naming, complexity, docstring accuracy.

### Phase 1: Core Storage (sequential — store.py is largest)

#### 017-A: Review `store.py` — core CRUD and state management (lines 1–750)
- [ ] Review `src/tapps_brain/store.py` lines 1–750. Focus on: `__init__`, `save()`, `recall()`, `delete()`, `get()`, `list_entries()`, thread-safety of Lock usage, write-through cache consistency. Fix all issues. Commit: `review(story-017.1): store.py core CRUD review`

#### 017-B: Review `store.py` — advanced features (lines 751–end)
- [ ] Review `src/tapps_brain/store.py` lines 751–end. Focus on: `reinforce()`, `ingest_context()`, `index_session()`, `search_sessions()`, `cleanup_sessions()`, `validate_entries()`, `health()`, `get_metrics()`, Hive propagation, MCP integration points. Fix all issues. Commit: `review(story-017.2): store.py advanced features review`

#### 017-C: Review `persistence.py` — SQLite layer
- [ ] Review `src/tapps_brain/persistence.py` (936 lines). Focus on: WAL mode correctness, FTS5 index updates, schema migrations v1→v7, SQL injection risk in any string formatting, connection lifecycle, error handling on disk full / locked DB. Fix all issues. Commit: `review(story-017.3): persistence.py SQLite layer review`

#### 017-D: Review `models.py` — Pydantic data models
- [ ] Review `src/tapps_brain/models.py` (327 lines). Focus on: Pydantic v2 validators, field defaults, serialization round-trip correctness, `MemoryEntry` / `ConsolidatedEntry` / `RecallResult` consistency, `MemoryTier` enum completeness, `agent_scope` validation. Fix all issues. Commit: `review(story-017.4): models.py data model review`

### Phase 2: Supporting Storage (all independent)

#### 017-E: Review `__init__.py` — public API surface
- [ ] Review `src/tapps_brain/__init__.py` (147 lines). Focus on: exported symbols match actual public API, no internal modules leaked, `__all__` completeness, version string consistency. Fix all issues. Commit: `review(story-017.5): __init__.py public API review`

#### 017-F: Review `_protocols.py` + `_feature_flags.py` — extension interfaces
- [ ] Review `src/tapps_brain/_protocols.py` (106 lines) and `src/tapps_brain/_feature_flags.py` (77 lines). Focus on: Protocol definitions match implementations, lazy import correctness, feature flag detection reliability, fallback behavior when optional deps missing. Fix all issues. Commit: `review(story-017.6): protocols and feature flags review`

#### 017-G: Review `audit.py` + `session_index.py` — logging and sessions
- [ ] Review `src/tapps_brain/audit.py` (152 lines) and `src/tapps_brain/session_index.py` (97 lines). Focus on: JSONL write atomicity, log rotation / size limits, session cleanup correctness, file handle leaks. Fix all issues. Commit: `review(story-017.7): audit and session index review`

#### 017-H: Review `integrity.py` — hash verification
- [ ] Review `src/tapps_brain/integrity.py` (141 lines). Focus on: hash algorithm choice, tamper detection reliability, performance of hash computation, edge cases (empty entries, None fields). Fix all issues. Commit: `review(story-017.8): integrity verification review`

## Active — EPIC-018: Code Review — Retrieval & Scoring

**Depends on:** EPIC-016 ✅
**Target:** 2026-04-30

**Goal:** Full code review of all retrieval, scoring, ranking, and search files. Fix all issues found.

**Review checklist:** Same as EPIC-017.

### Phase 1: Core Retrieval (sequential)

#### 018-A: Review `retrieval.py` — composite scoring engine
- [ ] Review `src/tapps_brain/retrieval.py` (672 lines). Focus on: scoring weight correctness (40/30/15/15), edge cases in score normalization, BM25 integration, vector search fallback, result ordering stability. Fix all issues. Commit: `review(story-018.1): retrieval.py scoring engine review`

#### 018-B: Review `recall.py` — recall orchestration
- [ ] Review `src/tapps_brain/recall.py` (411 lines). Focus on: Hive-aware merging logic, configurable weight (0.8 default), deduplication across local + Hive results, `hive_memory_count` accuracy, token budget enforcement. Fix all issues. Commit: `review(story-018.2): recall.py orchestration review`

### Phase 2: Scoring Components (all independent)

#### 018-C: Review `bm25.py` + `fusion.py` — text scoring
- [ ] Review `src/tapps_brain/bm25.py` (227 lines) and `src/tapps_brain/fusion.py` (42 lines). Focus on: BM25 parameter tuning (k1, b), term frequency correctness, IDF calculation, Reciprocal Rank Fusion k constant, empty corpus handling. Fix all issues. Commit: `review(story-018.3): BM25 and fusion scoring review`

#### 018-D: Review `similarity.py` — similarity computation
- [ ] Review `src/tapps_brain/similarity.py` (318 lines). Focus on: Jaccard + TF-IDF correctness, threshold calibration, edge cases (empty strings, identical entries), performance on large sets. Fix all issues. Commit: `review(story-018.4): similarity computation review`

#### 018-E: Review `embeddings.py` + `reranker.py` — optional ML components
- [ ] Review `src/tapps_brain/embeddings.py` (148 lines) and `src/tapps_brain/reranker.py` (186 lines). Focus on: lazy import safety, graceful fallback when deps missing, embedding dimension consistency, reranker score normalization, batch processing correctness. Fix all issues. Commit: `review(story-018.5): embeddings and reranker review`

## Active — EPIC-019: Code Review — Memory Lifecycle

**Depends on:** EPIC-016 ✅
**Target:** 2026-04-30

**Goal:** Full code review of memory lifecycle management: decay, consolidation, GC, promotion, reinforcement. Fix all issues found.

**Review checklist:** Same as EPIC-017.

### Phase 1: Decay & Consolidation (sequential)

#### 019-A: Review `decay.py` — exponential decay
- [ ] Review `src/tapps_brain/decay.py` (327 lines). Focus on: half-life correctness per tier (arch 180d, context 14d), lazy evaluation timing, decay floor (never reaches exactly 0?), timezone handling, edge cases (future timestamps, negative intervals). Fix all issues. Commit: `review(story-019.1): decay.py exponential decay review`

#### 019-B: Review `consolidation.py` — deterministic merging
- [ ] Review `src/tapps_brain/consolidation.py` (486 lines). Focus on: Jaccard + TF-IDF similarity threshold correctness, merge conflict resolution, metadata preservation during merge, no-LLM invariant, idempotency of repeated consolidation. Fix all issues. Commit: `review(story-019.2): consolidation.py merging review`

#### 019-C: Review `auto_consolidation.py` — automatic lifecycle
- [ ] Review `src/tapps_brain/auto_consolidation.py` (376 lines). Focus on: trigger conditions, threshold tuning, interaction with manual consolidation, thread safety, config update correctness, performance under large entry counts. Fix all issues. Commit: `review(story-019.3): auto_consolidation.py lifecycle review`

### Phase 2: GC, Promotion, Reinforcement (all independent)

#### 019-D: Review `gc.py` + `promotion.py` — garbage collection and tier promotion
- [ ] Review `src/tapps_brain/gc.py` (202 lines) and `src/tapps_brain/promotion.py` (161 lines). Focus on: archive-not-delete semantics, GC threshold correctness, promotion criteria accuracy, interaction between GC and promotion (promote before GC?). Fix all issues. Commit: `review(story-019.4): GC and promotion review`

#### 019-E: Review `reinforcement.py` + `extraction.py` — memory strengthening and extraction
- [ ] Review `src/tapps_brain/reinforcement.py` (61 lines) and `src/tapps_brain/extraction.py` (122 lines). Focus on: reinforcement score capping, extraction accuracy, edge cases (empty context, very long input). Fix all issues. Commit: `review(story-019.5): reinforcement and extraction review`

## Active — EPIC-020: Code Review — Safety & Validation

**Depends on:** EPIC-016 ✅
**Target:** 2026-04-30

**Goal:** Full code review of safety, injection detection, validation, and contradiction handling. These are security-critical — extra scrutiny required.

**Review checklist:** Same as EPIC-017, with emphasis on (2) Security.

### Phase 1: Security-Critical (sequential)

#### 020-A: Review `safety.py` + `injection.py` — prompt injection defense
- [ ] Review `src/tapps_brain/safety.py` (171 lines) and `src/tapps_brain/injection.py` (200 lines). Focus on: pattern completeness (OWASP prompt injection patterns), false positive/negative rates, bypass vectors (unicode normalization, encoding tricks), sanitization vs blocking decisions. Fix all issues. Commit: `review(story-020.1): safety and injection defense review`

#### 020-B: Review `doc_validation.py` — document validation
- [ ] Review `src/tapps_brain/doc_validation.py` (927 lines). Focus on: validation rule completeness, pluggable `LookupEngineLike` contract adherence, error message clarity, edge cases (empty docs, malformed input), performance on large documents. Fix all issues. Commit: `review(story-020.2): doc_validation.py review`

### Phase 2: Data Integrity (all independent)

#### 020-C: Review `contradictions.py` — contradiction detection
- [ ] Review `src/tapps_brain/contradictions.py` (242 lines). Focus on: detection accuracy, false positive handling, resolution strategy correctness, interaction with consolidation (contradictions block merge?). Fix all issues. Commit: `review(story-020.3): contradictions detection review`

#### 020-D: Review `seeding.py` — initial memory bootstrap
- [ ] Review `src/tapps_brain/seeding.py` (235 lines). Focus on: idempotency (seed twice = no duplicates), tier assignment correctness, interaction with existing entries, error handling on malformed seed data. Fix all issues. Commit: `review(story-020.4): seeding bootstrap review`

#### 020-E: Review `rate_limiter.py` — rate limiting
- [ ] Review `src/tapps_brain/rate_limiter.py` (182 lines). Focus on: rate limit algorithm correctness (token bucket? sliding window?), thread safety, clock skew handling, configuration validation, bypass vectors. Fix all issues. Commit: `review(story-020.5): rate limiter review`

## Active — EPIC-021: Code Review — Federation, Hive & Relations

**Depends on:** EPIC-016 ✅
**Target:** 2026-04-30

**Goal:** Full code review of cross-project and cross-agent sharing systems. Fix all issues found.

**Review checklist:** Same as EPIC-017.

### Phase 1: Federation (sequential)

#### 021-A: Review `federation.py` — cross-project sharing
- [ ] Review `src/tapps_brain/federation.py` (747 lines). Focus on: hub DB at `~/.tapps-brain/memory/federated.db` lifecycle, subscription management correctness, publish/subscribe conflict resolution, stale subscription cleanup, path traversal risk in project dirs. Fix all issues. Commit: `review(story-021.1): federation.py cross-project review`

### Phase 2: Hive (sequential)

#### 021-B: Review `hive.py` — cross-agent sharing (lines 1–300)
- [ ] Review `src/tapps_brain/hive.py` lines 1–300. Focus on: `HiveStore` init, SQLite WAL + FTS5 setup, namespace management, `save()` / `search()` / `recall()` operations, thread safety. Fix all issues. Commit: `review(story-021.2): hive.py HiveStore core review`

#### 021-C: Review `hive.py` — AgentRegistry and PropagationEngine (lines 301–end)
- [ ] Review `src/tapps_brain/hive.py` lines 301–end. Focus on: `AgentRegistry` YAML persistence, `PropagationEngine` routing logic (private/domain/hive), `ConflictPolicy` resolution correctness, backward compatibility when Hive disabled. Fix all issues. Commit: `review(story-021.3): hive.py registry and propagation review`

### Phase 3: Relations (independent)

#### 021-D: Review `relations.py` — knowledge graph
- [ ] Review `src/tapps_brain/relations.py` (305 lines). Focus on: graph traversal correctness (BFS/DFS), max_hops boundary, cycle detection, relation type validation, query performance on large graphs. Fix all issues. Commit: `review(story-021.4): relations.py knowledge graph review`

## Active — EPIC-022: Code Review — Interfaces (MCP, CLI, IO)

**Depends on:** EPIC-016 ✅
**Target:** 2026-05-15

**Goal:** Full code review of all user-facing interfaces: MCP server (41 tools), CLI, IO, and markdown import. Fix all issues found.

**Review checklist:** Same as EPIC-017.

### Phase 1: MCP Server (sequential — largest file)

#### 022-A: Review `mcp_server.py` — server setup and core memory tools (lines 1–500)
- [ ] Review `src/tapps_brain/mcp_server.py` lines 1–500. Focus on: argparse, server init, `memory_save`, `memory_recall`, `memory_delete`, `memory_get`, `memory_list` tools. Check input validation, error responses, JSON serialization. Fix all issues. Commit: `review(story-022.1): mcp_server.py core tools review`

#### 022-B: Review `mcp_server.py` — Hive, graph, and audit tools (lines 501–1000)
- [ ] Review `src/tapps_brain/mcp_server.py` lines 501–1000. Focus on: `hive_*` tools, `memory_relations`, `memory_find_related`, `memory_query_relations`, `memory_audit` tools. Check Hive fallback paths, graph query correctness. Fix all issues. Commit: `review(story-022.2): mcp_server.py Hive and graph tools review`

#### 022-C: Review `mcp_server.py` — tags, config, agent, profile tools + resources (lines 1001–end)
- [ ] Review `src/tapps_brain/mcp_server.py` lines 1001–end. Focus on: `memory_*_tags`, `memory_gc_config*`, `memory_consolidation_config*`, `agent_*`, `profile_*` tools, MCP resources, prompts. Check config validation, agent lifecycle correctness. Fix all issues. Commit: `review(story-022.3): mcp_server.py config and agent tools review`

### Phase 2: CLI (sequential — second largest file)

#### 022-D: Review `cli.py` — core commands (lines 1–750)
- [ ] Review `src/tapps_brain/cli.py` lines 1–750. Focus on: Click group setup, `save`, `recall`, `delete`, `get`, `list`, `search`, `import/export` commands. Check argument validation, output formatting, error messages. Fix all issues. Commit: `review(story-022.4): cli.py core commands review`

#### 022-E: Review `cli.py` — advanced commands (lines 751–end)
- [ ] Review `src/tapps_brain/cli.py` lines 751–end. Focus on: `federation`, `maintenance`, `agent`, `profile`, `relations`, `audit`, `tags` command groups. Check subcommand consistency, help text accuracy, error paths. Fix all issues. Commit: `review(story-022.5): cli.py advanced commands review`

### Phase 3: IO and Import (all independent)

#### 022-F: Review `io.py` — import/export operations
- [ ] Review `src/tapps_brain/io.py` (336 lines). Focus on: JSONL/CSV format handling, encoding correctness (UTF-8 BOM?), large file streaming, data loss risk during import, idempotency. Fix all issues. Commit: `review(story-022.6): io.py import/export review`

#### 022-G: Review `markdown_import.py` — markdown parsing
- [ ] Review `src/tapps_brain/markdown_import.py` (267 lines). Focus on: heading level → tier mapping accuracy, slug generation, deduplication logic, daily note date extraction regex, malformed markdown handling. Fix all issues. Commit: `review(story-022.7): markdown_import.py review`

## Active — EPIC-023: Code Review — Config, Profiles & Observability

**Depends on:** EPIC-016 ✅
**Target:** 2026-05-15

**Goal:** Full code review of configuration, profiles, metrics, and observability. Fix all issues found.

**Review checklist:** Same as EPIC-017.

### Phase 1: Profiles (sequential)

#### 023-A: Review `profile.py` — profile loading and validation
- [ ] Review `src/tapps_brain/profile.py` (366 lines). Focus on: YAML loading safety (yaml.safe_load), profile inheritance (base + extends), validation completeness, default profile correctness, path traversal in profile loading. Fix all issues. Commit: `review(story-023.1): profile.py review`

#### 023-B: Review all profile YAML files
- [ ] Review all 6 files in `src/tapps_brain/profiles/`: `repo-brain.yaml`, `customer-support.yaml`, `home-automation.yaml`, `personal-assistant.yaml`, `project-management.yaml`, `research-knowledge.yaml`. Focus on: tier weight consistency, decay half-life reasonableness, missing required fields, cross-profile consistency. Fix all issues. Commit: `review(story-023.2): profile YAML files review`

### Phase 2: Observability (all independent)

#### 023-C: Review `metrics.py` + `otel_exporter.py` — observability stack
- [ ] Review `src/tapps_brain/metrics.py` (208 lines) and `src/tapps_brain/otel_exporter.py` (88 lines). Focus on: metric naming conventions, counter overflow handling, OpenTelemetry integration correctness, missing metrics for key operations. Fix all issues. Commit: `review(story-023.3): metrics and OTel review`

## Active — EPIC-024: Code Review — Unit Tests (Part 1)

**Depends on:** EPIC-016 ✅
**Target:** 2026-05-31

**Goal:** Review all unit test files for: test quality, missing edge cases, flaky test patterns, proper isolation, assertion completeness, and fixture hygiene. Fix all issues found.

**Review checklist per test file:** (1) Coverage: are key paths tested? (2) Assertions: are they specific enough? (3) Isolation: no cross-test pollution? (4) Fixtures: proper setup/teardown, no resource leaks? (5) Flakiness: time-dependent tests, random ordering issues? (6) Naming: test names describe behavior?

### Phase 1: Core Storage Tests (sequential)

#### 024-A: Review `test_mcp_server.py` (2,082 lines)
- [ ] Review `tests/unit/test_mcp_server.py`. Largest test file — check for: redundant tests, missing tool coverage (all 41 tools tested?), proper mocking of store, error path coverage. Fix all issues. Commit: `review(story-024.1): test_mcp_server.py review`

#### 024-B: Review `test_cli.py` (1,320 lines)
- [ ] Review `tests/unit/test_cli.py`. Check: all commands tested, CliRunner usage, error message assertions, `--format json` coverage, help text validation. Fix all issues. Commit: `review(story-024.2): test_cli.py review`

#### 024-C: Review `test_memory_store.py` + `test_memory_persistence.py`
- [ ] Review `tests/unit/test_memory_store.py` (1,065 lines) and `tests/unit/test_memory_persistence.py` (964 lines). Check: CRUD coverage, thread-safety tests, migration tests, WAL mode tests, cache consistency tests. Fix all issues. Commit: `review(story-024.3): store and persistence tests review`

### Phase 2: Validation & Safety Tests (all independent)

#### 024-D: Review `test_coverage_gaps.py` + `test_doc_validation.py`
- [ ] Review `tests/unit/test_coverage_gaps.py` (877 lines) and `tests/unit/test_doc_validation.py` (776 lines). Check: are the "gaps" still gaps or already covered elsewhere? Validation test completeness. Fix all issues. Commit: `review(story-024.4): coverage gaps and validation tests review`

#### 024-E: Review `test_federation.py` + `test_hive.py`
- [ ] Review `tests/unit/test_federation.py` (720 lines) and `tests/unit/test_hive.py` (713 lines). Check: subscription lifecycle, publish/subscribe isolation, Hive propagation paths, conflict resolution coverage. Fix all issues. Commit: `review(story-024.5): federation and hive tests review`

#### 024-F: Review `test_profile.py` + `test_memory_retrieval.py`
- [ ] Review `tests/unit/test_profile.py` (695 lines) and `tests/unit/test_memory_retrieval.py` (662 lines). Check: profile inheritance tests, scoring weight tests, edge case coverage. Fix all issues. Commit: `review(story-024.6): profile and retrieval tests review`

### Phase 3: Lifecycle Tests (all independent)

#### 024-G: Review `test_memory_auto_consolidation.py` + `test_memory_consolidation.py`
- [ ] Review `tests/unit/test_memory_auto_consolidation.py` (574 lines) and `tests/unit/test_memory_consolidation.py` (499 lines). Check: threshold tests, merge correctness assertions, idempotency tests. Fix all issues. Commit: `review(story-024.7): consolidation tests review`

#### 024-H: Review `test_memory_similarity.py` + `test_safety.py`
- [ ] Review `tests/unit/test_memory_similarity.py` (468 lines) and `tests/unit/test_safety.py` (456 lines). Check: similarity edge cases, injection pattern coverage, bypass vector tests. Fix all issues. Commit: `review(story-024.8): similarity and safety tests review`

#### 024-I: Review `test_concurrent.py` + `test_recall.py`
- [ ] Review `tests/unit/test_concurrent.py` (446 lines) and `tests/unit/test_recall.py` (444 lines). Check: thread safety assertions, deadlock detection, recall with/without Hive coverage. Fix all issues. Commit: `review(story-024.9): concurrency and recall tests review`

### Phase 4: Remaining Unit Tests (all independent)

#### 024-J: Review `test_memory_foundation_integration.py` + `test_promotion.py` + `test_memory_io.py`
- [ ] Review `tests/unit/test_memory_foundation_integration.py` (416 lines), `tests/unit/test_promotion.py` (391 lines), and `tests/unit/test_memory_io.py` (384 lines). Check: foundation test relevance, promotion criteria tests, IO round-trip assertions. Fix all issues. Commit: `review(story-024.10): foundation, promotion, IO tests review`

#### 024-K: Review `test_markdown_import.py` + `test_reranker.py` + `test_memory_embeddings.py`
- [ ] Review `tests/unit/test_markdown_import.py` (321 lines), `tests/unit/test_reranker.py` (315 lines), and `tests/unit/test_memory_embeddings.py` (308 lines). Check: markdown edge cases, reranker score tests, embedding dimension tests. Fix all issues. Commit: `review(story-024.11): markdown, reranker, embeddings tests review`

#### 024-L: Review `test_contradictions.py` + `test_memory_models.py` + `test_gc_config.py` + `test_relations.py`
- [ ] Review `tests/unit/test_contradictions.py` (296 lines), `tests/unit/test_memory_models.py` (267 lines), `tests/unit/test_gc_config.py` (262 lines), and `tests/unit/test_relations.py` (254 lines). Fix all issues. Commit: `review(story-024.12): contradictions, models, GC, relations tests review`

#### 024-M: Review `test_source_trust.py` + `test_consolidation_config.py` + `test_memory_decay.py` + `test_memory_bm25.py`
- [ ] Review `tests/unit/test_source_trust.py` (237 lines), `tests/unit/test_consolidation_config.py` (233 lines), `tests/unit/test_memory_decay.py` (227 lines), and `tests/unit/test_memory_bm25.py` (225 lines). Fix all issues. Commit: `review(story-024.13): trust, consolidation config, decay, BM25 tests review`

#### 024-N: Review remaining small unit test files
- [ ] Review `tests/unit/test_rate_limiter.py` (222), `test_memory_injection.py` (211), `test_edge_cases.py` (209), `test_seeding.py` (202), `test_metrics.py` (177), `test_verify_integrity.py` (148), `test_package_api.py` (148), `test_memory_reinforcement.py` (128), `test_memory_gc.py` (127), `test_audit.py` (121), `test_otel_exporter.py` (106), `test_extraction.py` (86), `test_version_consistency.py` (85), `test_sqlite_corruption.py` (76), `test_memory_fusion.py` (74), `test_memory_embeddings_persistence.py` (66), `test_session_index.py` (60). Fix all issues. Commit: `review(story-024.14): remaining small unit tests review`

## Active — EPIC-025: Code Review — Integration Tests, Benchmarks & TypeScript

**Depends on:** EPIC-016 ✅
**Target:** 2026-05-31

**Goal:** Review all integration tests, benchmarks, test infrastructure, TypeScript plugin code, and configuration files. Fix all issues found.

**Review checklist:** Same as EPIC-024 for tests. For TypeScript: type safety, error handling, MCP client correctness.

### Phase 1: Integration Tests (all independent)

#### 025-A: Review `test_mcp_integration.py` + `test_retrieval_integration.py`
- [ ] Review `tests/integration/test_mcp_integration.py` (735 lines) and `tests/integration/test_retrieval_integration.py` (548 lines). Check: real SQLite usage, cleanup, end-to-end tool coverage, retrieval accuracy assertions. Fix all issues. Commit: `review(story-025.1): MCP and retrieval integration tests review`

#### 025-B: Review `test_openclaw_integration.py` + `test_profile_integration.py` + `test_hive_mcp_roundtrip.py`
- [ ] Review `tests/integration/test_openclaw_integration.py` (536 lines), `test_profile_integration.py` (516 lines), and `test_hive_mcp_roundtrip.py` (508 lines). Check: OpenClaw import correctness, profile lifecycle, Hive round-trip assertions. Fix all issues. Commit: `review(story-025.2): OpenClaw, profile, Hive integration tests review`

#### 025-C: Review `test_federation_integration.py` + `test_cross_profile_integration.py` + `test_doc_validation_integration.py`
- [ ] Review `tests/integration/test_federation_integration.py` (472 lines), `test_cross_profile_integration.py` (453 lines), and `test_doc_validation_integration.py` (442 lines). Check: federation hub lifecycle, cross-profile isolation, validation with real DB. Fix all issues. Commit: `review(story-025.3): federation, cross-profile, validation integration tests review`

#### 025-D: Review remaining integration tests
- [ ] Review `tests/integration/test_recall_integration.py` (275), `test_hive_integration.py` (215), `test_temporal_integration.py` (204), `test_session_index_integration.py` (191), `test_graph_integration.py` (174), `test_observability_integration.py` (153), `test_reinforcement_integration.py` (131), `test_extraction_integration.py` (108). Fix all issues. Commit: `review(story-025.4): remaining integration tests review`

### Phase 2: Test Infrastructure & Benchmarks

#### 025-E: Review test infrastructure and benchmarks
- [ ] Review `tests/conftest.py` (40 lines), `tests/factories.py` (80 lines), and `tests/benchmarks/test_benchmarks.py` (166 lines) + `tests/benchmarks/conftest.py` (47 lines). Check: fixture reusability, factory correctness, benchmark methodology, realistic workloads. Fix all issues. Commit: `review(story-025.5): test infrastructure and benchmarks review`

### Phase 3: TypeScript & Config

#### 025-F: Review OpenClaw TypeScript plugin
- [ ] Review `openclaw-plugin/src/index.ts` (294 lines) and `openclaw-plugin/src/mcp_client.ts` (224 lines). Check: MCP client connection handling, hook implementations (bootstrap, ingest, afterTurn, compact), error handling, type safety, rate limiting in afterTurn. Fix all issues. Commit: `review(story-025.6): OpenClaw TypeScript plugin review`

#### 025-G: Review configuration and manifest files
- [ ] Review `pyproject.toml`, `openclaw-plugin/package.json`, `openclaw-plugin/tsconfig.json`, `openclaw-plugin/openclaw.plugin.json`, `openclaw-skill/openclaw.plugin.json`, `openclaw-skill/SKILL.md`, `server.json`. Check: version consistency, dependency pinning, metadata completeness, schema correctness. Fix all issues. Commit: `review(story-025.7): configuration and manifest files review`

## Notes

- **One task per loop.** Each task is sized for ~15 min. If a task is too large, split it and check off the part you finished.
- **Dependency graph (EPIC-017):** 017-A → 017-B. 017-C, 017-D independent. 017-E, 017-F, 017-G, 017-H all independent.
- **Dependency graph (EPIC-018):** 018-A → 018-B. 018-C, 018-D, 018-E all independent.
- **Dependency graph (EPIC-019):** 019-A → 019-B → 019-C. 019-D, 019-E independent.
- **Dependency graph (EPIC-020):** 020-A → 020-B. 020-C, 020-D, 020-E all independent.
- **Dependency graph (EPIC-021):** 021-A independent. 021-B → 021-C. 021-D independent.
- **Dependency graph (EPIC-022):** 022-A → 022-B → 022-C. 022-D → 022-E. 022-F, 022-G independent.
- **Dependency graph (EPIC-023):** 023-A → 023-B. 023-C independent.
- **Dependency graph (EPIC-024):** 024-A, 024-B, 024-C form Phase 1 (independent). All Phase 2–4 tasks independent.
- **Dependency graph (EPIC-025):** All Phase 1 tasks independent. 025-E, 025-F, 025-G all independent.
- Always cross-check the relevant epic file before starting a task.
- Maintain **95%** test coverage; run full lint / type / test suite before committing.
- After completing a task, update this file: change `- [ ]` to `- [x]`.
