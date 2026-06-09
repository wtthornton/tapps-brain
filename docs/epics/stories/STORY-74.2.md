# 020_experience_events.sql: migration 023 query index

## What

Add performance index for brain_query_events hot path and integration round-trip test proving tapps-mcp verification contract.

## Where

- `src/tapps_brain/migrations/private/023_experience_events_query_index.sql:1-30`
- `tests/integration/test_experience_event_query.py:1-120`

## Acceptance

- [ ] - [ ] Migration 023 adds idx on (project_id
- [ ] event_type
- [ ] event_time DESC) with down migration
- [ ] Integration test records quality_metric with rich payload then queries by event_type and file path
- [ ] Assert payload.score
- [ ] duration_ms
- [ ] gate_passed
- [ ] started_at survive round-trip
- [ ] Test marked requires_postgres and passes in CI Postgres service container

## Refs

docs/planning/epics/EPIC-074.md
