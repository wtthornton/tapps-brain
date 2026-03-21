---
id: EPIC-006
title: "Persistent knowledge graph and semantic queries"
status: done
priority: high
created: 2026-03-19
target_date: 2026-05-15
tags: [relations, graph, persistence, retrieval]
---

# EPIC-006: Persistent Knowledge Graph and Semantic Queries

## Context

tapps-brain has a `relations.py` module that extracts entity relations (subject-predicate-object triples) and a `retrieval.py` query expansion path that traverses these relations up to 2 hops. However, **relations are ephemeral** — they exist only in memory during a session and are lost when the store closes. There is no persistence layer for relations in SQLite.

This means:

- Relations extracted in one session are gone in the next
- The graph cannot be queried directly ("what uses PostgreSQL?", "what depends on auth?")
- Federation cannot share or traverse relations across projects
- Auto-recall (EPIC-003) cannot boost memories connected via the knowledge graph
- Consolidation doesn't consider graph relationships when merging entries

The `relations.py` module already defines `RelationEntry(subject, predicate, object, source_key, confidence)` and `extract_relations()` / `expand_via_relations()`. What's missing is SQLite persistence, a query API, and integration with recall and consolidation.

## Success Criteria

- [ ] Relations persisted in SQLite (new `relations` table in schema v6)
- [ ] Relations automatically extracted and stored on `save()` and `ingest_context()`
- [ ] `store.find_related(key, max_hops=2)` returns entries connected via the graph
- [ ] `store.query_relations(subject=..., predicate=..., object=...)` for direct graph queries
- [ ] Auto-recall scoring boosts memories connected to the query via the knowledge graph
- [ ] Consolidation merges relation sets when entries are consolidated
- [ ] Supersession (EPIC-004) transfers relations from old entry to new entry
- [ ] Relations survive store close/reopen (persistence round-trip)
- [ ] Overall coverage stays at 95%+

## Stories

### STORY-006.1: Schema v6 — relations table

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/persistence.py`, `src/tapps_brain/relations.py`
**Verification:** `pytest tests/unit/test_persistence.py -v --cov=tapps_brain.persistence --cov-report=term-missing`

#### Why

Relations need a home in SQLite before anything else can be built. The schema migration must be backward-compatible — stores without relations continue to work.

#### Acceptance Criteria

- [ ] Schema migration v5→v6 adds `relations` table: `id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL, confidence REAL DEFAULT 1.0, created_at TEXT NOT NULL, FOREIGN KEY(source_key) REFERENCES memories(key)`
- [ ] Index: `CREATE INDEX idx_relations_source ON relations(source_key)`
- [ ] Index: `CREATE INDEX idx_relations_subject ON relations(subject)`
- [ ] Index: `CREATE INDEX idx_relations_object ON relations(object)`
- [ ] `save_relations(key, relations: list[RelationEntry])` and `load_relations(key) -> list[RelationEntry]` in persistence
- [ ] `delete_relations(key)` for cleanup when entries are removed
- [ ] Migration is idempotent — running twice produces the same result
- [ ] Existing stores (v5) auto-migrate on open
- [ ] All existing tests pass without modification

---

### STORY-006.2: Auto-extract and persist relations on save

**Status:** planned
**Effort:** M
**Depends on:** STORY-006.1
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/relations.py`
**Verification:** `pytest tests/unit/test_memory_store.py::TestRelationsPersistence -v`

#### Why

Relations should be extracted automatically when entries are saved, not only when explicitly requested. This makes the knowledge graph grow organically as the store is used.

#### Acceptance Criteria

- [ ] `MemoryStore.save()` calls `extract_relations()` on the saved entry and persists results
- [ ] `MemoryStore.ingest_context()` extracts and persists relations for each created entry
- [ ] Relations are loaded from SQLite on store open (cold-start)
- [ ] `MemoryStore.get_relations(key) -> list[RelationEntry]` convenience method
- [ ] Unit test: save an entry mentioning "project uses PostgreSQL", verify relation is persisted
- [ ] Unit test: close and reopen store, verify relations survive

---

### STORY-006.3: Graph query API

**Status:** planned
**Effort:** L
**Depends on:** STORY-006.2
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/relations.py`
**Verification:** `pytest tests/unit/test_memory_store.py::TestGraphQueries -v`

#### Why

Direct graph queries let callers ask "what uses X?" or "what does Y depend on?" without searching entry text. This is a fundamentally different retrieval path from FTS5 — it follows relationships, not keywords.

#### Acceptance Criteria

- [ ] `store.find_related(key, max_hops=2) -> list[MemoryEntry]` — returns entries connected via relation graph, up to N hops
- [ ] `store.query_relations(subject=None, predicate=None, object=None) -> list[RelationEntry]` — filter relations by any combination of fields
- [ ] Results are deduplicated and ordered by hop distance (closest first)
- [ ] Temporal filtering: superseded entries excluded by default (consistent with EPIC-004)
- [ ] Unit test: create A→B→C relation chain, `find_related("A", max_hops=2)` returns [B, C]
- [ ] Unit test: `query_relations(predicate="uses")` returns all "uses" relations

---

### STORY-006.4: Recall scoring boost via knowledge graph

**Status:** planned
**Effort:** M
**Depends on:** STORY-006.3
**Context refs:** `src/tapps_brain/recall.py`, `src/tapps_brain/retrieval.py`
**Verification:** `pytest tests/unit/test_recall.py::TestGraphBoost -v`

#### Why

EPIC-003's auto-recall returns memories based on text similarity. If the knowledge graph connects a memory to the query topic (even without keyword overlap), it should receive a scoring boost. This makes recall more semantically aware without requiring an LLM.

#### Acceptance Criteria

- [ ] `RecallConfig` gains `use_graph_boost: bool = True` and `graph_boost_factor: float = 0.15`
- [ ] When enabled, recall checks if any result entries are graph-connected to entities mentioned in the query
- [ ] Connected entries receive a configurable score boost (default +0.15)
- [ ] Boost is applied after primary scoring, before token budget truncation
- [ ] Unit test: entry not matching query text but graph-connected to query entity gets boosted into results

---

### STORY-006.5: Relation transfer on supersede and consolidation

**Status:** planned
**Effort:** M
**Depends on:** STORY-006.2
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/consolidation.py`
**Verification:** `pytest tests/unit/test_consolidation.py::TestRelationTransfer -v`

#### Why

When entries are superseded or consolidated, their relations should transfer to the new/merged entry. Otherwise the knowledge graph fragments as facts evolve.

#### Acceptance Criteria

- [ ] `store.supersede()` copies relations from old entry to new entry (with updated `source_key`)
- [ ] Consolidation merges relation sets from all source entries into the consolidated entry
- [ ] Duplicate relations (same subject-predicate-object) are deduplicated during transfer
- [ ] Old entry's relations are preserved (for history queries) but marked with the old key
- [ ] Unit test: supersede entry with 3 relations, verify new entry inherits them
- [ ] Unit test: consolidate 3 entries with overlapping relations, verify merged set

---

### STORY-006.6: Integration tests — persistent graph lifecycle

**Status:** planned
**Effort:** M
**Depends on:** STORY-006.3, STORY-006.4, STORY-006.5
**Context refs:** `src/tapps_brain/store.py`
**Verification:** `pytest tests/integration/test_graph_integration.py -v`

#### Why

Individual stories validate components; this validates the full lifecycle with real SQLite — extraction, persistence, query, supersession transfer, and recall boost.

#### Acceptance Criteria

- [ ] Integration test: save 10 entries with various relationships, close/reopen store, verify all relations survived
- [ ] Integration test: `find_related()` traverses 2 hops through persisted relations
- [ ] Integration test: supersede an entry, verify new entry's relations include transferred set
- [ ] Integration test: recall with graph boost ranks connected entry higher than text-only match
- [ ] All tests use real `MemoryStore` + SQLite (no mocks)

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-006.1 — Schema v6 | M | Foundation: SQLite table for relations |
| 2 | STORY-006.2 — Auto-extract + persist | M | Populate the graph automatically |
| 3 | STORY-006.3 — Graph query API | L | Core retrieval path |
| 4 | STORY-006.4 — Recall graph boost | M | Integrate with EPIC-003 |
| 5 | STORY-006.5 — Transfer on supersede | M | Integrate with EPIC-004 |
| 6 | STORY-006.6 — Integration tests | M | Full lifecycle validation |

## Dependency Graph

```
006.1 (schema) ──→ 006.2 (auto-extract) ──┬──→ 006.3 (query API) ──┬──→ 006.6 (integration)
                                           │                        │
                                           └──→ 006.5 (transfer) ───┘
                                                                    │
                                           006.4 (recall boost) ────┘
```

006.4 depends on 006.3. Stories 006.3 and 006.5 can run in parallel after 006.2.
