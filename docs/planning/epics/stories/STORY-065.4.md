# Story 65.4 -- Hive hub deep monitoring panel

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** the Hive hub panel to show per-namespace entry counts, overall connection health, and agent count in a structured table, **so that** I can immediately see which namespaces are active, how many memories each holds, and whether the Hive is healthy without running psql

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 8 | **Size:** L

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the Hive hub section becomes the primary operational view for a multi-agent deployment. The current panel shows a single prose string: "Connected · 1176 entries · 20 registered agents · namespaces: personal-assistant, repo-brain, universal." This tells the operator nothing about the health or distribution of individual namespaces. A namespace that has zero entries, a namespace that is growing unbounded, or a namespace that stopped receiving writes are all invisible. This story replaces the prose string with a structured table backed by live per-namespace counts from the Postgres hive.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Extend HiveHealthSummary in visual_snapshot.py to include a namespace_detail list: [{namespace, entry_count, last_write_at}]. Extend _collect_hive_health() to run a single GROUP BY query against the hive entries table to collect these. Update VisualSnapshot schema. In the dashboard, replace the hive-detail prose block with a table: Namespace | Entries | Last Write. Add a hive status row at the top: connected/degraded/offline badge, total agents, total entries. Add a "No namespaces" empty state for fresh deployments.

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

- [ ] Add NamespaceDetail dataclass/TypedDict with fields: namespace str, entry_count int, last_write_at str | None (`src/tapps_brain/visual_snapshot.py`)
- [ ] Add namespace_detail: list[NamespaceDetail] field to HiveHealthSummary (`src/tapps_brain/visual_snapshot.py`)
- [ ] Extend _collect_hive_health() with SELECT namespace, COUNT(*), MAX(updated_at) FROM hive_entries GROUP BY namespace query (`src/tapps_brain/visual_snapshot.py`)
- [ ] Update VisualSnapshot tests to assert namespace_detail is populated when hive is connected (`tests/unit/test_visual_snapshot.py`)
- [ ] Replace #hive-detail prose block in index.html with a <table id='hive-ns-table'> with columns Namespace, Entries, Last Write (`examples/brain-visual/index.html`)
- [ ] Add hive status row above the table: connected/degraded/offline badge + total agents + total entries summary (`examples/brain-visual/index.html`)
- [ ] Add empty state row 'No namespaces — Hive has no data yet' when namespace_detail is empty (`examples/brain-visual/index.html`)
- [ ] Format last_write_at as relative time (e.g. '2m ago', '3h ago') using existing date formatting utilities in index.html (`examples/brain-visual/index.html`)
- [ ] Add hive_namespace_detail help entry to brain-visual-help.js (`examples/brain-visual/brain-visual-help.js`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] HiveHealthSummary.namespace_detail contains one row per namespace with entry_count and last_write_at
- [ ] _collect_hive_health() issues a single GROUP BY query — not one query per namespace
- [ ] Hive hub panel renders a table with Namespace / Entries / Last Write columns
- [ ] Table rows match direct psql SELECT namespace
- [ ] COUNT(*) FROM hive_entries GROUP BY namespace
- [ ] Last Write column shows relative time string (e.g. '5m ago') not raw ISO timestamp
- [ ] Empty state row appears when hive is connected but no namespaces exist
- [ ] Status badge correctly shows connected (green) / degraded (amber) / offline (red)
- [ ] HiveHealthSummary serialises cleanly through VisualSnapshot.model_dump() for /snapshot JSON response

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Hive hub deep monitoring panel code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. Populated hive: namespace_detail has correct counts matching psql
2. Empty hive: namespace_detail == [] and empty state renders
3. Hive unreachable: connected=False
4. namespace_detail=[]
5. offline badge renders
6. Last write formatting: last_write_at 65 seconds ago → '1m ago'
7. Serialisation: HiveHealthSummary with namespace_detail round-trips through model_dump/model_validate

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- The hive_entries table name may vary by schema version — check postgres_hive.py for the canonical table name
- MAX(updated_at) may be NULL for namespaces with no updated_at column — use COALESCE(MAX(updated_at)
- MAX(created_at)) as last_write_at
- _collect_hive_health() must still return connected=False gracefully if psycopg import fails or DSN is not set

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
- [ ] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
