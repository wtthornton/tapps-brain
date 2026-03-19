---
id: EPIC-002
title: "Integration wiring — connect standalone modules to the runtime"
status: done
priority: high
created: 2026-03-19
target_date: 2026-04-30
completed: 2026-03-19
tags: [integration, doc-validation, session, extraction, reinforcement, federation, performance]
---

# EPIC-002: Integration Wiring — Connect Standalone Modules to the Runtime

## Context

Epic 1 raised test coverage to 96.59% across 792 tests — the codebase is well-tested at the unit level. However, several fully-built modules remain standalone: they have clean APIs and solid unit tests but are not wired into the main `MemoryStore` lifecycle or exposed to callers.

Specifically:

- **`doc_validation.py`** (Epic 62, 394 lines) — TF-IDF claim extraction and similarity scoring against authoritative docs. Designed to plug into a `LookupEngineLike` provider but that integration is never invoked from the store or retrieval layer.
- **`session_index.py`** (Epic 65.10) — FTS5 session search works in isolation but is never called during normal store operations. At 90% coverage it's the lowest-covered module; real usage would surface integration issues.
- **`extraction.py`** (Epic 65.5) — Pattern-based durable-fact extraction is ready but nothing in the store or ingestion path calls `extract_durable_facts()` automatically.
- **`reinforcement.py`** (Epic 24.2) — `reinforce()` returns an update dict but there's no store-level method to apply it. Callers must manually patch fields.
- **`federation.py`** (Epic 64) — Cross-project sharing works but lacks documentation, CLI discoverability, and expanded integration tests for multi-project scenarios.

Additionally, there are **no performance benchmarks**. The 500-entry cap and lazy decay suggest resource awareness, but without a benchmarking suite there's no way to detect regressions or validate performance characteristics.

This epic wires these modules into the runtime, adds a lightweight benchmark suite, and ensures the integrations are tested end-to-end.

## Results

- **839 tests passing** (up from 792), 5 skipped, ~35s runtime
- **97.17% overall coverage** (up from 96.59%)
- 6 new `MemoryStore` methods: `validate_entries()`, `reinforce()`, `ingest_context()`, `index_session()`, `search_sessions()`, `cleanup_sessions()`
- 5 new integration test files with 62 tests
- 7-benchmark performance suite via `pytest-benchmark`
- Federation usage guide at `docs/guides/federation.md`

## Success Criteria

- [x] `MemoryStore` exposes a `validate_entries()` method that runs doc validation when a lookup engine is configured
- [x] `MemoryStore` exposes a `reinforce()` method that applies reinforcement updates atomically
- [x] `extract_durable_facts()` is callable from a store-level `ingest_context()` method
- [x] Session indexing is triggered on store operations that produce session-relevant data
- [x] Federation has a usage guide and at least one multi-project integration test
- [x] A benchmark suite exists covering store CRUD, retrieval, BM25, and decay at 500 entries
- [x] All new integration paths have end-to-end tests (not mocked)
- [x] Overall coverage stays at 95%+

## Stories

### STORY-002.1: Wire doc validation into the store lifecycle

**Status:** done
**Effort:** L
**Depends on:** none
**Context refs:** `src/tapps_brain/doc_validation.py`, `src/tapps_brain/store.py`, `src/tapps_brain/_protocols.py`
**Verification:** `pytest tests/integration/test_doc_validation_integration.py -v --cov=tapps_brain.doc_validation --cov=tapps_brain.store --cov-report=term-missing`

#### Why

`MemoryDocValidator` is fully implemented but never invoked from the store. Callers would need to manually instantiate the validator, extract claims, score them, and apply results — a multi-step process that should be a single store method. Without this wiring, doc validation is dead code in production.

#### Acceptance Criteria

- [x] `MemoryStore` accepts an optional `lookup_engine: LookupEngineLike` parameter at construction
- [x] `MemoryStore.validate_entries(keys=None)` method validates specified entries (or all) against the configured lookup engine
- [x] Validation results (alignment level, confidence adjustments) are persisted back to the store atomically
- [x] When no lookup engine is configured, `validate_entries()` is a no-op (returns empty report)
- [x] Integration test with a stub `LookupEngineLike` that returns canned docs — verifies the full claim-extraction → scoring → persistence round-trip
- [x] Entries with low alignment scores have their confidence reduced; high-alignment entries are boosted

---

### STORY-002.2: Add store-level reinforcement method

**Status:** done
**Effort:** S
**Depends on:** none
**Context refs:** `src/tapps_brain/reinforcement.py`, `src/tapps_brain/store.py`, `src/tapps_brain/decay.py`
**Verification:** `pytest tests/unit/test_memory_store.py::TestStoreReinforcement tests/integration/test_reinforcement_integration.py -v --cov=tapps_brain.reinforcement --cov=tapps_brain.store --cov-report=term-missing`

#### Why

`reinforce()` returns an update dict but there's no atomic way to apply it through the store. Callers must manually read the entry, call `reinforce()`, and update individual fields — error-prone and not thread-safe. A store-level method makes reinforcement a single atomic operation.

#### Acceptance Criteria

- [x] `MemoryStore.reinforce(key, confidence_boost=0.0)` method that: retrieves the entry, calls `reinforce()`, applies the returned updates atomically (in-memory dict + SQLite)
- [x] Returns the updated `MemoryEntry` (or raises `KeyError` if not found)
- [x] Thread-safety: reinforcement acquires the store lock
- [x] Integration test: reinforce an entry, verify `last_reinforced`, `reinforce_count`, and `confidence` are updated in both memory and SQLite
- [x] Reinforcing a non-existent key raises `KeyError`

---

### STORY-002.3: Wire extraction into a store-level ingest method

**Status:** done
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/extraction.py`, `src/tapps_brain/store.py`, `src/tapps_brain/models.py`
**Verification:** `pytest tests/integration/test_extraction_integration.py -v --cov=tapps_brain.extraction --cov=tapps_brain.store --cov-report=term-missing`

#### Why

`extract_durable_facts()` can pull decision-like statements from session context, but nothing calls it automatically. Manually calling extraction, converting results to `MemoryEntry` objects, and saving them is boilerplate that every consumer would repeat. A store-level method closes this gap.

#### Acceptance Criteria

- [x] `MemoryStore.ingest_context(context, source="agent", capture_prompt="")` method that: calls `extract_durable_facts()`, converts each fact to a `MemoryEntry` with the appropriate tier, and saves new entries to the store
- [x] Deduplication: if an entry with the same key already exists, skip it (do not overwrite)
- [x] Returns a list of keys for newly created entries
- [x] Integration test: ingest a block of text containing decision patterns ("we decided to use PostgreSQL", "key decision: switch to REST"), verify entries are created with correct tiers and values
- [x] Integration test: ingest the same text twice, verify no duplicates are created

---

### STORY-002.4: Integrate session indexing with the store

**Status:** done
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/session_index.py`, `src/tapps_brain/store.py`, `src/tapps_brain/persistence.py`
**Verification:** `pytest tests/integration/test_session_index_integration.py -v --cov=tapps_brain.session_index --cov=tapps_brain.store --cov-report=term-missing`

#### Why

`session_index.py` provides FTS5 search over past sessions but is called in isolation. There's no store-level method to index a session or search across sessions alongside memory entries. This makes the feature invisible to callers who only interact with `MemoryStore`.

#### Acceptance Criteria

- [x] `MemoryStore.index_session(session_id, chunks)` delegates to `session_index.index_session()` using the store's project root
- [x] `MemoryStore.search_sessions(query, limit=10)` delegates to `session_index.search_session_index()`
- [x] `MemoryStore.cleanup_sessions(ttl_days=90)` delegates to `session_index.delete_expired_sessions()`
- [x] Integration test: index 3 sessions with distinct content, search for a term present in only one, verify only that session is returned
- [x] Integration test: index sessions, advance time, run cleanup, verify expired sessions are removed
- [x] Session index errors (corrupt DB, disk full) are caught and logged, not propagated — session indexing is best-effort

---

### STORY-002.5: Federation usage guide and multi-project integration tests

**Status:** done
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/federation.py`, `tests/unit/test_federation.py`
**Verification:** `pytest tests/integration/test_federation_integration.py -v --cov=tapps_brain.federation --cov-report=term-missing`

#### Why

Federation is the most complex module (24.5 KB) but has no usage guide and only unit tests. Real-world usage involves multiple projects publishing, subscribing, syncing, and searching — a multi-step workflow that unit tests don't exercise. Without integration tests, subtle bugs in the sync/conflict-resolution logic could go unnoticed.

#### Acceptance Criteria

- [x] `docs/guides/federation.md` created: explains hub-and-spoke model, registration, publishing, subscribing, syncing, and federated search with code examples
- [x] Integration test: 2 projects (A and B) each with their own `MemoryStore`, both registered with a shared hub
- [x] Integration test: Project A publishes entries, Project B syncs from hub, verifies entries are received with correct metadata
- [x] Integration test: Both projects publish entries with the same key — verify local-wins conflict resolution
- [x] Integration test: `federated_search()` combines local and hub results, local entries get the 1.2x boost
- [x] Integration test: Subscription filters (tags, min_confidence) are respected during sync

---

### STORY-002.6: Add a performance benchmark suite

**Status:** done
**Effort:** L
**Depends on:** STORY-002.1, STORY-002.2, STORY-002.3, STORY-002.4
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/retrieval.py`, `src/tapps_brain/bm25.py`, `src/tapps_brain/decay.py`
**Verification:** `pytest tests/benchmarks/ -v --benchmark-only`

#### Why

The system has a 500-entry cap and uses lazy decay, BM25 scoring, and FTS5 search — all with performance implications. Without benchmarks, there's no way to detect regressions or validate that operations stay within acceptable latency bounds. This is especially important now that Stories 002.1–4 add new store-level methods.

#### Acceptance Criteria

- [x] `tests/benchmarks/` directory with `conftest.py` providing a pre-populated store fixture (500 entries, mixed tiers)
- [x] Benchmark: `store.save()` — 500 sequential writes, report p50/p95/p99 latency
- [x] Benchmark: `store.get()` — 1000 random reads from a full store
- [x] Benchmark: `retriever.search()` — 100 queries against a 500-entry store with BM25 + FTS5
- [x] Benchmark: `decay.calculate_decayed_confidence()` — 10,000 calls with varied ages and tiers
- [x] Benchmark: `store.reinforce()` — 500 reinforcements (from STORY-002.2)
- [x] All benchmarks use `pytest-benchmark` (add to dev dependencies if not present)
- [x] Benchmark results are printed to stdout (no external service dependency)
- [x] Add a CI job or marker so benchmarks run separately from the main test suite (`-m benchmark` or `--benchmark-only`)

---

### STORY-002.7: Integration sweep — verify coverage stays at 95%+

**Status:** done
**Effort:** M
**Depends on:** STORY-002.1, STORY-002.2, STORY-002.3, STORY-002.4, STORY-002.5, STORY-002.6
**Context refs:** `pyproject.toml`
**Verification:** `pytest tests/ -v --tb=short --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95`

#### Why

Stories 002.1–002.5 add new code paths in `store.py` and new integration tests. This final story verifies nothing fell through the cracks: no new methods are untested, no existing coverage regressed, and the CI floor holds.

#### Acceptance Criteria

- [x] All new `MemoryStore` methods (`validate_entries`, `reinforce`, `ingest_context`, `index_session`, `search_sessions`, `cleanup_sessions`) have both unit and integration test coverage
- [x] No source module below 80% line coverage
- [x] Overall coverage at 95%+
- [x] `session_index.py` coverage improved from 90% (currently the lowest)
- [x] CI passes on all matrix targets (Ubuntu/macOS/Windows × Python 3.12/3.13)
- [x] No new `ResourceWarning` or deprecation warnings introduced

## Priority Order

| Order | Story | Effort | Impact |
|-------|-------|--------|--------|
| 1 | STORY-002.2 — Store reinforcement | S | Smallest change, unblocks reinforcement usage |
| 2 | STORY-002.3 — Extraction ingestion | M | Enables automatic fact capture from sessions |
| 3 | STORY-002.4 — Session index integration | M | Makes session search discoverable via the store |
| 4 | STORY-002.1 — Doc validation wiring | L | Most complex integration; requires lookup engine plumbing |
| 5 | STORY-002.5 — Federation guide + tests | M | Documentation + integration tests for the most complex module |
| 6 | STORY-002.6 — Benchmark suite | L | Depends on new store methods from 002.1–4 |
| 7 | STORY-002.7 — Coverage sweep | M | Final gate — depends on all prior stories |

## Dependency Graph

```
002.2 (reinforcement) ──┐
002.3 (extraction)   ───┤
002.4 (session index) ──┼──→ 002.6 (benchmarks) ──→ 002.7 (sweep)
002.1 (doc validation) ─┤
002.5 (federation)   ───┘
```

Stories 002.1–002.5 are independent of each other and can be worked in parallel. Story 002.6 depends on 002.1–4 (benchmarks the new store methods). Story 002.7 depends on all prior stories.
