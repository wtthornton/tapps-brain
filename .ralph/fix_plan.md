# Ralph Fix Plan — tapps-brain

**Scope:** EPIC-066 (fix 90 failing tests, Postgres hardening) → EPIC-065 (live dashboard).
**Task sizing:** Each `- [ ]` is ONE Ralph loop unless marked `[BATCH-N: SMALL]`.
**QA strategy:** ALL testing deferred to 066.14 (final sweep). Do NOT run full test suite at phase boundaries — set `TESTS_STATUS: DEFERRED` for everything until 066.14.

---

## EPIC-066: Postgres-Only Persistence Plane — Production Readiness

**Read first:** `docs/planning/epics/EPIC-066.md`

### Phase A: Failing test fixes <!-- id: 066-phase-a -->

- [x] **066.1** Consolidation merge audit emission [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-066.1.md -->

### Phase B: Operator readiness <!-- id: 066-phase-b -->


### Phase C: Docs, benchmarks, test parity <!-- id: 066-phase-c -->


### Phase D: Final sweep <!-- id: 066-phase-d -->


<!-- Full suite runs at deployment only — not here. Set TESTS_STATUS: DEFERRED and EXIT_SIGNAL: true when 066.14 is done. -->

---

## EPIC-065: Live Always-On Dashboard

**Read first:** `docs/planning/epics/EPIC-065.md`

### Phase A: Live endpoint + polling <!-- id: 065-phase-a -->


### Phase B: Hive and agent monitoring panels <!-- id: 065-phase-b -->

- [ ] **065.5** Agent registry live table [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-065.5.md -->

### Phase C: Velocity and retrieval panels <!-- id: 065-phase-c -->

- [ ] **065.6** Memory velocity panel [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-065.6.md -->
- [ ] **065.7** Retrieval pipeline live metrics panel [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-065.7.md -->

<!-- QA deferred — all testing owned by EPIC-066 story 066.14 -->
