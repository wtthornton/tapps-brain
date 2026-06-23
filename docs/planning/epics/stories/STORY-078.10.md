# Story 78.10 — Complete EPIC-065.4: Hive namespace monitoring table

**Points:** 8 | **Epic:** EPIC-078 | **Priority:** P1

## What

Extend `HiveHealthSummary` with per-namespace entry counts and `last_write_at`; render structured table on `#agents` page replacing comma-separated prose.

## Where

- `src/tapps_brain/visual_snapshot.py` — `_collect_hive_health`, `HiveHealthSummary` model
- `examples/brain-visual/index.html` — Hive hub panel on `#agents`
- `tests/unit/test_visual_snapshot.py`

## Acceptance Criteria (from EPIC-065.4)

- [x] Snapshot JSON includes `hive_health.namespaces[]` with `{name, entry_count, last_write_at}`
- [x] Dashboard renders sortable table (namespace, entries, last write)
- [x] Hive unreachable → clear empty state (not fake zeros)
- [x] EPIC-065.4 marked complete
