# Story 78.2 — Optimize snapshot aggregates: eliminate full list_all scan

<!-- docsmcp:start:user-story -->
> **As a** operator with a large memory store, **I want** `/snapshot` to build from SQL aggregates, **so that** the dashboard loads within nginx timeout even with thousands of entries.
<!-- docsmcp:end:user-story -->

**Points:** 8 | **Epic:** EPIC-078 | **Priority:** P0

## What

Replace `store.list_all()` in `build_visual_snapshot()` with targeted Postgres aggregate queries for tier distribution, agent_scope counts, access histogram, tag stats, and memory groups.

## Where

- `src/tapps_brain/visual_snapshot.py:879-1006` — `build_visual_snapshot()`
- `src/tapps_brain/postgres_private.py` — new aggregate query helpers
- `tests/unit/test_visual_snapshot.py` — golden fixture parity

## Why

`build_visual_snapshot()` currently calls `store.list_all()` which loads every `MemoryEntry` into Python on each cache miss. On a warm store with MCP traffic this exceeds nginx's **10s** `proxy_read_timeout`, producing **504** at `:8088/snapshot`.

## Tasks

- [ ] Add `PostgresPrivateBackend.snapshot_aggregates(project_id) -> SnapshotAggregates` returning tier counts, scope counts, access buckets, tag top-N, memory group counts without full row hydration
- [ ] Refactor `_access_stats_from_entries`, `_agent_scope_counts`, `_memory_group_stats`, `_tag_stats_local` to consume aggregates when backend supports them; fall back to list_all for in-memory test stores
- [ ] Add benchmark test: 5_000-entry fixture builds snapshot in **<3s** cold (no network)
- [ ] Bump `identity_schema_version` if aggregate inputs change fingerprint inputs; document in `visual-snapshot.md`

## Acceptance Criteria

- [ ] Cold snapshot build on 5_000-entry Postgres fixture completes in **<3s** (pytest, not benchmark gate)
- [ ] Snapshot JSON matches prior golden fixture for a 100-entry store (field parity except `generated_at`)
- [ ] No raw memory `value` text in snapshot JSON (privacy invariant preserved)
- [ ] `list_all()` is not called on Postgres backend snapshot path (assert via mock/spy)

## Test Cases

1. Empty store → zero counts, valid scorecard
2. 100-entry golden fixture → byte-stable aggregate fields
3. 5000-entry stress fixture → completes under 3s
