# Story 78.11 — Complete EPIC-065.5: Agent registry live table

**Points:** 5 | **Epic:** EPIC-078 | **Priority:** P1

## What

Render `agent_registry[]` from snapshot as live table with agent_id, namespace, scope, registered_at, last_write_at on `#agents` page; wire topology SVG to same data.

## Where

- `examples/brain-visual/index.html` — agent registry table + topology (`renderAgentTopology`)
- `src/tapps_brain/visual_snapshot.py` — `_collect_agent_registry`

## Acceptance Criteria (from EPIC-065.5)

- [x] Table lists all agents from snapshot (cap 50 with truncation indicator matching topology)
- [x] Last-write column shows relative time (e.g. "3h ago")
- [x] Empty registry + Hive connected → explanatory note (existing topology behavior)
- [x] EPIC-065.5 marked complete
