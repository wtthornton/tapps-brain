# Story 78.15 — Dashboard OFFLINE ops runbook

**Points:** 2 | **Epic:** EPIC-078 | **Priority:** P2

## What

Add "Visual dashboard troubleshooting" section covering OFFLINE/ERROR badge, 504 timeout, auth token alignment, unhealthy brain-http, and remediation commands.

## Where

- `docs/guides/visual-snapshot.md` — new troubleshooting section
- `docs/guides/hive-deployment.md` — cross-link
- `docker/README.md` — quick triage table

## Acceptance Criteria

- [ ] Runbook covers: 504 → check brain-http health + snapshot latency; 401 → sync `TAPPS_BRAIN_AUTH_TOKEN` in docker/.env and restart tapps-visual; empty panels → verify `/snapshot` JSON manually
- [ ] Includes `docker logs tapps-brain-http --tail 50` and `curl -H "Authorization: Bearer …" localhost:8080/snapshot` examples (placeholder token)
- [ ] Linked from dashboard empty-state (STORY-078.7)
