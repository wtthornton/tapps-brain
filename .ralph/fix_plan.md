# Ralph Fix Plan — tapps-brain

Aligned with the repo as of **2026-03-20**. For full story text, see `docs/planning/epics/EPIC-*.md`.

**Task sizing:** Each item is scoped to ONE Ralph loop (~15 min). Do one, check it off, commit.

## Completed Epics

- [x] EPIC-001: Test suite quality — A+ (done)
- [x] EPIC-002: Integration wiring (done)
- [x] EPIC-003: Auto-recall orchestrator (done)
- [x] EPIC-004: Bi-temporal fact versioning (done)
- [x] EPIC-005: CLI tool (done)
- [x] EPIC-008: MCP Server (done)

## High Priority

### EPIC-006: Knowledge Graph (High)

**Already in tree:** `relations` table in SQLite, `Persistence.save_relation`/`list_relations`, `relations.py` extraction, `RetrievalEngine` query expansion. **Not wired:** MemoryStore doesn't persist relations on save/ingest; no `find_related`/`query_relations`; no recall boost; no relation transfer on supersede/consolidation.

#### 006-A: Persistence layer for relations
- [x] Verify/add `save_relations(key, relations)`, `load_relations(key)`, `delete_relations(key)` in `persistence.py`. Add unit tests in `test_memory_persistence.py`. Commit: `feat(story-006.1): relation persistence methods`

#### 006-B: Wire relations into MemoryStore save/ingest
- [ ] In `store.py`, call `extract_relations()` from `save()` and `ingest_context()`, persist via `self._persistence.save_relations()`. Add `get_relations(key)` convenience method. Add unit tests. Commit: `feat(story-006.2): auto-extract relations on save/ingest`

#### 006-C: Load relations on store init (cold start)
- [ ] On `MemoryStore.__init__`, load all persisted relations into memory. Add test for close/reopen round-trip. Commit: `feat(story-006.2): load relations on cold start`

#### 006-D: Graph query API — find_related
- [ ] Add `find_related(key, max_hops=2)` to `store.py` — BFS traversal of relation graph, dedup by key, order by hop distance. Add unit tests with A→B→C chain. Commit: `feat(story-006.3): find_related graph traversal`

#### 006-E: Graph query API — query_relations
- [ ] Add `query_relations(subject=None, predicate=None, object_entity=None)` to `store.py` — filter relations by field. Add unit tests. Commit: `feat(story-006.3): query_relations filter API`

#### 006-F: Recall scoring boost via graph
- [ ] Add `use_graph_boost: bool` and `graph_boost_factor: float` to `RecallConfig`. In recall logic, extract query entities, call `find_related()`, boost connected entries' scores. Add unit tests. Commit: `feat(story-006.4): graph-based recall boost`

#### 006-G: Relation transfer on supersede
- [ ] In `store.supersede()`, copy relations from old key to new key via `get_relations()` + `save_relations()`. Add unit tests. Commit: `feat(story-006.5): transfer relations on supersede`

#### 006-H: Relation transfer on consolidation
- [ ] In `consolidation.py`, merge relations from all source entries, deduplicate same subject-predicate-object triples, persist on consolidated entry. Add unit tests. Commit: `feat(story-006.5): merge relations on consolidation`

#### 006-I: Graph lifecycle integration tests
- [ ] Create `tests/integration/test_graph_integration.py` — save entries with relations, close/reopen store, verify find_related traversal, supersede transfer, recall boost ranking. All on real SQLite. Commit: `test(story-006.6): graph lifecycle integration tests`

### EPIC-009: Multi-Interface Distribution (High)

**Current:** `typer` is a core dep (should be optional), no `__all__`, no `py.typed`, no MCP registry manifest.

#### 009-A: Library API surface cleanup
- [ ] Add explicit `__all__` to `__init__.py` with all public symbols organized by group. Create empty `src/tapps_brain/py.typed` marker. Add test verifying all `__all__` symbols are importable. Commit: `feat(story-009.2): curated __all__ and py.typed`

#### 009-B: Dependency extras reorganization
- [ ] In `pyproject.toml`, move `typer` to `[cli]` extra, create `[all]` extra combining cli+mcp+vector+reranker. Add graceful `ImportError` messages in `cli.py` and `mcp_server.py` when extras missing. Add unit tests for graceful errors. Commit: `feat(story-009.1): optional extras for cli and mcp`

#### 009-C: Entry points and version unification
- [ ] Declare `tapps-brain` CLI entry point in `pyproject.toml`. Replace hardcoded `__version__` with `importlib.metadata.version()`. Verify CLI `--version` and MCP server version match. Add unit tests. Commit: `feat(story-009.3): entry points and unified version`

#### 009-D: MCP Registry manifest
- [ ] Create `server.json` following MCP Registry schema. Commit: `feat(story-009.4): MCP registry server.json`

#### 009-E: Distribution integration tests
- [ ] Add pytest markers `requires_cli` / `requires_mcp` to relevant test files. Verify core import works without extras. Commit: `test(story-009.5): extras-aware test markers`

## Medium Priority

### EPIC-007: Observability (Medium)

**Already in tree:** `metrics.py` (MetricsCollector, snapshots, health), `audit.py` (AuditReader), `store.health()`, `store.get_metrics()`. **Not wired:** counters/histograms not populated from store operations. No OTel exporter.

#### 007-A: Instrument save/get/search paths
- [ ] In `store.py`, add `self._metrics.increment()` and `self._metrics.observe()` calls to `save()`, `get()`, and `search()`. Add unit tests verifying counters increment. Commit: `feat(story-007.2): instrument save/get/search metrics`

#### 007-B: Instrument recall/supersede/consolidate/GC paths
- [ ] In `store.py`, add metrics to `recall()`, `supersede()`, consolidate, and `gc()`. Add unit tests. Commit: `feat(story-007.2): instrument lifecycle operation metrics`

#### 007-C: Expose store.audit() convenience
- [ ] Add `store.audit(**kwargs)` method delegating to `AuditReader`. Add unit tests. If already exists, verify and add missing test coverage. Commit: `feat(story-007.3): store.audit() convenience method`

#### 007-D: OpenTelemetry exporter
- [ ] Create `otel_exporter.py` with `OTelExporter` class, add `otel` optional dep to `pyproject.toml`, add feature flag in `_feature_flags.py`. Add unit tests with mocked OTel SDK. Commit: `feat(story-007.5): optional OpenTelemetry exporter`

#### 007-E: Observability integration tests
- [ ] Create `tests/integration/test_observability_integration.py` — perform 50 mixed operations, verify metrics snapshot, query audit trail, verify health report. Real SQLite. Commit: `test(story-007.6): observability integration tests`

## Notes

- **One task per loop.** Each task is sized for ~15 min. If a task is too large, split it and check off the part you finished.
- **EPIC-006** — Do not assume "greenfield"; persistence and retrieval expansion exist — wire store lifecycle and graph APIs next.
- **EPIC-009** — 009-A (API cleanup) should come before 009-B (extras split) since __all__ defines the public surface.
- Always cross-check **`docs/planning/epics/`** before starting a task.
- Maintain **95%** test coverage; run full lint / type / test suite before committing.
- After completing a task, update this file: change `- [ ]` to `- [x]`.
