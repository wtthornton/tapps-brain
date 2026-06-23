# Story 78.9 — Complete EPIC-065.3: purge stale/privacy-gated components

**Points:** 3 | **Epic:** EPIC-078 | **Priority:** P1

## What

Finish EPIC-065 story 65.3: remove Tags/Memory Groups sections gated behind `local` privacy tier from default Docker layout; replace static pipeline diagram; dynamic privacy badge.

## Where

- `examples/brain-visual/index.html` — Tags, Memory Groups, pipeline diagram sections
- `docs/planning/epics/stories/STORY-065.3.md` — source AC
- `docs/planning/epics/EPIC-065.md`

## Acceptance Criteria (from EPIC-065.3)

- [x] Tags section removed from default dashboard layout (or shows "requires local privacy tier" only when tier=local)
- [x] Memory Groups section same treatment
- [x] Static retrieval pipeline step-flow diagram removed
- [x] `scorecard-derive.js` deleted if still referenced
- [x] Privacy footer replaced with dynamic badge from snapshot `privacy_tier`
- [x] EPIC-065.3 tasks marked complete in EPIC-065.md
