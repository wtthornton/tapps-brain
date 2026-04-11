# Story 65.6 -- Memory velocity panel

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** a panel showing how many memories were written and recalled in the last 1 hour and 24 hours, **so that** I can tell whether agents are actively using memory, whether activity is trending up or down, and spot periods of unexpected silence

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that operators can answer the most fundamental operational question about a memory system: is it being used? The current dashboard shows a static entry count and an access histogram with coarse lifetime buckets (0, 1-5, 6-20, 21+), but nothing about recent write or recall rate. An operator cannot tell from the current dashboard whether the system received 200 writes in the last hour or zero writes in the last week. Velocity windows derived from created_at/last_accessed timestamps in Postgres require no new instrumentation and give an accurate picture of recent activity.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Add a MemoryVelocity dataclass to visual_snapshot.py with fields: writes_1h, recalls_1h, writes_24h, recalls_24h (all int). Collect by running COUNT queries against hive_entries WHERE created_at > NOW() - INTERVAL '1 hour' (and 24h equivalents) and similarly for last_accessed. Add velocity: MemoryVelocity to VisualSnapshot. In the dashboard, add a Velocity panel above the Activity section: a 2×2 stat grid — Writes 1h / Recalls 1h / Writes 24h / Recalls 24h — using the same stat-tile style as the Trust bento. Add delta indicators: if writes_1h > writes_24h/24 the tile shows an up arrow, otherwise down.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/visual_snapshot.py`
- `tests/unit/test_visual_snapshot.py`
- `examples/brain-visual/index.html`
- `examples/brain-visual/brain-visual-help.js`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Add MemoryVelocity dataclass: writes_1h, recalls_1h, writes_24h, recalls_24h (int fields, default 0) (`src/tapps_brain/visual_snapshot.py`)
- [ ] Add _collect_velocity(store) helper: runs 4 COUNT queries (writes 1h, writes 24h, recalls 1h, recalls 24h) against the store backend (`src/tapps_brain/visual_snapshot.py`)
- [ ] For Postgres backend use WHERE created_at > NOW() - INTERVAL '1 hour'; for SQLite use WHERE created_at > datetime('now','-1 hour') (`src/tapps_brain/visual_snapshot.py`)
- [ ] recalls are approximated by last_accessed > NOW() - INTERVAL '1 hour' AND last_accessed != created_at (`src/tapps_brain/visual_snapshot.py`)
- [ ] Add velocity: MemoryVelocity to VisualSnapshot model (`src/tapps_brain/visual_snapshot.py`)
- [ ] Add Velocity panel HTML to index.html: section#velocity with 2x2 stat grid (writes-1h, recalls-1h, writes-24h, recalls-24h tiles) (`examples/brain-visual/index.html`)
- [ ] Add renderVelocity(velocity) JS function with delta arrow logic: up-arrow if 1h rate > 24h hourly average, down-arrow otherwise (`examples/brain-visual/index.html`)
- [ ] Add Velocity link to section nav (`examples/brain-visual/index.html`)
- [ ] Add velocity help entry to brain-visual-help.js — explain what writes vs recalls mean, caveat that recalls approximation uses last_accessed (`examples/brain-visual/brain-visual-help.js`)
- [ ] Add unit tests for _collect_velocity against mock store with known timestamps (`tests/unit/test_visual_snapshot.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] velocity field present in /snapshot response with writes_1h
- [ ] recalls_1h
- [ ] writes_24h
- [ ] recalls_24h
- [ ] counts match direct COUNT queries run against the same database at the same time (±1 for race)
- [ ] Velocity panel renders 4 stat tiles with correct values
- [ ] Up arrow shown when 1h write rate > 24h hourly average (writes_1h > writes_24h / 24)
- [ ] Down arrow shown otherwise
- [ ] Zero counts display as '0' not blank
- [ ] Panel renders correctly on fresh deployment with all-zero velocity
- [ ] _collect_velocity returns MemoryVelocity(0
- [ ] 0
- [ ] 0
- [ ] 0) without exception when store has no entries

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Memory velocity panel code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. 5 writes in last 30m: writes_1h=5
2. writes_24h=5
3. 0 writes in last 1h but 10 in last 24h: writes_1h=0
4. up/down arrow shows down
5. Empty store: all counts 0
6. panel shows 0 tiles no errors
7. Recall approximation: 3 entries accessed since last save: recalls_1h >= 3
8. SQLite backend: same query logic produces correct counts

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- recalls_1h is an approximation — last_accessed is updated on every recall but also on save; the != created_at guard reduces false positives but does not eliminate them. Document this in the help text
- _collect_velocity must work for both SQLite and Postgres backends — use store.backend_type to select the correct datetime function
- Velocity counts reset if the store is wiped — this is expected and should be documented in help text
- Do not attempt to persist velocity history across /snapshot calls — this story is counts-only; time-series storage is out of scope

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-065.1

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [ ] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
