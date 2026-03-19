---
id: EPIC-004
title: "Bi-temporal fact versioning with validity windows"
status: planned
priority: high
created: 2026-03-19
target_date: 2026-04-30
tags: [temporal, versioning, schema, retrieval, persistence]
---

# EPIC-004: Bi-Temporal Fact Versioning with Validity Windows

## Context

tapps-brain currently tracks three timestamps per memory: `created_at` (when stored), `updated_at` (when last modified), and `last_accessed` (when last read). This is **uni-temporal with access tracking** — it knows *when a fact was recorded* but not *when a fact was true*.

In production, facts change:
- "Our pricing is $297/mo" → superseded by "Our pricing is $397/mo"
- "We use PostgreSQL 15" → replaced by "We use PostgreSQL 17"
- "Release freeze starts March 5" → expires after March 5

Today, superseded facts are either deleted or marked `contradicted=True` with no temporal query capability. There's no way to ask "what was our pricing last month?" or "what was true on March 1st?"

Zep's Graphiti engine (24k GitHub stars in 2026, outpacing its parent project) proves massive demand for temporal fact management. Their bi-temporal model with `valid_at`/`invalid_at` windows is the gold standard. No deterministic (LLM-free) memory system implements this — making it a unique differentiator for tapps-brain.

**Bi-temporal means two time axes:**
1. **System time** (already tracked): when the fact was recorded in the store (`created_at`, `updated_at`)
2. **Valid time** (new): when the fact is/was actually true in the real world (`valid_at`, `invalid_at`)

This enables:
- **Point-in-time queries**: "What was true on 2026-03-01?"
- **Supersession without deletion**: old facts are invalidated, not removed — history is preserved
- **Scheduled facts**: `valid_at` in the future for upcoming changes
- **Automatic expiry**: `invalid_at` for time-bounded facts (release freezes, sprint goals, etc.)

## Success Criteria

- [ ] `MemoryEntry` has `valid_at` and `invalid_at` optional timestamp fields
- [ ] SQLite schema migrated to v5 with both columns + index
- [ ] `MemoryStore.supersede(old_key, new_key_or_entry)` atomically invalidates the old entry and links it to the new one
- [ ] `MemoryRetriever.search()` filters out temporally invalid entries by default (with opt-in override)
- [ ] `MemoryStore.search()` respects validity windows
- [ ] Point-in-time query: `store.search(query, as_of="2026-03-01T00:00:00Z")` returns only facts valid at that timestamp
- [ ] `store.history(key)` returns the full temporal chain for a key (all versions, including superseded)
- [ ] Existing entries with `contradicted=True` + `contradiction_reason` containing "consolidated into" can be migrated to use `invalid_at`
- [ ] All existing tests pass without modification (backward compatibility)
- [ ] Overall coverage stays at 95%+

## Stories

### STORY-004.1: Add valid_at / invalid_at fields to MemoryEntry

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/models.py`, `src/tapps_brain/persistence.py`
**Verification:** `pytest tests/unit/test_models.py tests/unit/test_persistence.py -v --cov=tapps_brain.models --cov=tapps_brain.persistence --cov-report=term-missing`

#### Why

The data model is the foundation. Adding optional temporal fields to `MemoryEntry` and migrating the SQLite schema must happen first — everything else builds on this.

#### Acceptance Criteria

- [ ] `MemoryEntry` gains two optional fields: `valid_at: str | None = None` (ISO-8601 UTC), `invalid_at: str | None = None` (ISO-8601 UTC)
- [ ] Computed property `is_temporally_valid(as_of: str | None = None) -> bool`: returns True if `as_of` (or now) falls within `[valid_at, invalid_at)`. Both None = always valid.
- [ ] Computed property `is_superseded -> bool`: returns True if `invalid_at` is not None and is in the past
- [ ] `superseded_by: str | None = None` field — key of the entry that replaced this one (nullable)
- [ ] Pydantic validator: `invalid_at` must be after `valid_at` if both are set
- [ ] Schema migration v4→v5: `ALTER TABLE memories ADD COLUMN valid_at TEXT`, `ALTER TABLE memories ADD COLUMN invalid_at TEXT`, `ALTER TABLE memories ADD COLUMN superseded_by TEXT`
- [ ] Index: `CREATE INDEX idx_memories_temporal ON memories(valid_at, invalid_at)`
- [ ] Existing entries with NULL `valid_at`/`invalid_at` are treated as always-valid (backward compatible)
- [ ] `_entry_to_row()` and `_row_to_entry()` in persistence handle the new fields
- [ ] All existing tests pass without modification

---

### STORY-004.2: Implement supersede() on MemoryStore

**Status:** planned
**Effort:** M
**Depends on:** STORY-004.1
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/persistence.py`
**Verification:** `pytest tests/unit/test_memory_store.py::TestSupersede -v --cov=tapps_brain.store --cov-report=term-missing`

#### Why

Supersession is the primary write operation for bi-temporal data. It atomically marks an old fact as invalid and links it to its replacement. Without this, callers would need to manually set `invalid_at` and `superseded_by` — error-prone and not thread-safe.

#### Acceptance Criteria

- [ ] `MemoryStore.supersede(old_key: str, new_value: str, **kwargs) -> MemoryEntry` method that:
  - Sets `invalid_at = now()` on the old entry
  - Sets `superseded_by = new_key` on the old entry
  - Creates a new entry (via `save()`) with the new value and `valid_at = now()`
  - Returns the new entry
- [ ] If `old_key` does not exist, raises `KeyError`
- [ ] If `old_key` is already superseded (`invalid_at` is set), raises `ValueError` with descriptive message
- [ ] Both operations (invalidation + creation) happen under the store lock (atomic)
- [ ] Both operations are persisted to SQLite in a single transaction
- [ ] Audit log records both the invalidation and the new entry
- [ ] Unit test: supersede a fact, verify old entry has `invalid_at` and `superseded_by`, new entry has `valid_at`
- [ ] Unit test: supersede an already-superseded entry raises `ValueError`
- [ ] Unit test: supersede a non-existent key raises `KeyError`

---

### STORY-004.3: Temporal filtering in retrieval

**Status:** planned
**Effort:** M
**Depends on:** STORY-004.1
**Context refs:** `src/tapps_brain/retrieval.py`, `src/tapps_brain/store.py`
**Verification:** `pytest tests/unit/test_retrieval.py tests/unit/test_memory_store.py -v --cov=tapps_brain.retrieval --cov=tapps_brain.store --cov-report=term-missing`

#### Why

Without temporal filtering, superseded facts pollute search results. The retriever and store search must respect validity windows by default, while still allowing historical queries with an explicit `as_of` parameter.

#### Acceptance Criteria

- [ ] `MemoryRetriever.search()` gains optional `as_of: str | None = None` and `include_superseded: bool = False` parameters
- [ ] By default (`as_of=None, include_superseded=False`): filters out entries where `is_temporally_valid(now)` is False
- [ ] When `as_of` is provided: filters using that timestamp instead of now
- [ ] When `include_superseded=True`: returns all entries regardless of temporal validity (but marks them with `stale=True` in `ScoredMemory`)
- [ ] `MemoryStore.search()` gains the same `as_of` parameter, applies temporal filtering after FTS5 results
- [ ] `MemoryStore.list_all()` gains `include_superseded: bool = False` parameter
- [ ] Composite scoring: superseded entries that are included via `include_superseded=True` get a 0.5x penalty to their relevance score
- [ ] Unit test: store 3 versions of a fact (v1 superseded, v2 superseded, v3 current), search returns only v3 by default
- [ ] Unit test: same setup, search with `as_of` timestamp between v1 and v2, returns only v1
- [ ] Unit test: search with `include_superseded=True` returns all 3 versions

---

### STORY-004.4: Point-in-time history queries

**Status:** planned
**Effort:** M
**Depends on:** STORY-004.2
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/persistence.py`
**Verification:** `pytest tests/unit/test_memory_store.py::TestHistory -v --cov=tapps_brain.store --cov-report=term-missing`

#### Why

A key benefit of bi-temporal data is the ability to trace how a fact evolved. `store.history(key)` returns the full chain of versions — essential for understanding why a decision was made or what changed. This is also the feature that audit-conscious teams need.

#### Acceptance Criteria

- [ ] `MemoryStore.history(key: str) -> list[MemoryEntry]` method that returns the full version chain, ordered by `valid_at` ascending
- [ ] Follows the `superseded_by` chain forward from the given key to find all successors
- [ ] Follows the chain backward (entries whose `superseded_by == key`) to find all predecessors
- [ ] Returns all entries in the chain, including the current (non-superseded) version
- [ ] If the key has no history (never superseded, no predecessors), returns a single-element list
- [ ] If the key does not exist, raises `KeyError`
- [ ] Unit test: create a 3-version chain (A → B → C), call `history("A")`, get [A, B, C] ordered by `valid_at`
- [ ] Unit test: call `history("C")` (the current version), get the same [A, B, C]
- [ ] Unit test: call `history("standalone_key")`, get single-element list

---

### STORY-004.5: Migrate contradicted entries to use temporal fields

**Status:** planned
**Effort:** S
**Depends on:** STORY-004.1, STORY-004.2
**Context refs:** `src/tapps_brain/persistence.py`, `src/tapps_brain/consolidation.py`
**Verification:** `pytest tests/unit/test_persistence.py::TestTemporalMigration -v --cov=tapps_brain.persistence --cov-report=term-missing`

#### Why

Existing entries with `contradicted=True` and `contradiction_reason` containing "consolidated into {key}" are logically superseded facts. Migrating them to use `invalid_at` and `superseded_by` unifies the two mechanisms and makes them queryable via the new temporal API.

#### Acceptance Criteria

- [ ] Migration function `migrate_contradicted_to_temporal()` in persistence module
- [ ] For each entry where `contradicted=True` and `contradiction_reason` matches pattern "consolidated into {key}": sets `invalid_at = updated_at` (the time of consolidation) and `superseded_by = {extracted_key}`
- [ ] Migration is idempotent — running it twice produces the same result
- [ ] Migration runs as part of the v5 schema migration (after columns are added)
- [ ] Unit test: create 3 contradicted entries with "consolidated into" reasons, run migration, verify `invalid_at` and `superseded_by` are set correctly
- [ ] Unit test: entries with `contradicted=True` but no "consolidated into" pattern are left unchanged
- [ ] Original `contradicted` and `contradiction_reason` fields are preserved (not cleared) for backward compatibility

---

### STORY-004.6: Integration with auto-consolidation

**Status:** planned
**Effort:** M
**Depends on:** STORY-004.2, STORY-004.3
**Context refs:** `src/tapps_brain/consolidation.py`, `src/tapps_brain/auto_consolidation.py`, `src/tapps_brain/store.py`
**Verification:** `pytest tests/unit/test_consolidation.py tests/unit/test_auto_consolidation.py -v --cov=tapps_brain.consolidation --cov=tapps_brain.auto_consolidation --cov-report=term-missing`

#### Why

Auto-consolidation currently marks source entries as `contradicted=True`. With temporal versioning, it should instead use `supersede()` to properly invalidate source entries and link them to the consolidated result. This makes consolidation fully temporal and queryable via `history()`.

#### Acceptance Criteria

- [ ] `consolidate_entries()` in `consolidation.py` uses `supersede()` (or sets `invalid_at` + `superseded_by`) on source entries when creating a `ConsolidatedEntry`
- [ ] `ConsolidatedEntry.source_ids` entries are temporally invalidated, not just flagged as contradicted
- [ ] Auto-consolidation in `auto_consolidation.py` delegates to the updated consolidation logic
- [ ] `history()` can trace from a source entry to its consolidated result
- [ ] Backward compatibility: `contradicted` flag is still set (for callers that check it) in addition to temporal fields
- [ ] Unit test: trigger auto-consolidation on 3 similar entries, verify all sources have `invalid_at` and `superseded_by` pointing to the consolidated entry
- [ ] Unit test: call `history()` on a source entry, verify the chain includes the consolidated entry

---

### STORY-004.7: Integration tests — full temporal lifecycle

**Status:** planned
**Effort:** M
**Depends on:** STORY-004.2, STORY-004.3, STORY-004.4, STORY-004.6
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/retrieval.py`
**Verification:** `pytest tests/integration/test_temporal_integration.py -v --cov=tapps_brain.store --cov=tapps_brain.retrieval --cov-report=term-missing`

#### Why

Individual stories validate components; this story validates the full lifecycle with a real SQLite-backed store — creation, supersession, temporal search, history queries, and interaction with consolidation.

#### Acceptance Criteria

- [ ] Integration test: Create fact v1, supersede with v2, supersede with v3. Search returns only v3. Search with `as_of` between v1 and v2 returns only v1. `history()` returns [v1, v2, v3].
- [ ] Integration test: Create entry with `valid_at` in the future. Search now returns nothing. Search with `as_of` at that future time returns the entry.
- [ ] Integration test: Create entry with `invalid_at` set to a past time. Search returns nothing. Search with `include_superseded=True` returns it marked stale.
- [ ] Integration test: Auto-consolidation of 3 entries produces a temporal chain queryable via `history()`
- [ ] Integration test: Full round-trip through persistence — supersede, restart store (cold-start from SQLite), verify temporal fields survived
- [ ] Integration test: Recall orchestrator (EPIC-003) excludes superseded entries from auto-recall results
- [ ] All tests use real `MemoryStore` + SQLite (no mocks)

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-004.1 — Model + schema | M | Foundation: everything depends on the new fields |
| 2 | STORY-004.2 — supersede() | M | Primary write operation for temporal data |
| 3 | STORY-004.3 — Temporal filtering | M | Primary read operation; can parallel with 004.2 |
| 4 | STORY-004.4 — History queries | M | Builds on supersede chain; can parallel with 004.3 |
| 5 | STORY-004.5 — Contradicted migration | S | Cleanup; depends on 004.1 + 004.2 |
| 6 | STORY-004.6 — Consolidation integration | M | Wires temporal into existing consolidation |
| 7 | STORY-004.7 — Integration tests | M | Final validation; depends on all prior stories |

## Dependency Graph

```
004.1 (model + schema) ──┬──→ 004.2 (supersede) ──┬──→ 004.4 (history) ──┐
                         │                         │                      │
                         ├──→ 004.3 (filtering) ───┤                      ├──→ 004.7 (integration)
                         │                         │                      │
                         └──→ 004.5 (migration) ───┘──→ 004.6 (consol.) ──┘
```

Stories 004.2, 004.3, and 004.5 can start in parallel once 004.1 is complete. Story 004.4 depends on 004.2. Story 004.6 depends on 004.2 + 004.3. Story 004.7 depends on all prior stories.
