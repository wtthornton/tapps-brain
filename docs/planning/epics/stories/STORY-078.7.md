# Story 78.7 — UI degraded-mode + HTTP error classification

**Points:** 5 | **Epic:** EPIC-078 | **Priority:** P1

## What

Improve empty-state and ERROR badge copy to distinguish **504 timeout**, **401/403 auth**, **503 no-store**, and **network offline** with operator remediation steps.

## Where

- `examples/brain-visual/index.html:2515-2525` — empty state
- `examples/brain-visual/index.html:5287-5302` — `fetchSnapshot` error handling

## Why

Today all failures collapse to `ERROR · HTTP 504` after 3 attempts. Operators cannot tell whether to fix auth token, start brain-http, or wait for slow snapshot.

## Tasks

- [ ] Map HTTP status to user-facing messages: 504 → "Brain snapshot timed out — check tapps-brain-http logs"; 401/403 → "Auth token mismatch between visual nginx and brain-http"; 503 → "No MemoryStore configured"
- [ ] Show last-error detail in empty-state `<details>` expandable (no secrets)
- [ ] Add link anchor to `docs/guides/hive-deployment.md#visual-dashboard` troubleshooting
- [ ] Keep badge states: LIVE / STALE / OFFLINE / ERROR with distinct aria labels

## Acceptance Criteria

- [ ] Simulated 504 shows timeout-specific copy (manual or Playwright)
- [ ] Simulated 401 shows auth-specific copy
- [ ] Empty state remains keyboard-accessible (focusable details)
- [ ] No raw Bearer tokens logged or displayed
