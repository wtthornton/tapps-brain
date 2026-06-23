# Story 78.8 — UI poll single-flight + exponential backoff

**Points:** 3 | **Epic:** EPIC-078 | **Priority:** P2

## What

Prevent overlapping `/snapshot` fetches when builds exceed poll interval; add exponential backoff on repeated errors.

## Where

- `examples/brain-visual/index.html:5259-5339` — `initLivePolling` IIFE

## Tasks

- [ ] Track in-flight fetch with boolean; skip new poll if previous pending
- [ ] On error: backoff 30s → 60s → 120s (cap); reset on success
- [ ] Manual "Refresh now" button bypasses backoff (if not already present, add to top bar)
- [ ] Do not stack multiple `setInterval(updateBadge)` on interval change (fix leak if present)

## Acceptance Criteria

- [ ] Slow 20s snapshot + 30s interval → never two concurrent fetches (DevTools network tab)
- [ ] After 3 errors, poll interval increases until success
- [ ] Changing poll interval select resets timer cleanly
