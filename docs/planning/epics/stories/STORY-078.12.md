# Story 78.12 — Complete EPIC-065.6: Memory velocity panel wiring

**Points:** 5 | **Epic:** EPIC-078 | **Priority:** P1

## What

Surface `velocity` block (writes/recalls 1h and 24h) on `#memory` page with spark-style KPI tiles; ensure backend `_collect_velocity` populates from Postgres metrics.

## Where

- `src/tapps_brain/visual_snapshot.py` — `_collect_velocity`, `MemoryVelocity` model
- `examples/brain-visual/index.html` — `#memory` velocity section

## Acceptance Criteria (from EPIC-065.6)

- [x] Snapshot includes non-zero velocity when store has recent activity (integration test)
- [x] Dashboard shows writes_1h, writes_24h, recalls_1h, recalls_24h tiles
- [x] Zero activity → "No recent activity" not fabricated numbers
- [x] EPIC-065.6 marked complete
