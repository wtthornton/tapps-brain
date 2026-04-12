# Ralph Fix Plan — tapps-brain

**Scope:** EPIC-066 (fix 90 failing tests, Postgres hardening) → EPIC-065 (live dashboard).
**Task sizing:** Each `- [ ]` is ONE Ralph loop unless marked `[BATCH-N: SMALL]`.
**QA strategy:** Run full QA **only** at `🔒 QA GATE` lines. Everything else → `TESTS_STATUS: DEFERRED`.

---

## EPIC-066: Postgres-Only Persistence Plane — Production Readiness

**Read first:** `docs/planning/epics/EPIC-066.md`

### Phase A: Failing test fixes <!-- id: 066-phase-a -->

- [x] **066.1** Consolidation merge audit emission [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-066.1.md -->
🔒 **QA GATE — Phase A.** `uv run pytest tests/ -v --tb=short -m "not benchmark and not requires_postgres"` — target: 0 failures.

### Phase B: Operator readiness <!-- id: 066-phase-b -->

- [ ] **066.6** CI workflow with ephemeral Postgres service container [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-066.6.md -->
- [ ] **066.7** Connection pool tuning + health JSON pool fields [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-066.7.md -->
- [ ] **066.8** Auto-migrate on startup gate (`TAPPS_BRAIN_AUTO_MIGRATE=1`) [SMALL] <!-- story: docs/planning/epics/stories/STORY-066.8.md -->

### Phase C: Docs, benchmarks, test parity <!-- id: 066-phase-c -->

- [ ] **066.9** Behavioral parity doc + load smoke benchmark [LARGE] <!-- story: docs/planning/epics/stories/STORY-066.9.md -->
- [ ] **066.10** pg_tde operator runbook [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-066.10.md -->
- [ ] **066.11** Postgres backup and restore runbook [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-066.11.md -->
- [ ] **066.12** Engineering docs drift sweep — zero stale SQLite refs [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-066.12.md -->
- [ ] **066.13** Postgres integration tests replacing deleted SQLite-coupled tests [LARGE] <!-- story: docs/planning/epics/stories/STORY-066.13.md -->

### Phase D: Final sweep <!-- id: 066-phase-d -->

- [ ] **066.14** Final test failure sweep — 90 to zero, tag 3.4.0 [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-066.14.md -->

🔒 **QA GATE — EPIC-066 complete.** `uv run pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-fail-under=95 && ruff check src/ tests/ && mypy --strict src/tapps_brain/`

---

## EPIC-065: Live Always-On Dashboard

**Read first:** `docs/planning/epics/EPIC-065.md`

### Phase A: Live endpoint + polling <!-- id: 065-phase-a -->

- [ ] **065.1** GET /snapshot live endpoint on HttpAdapter [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-065.1.md -->
- [ ] **065.2** Dashboard live polling mode [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-065.2.md -->
- [ ] **065.3** Purge stale and privacy-gated components [SMALL] <!-- story: docs/planning/epics/stories/STORY-065.3.md -->

### Phase B: Hive and agent monitoring panels <!-- id: 065-phase-b -->

- [ ] **065.4** Hive hub deep monitoring panel [LARGE] <!-- story: docs/planning/epics/stories/STORY-065.4.md -->
- [ ] **065.5** Agent registry live table [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-065.5.md -->

### Phase C: Velocity and retrieval panels <!-- id: 065-phase-c -->

- [ ] **065.6** Memory velocity panel [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-065.6.md -->
- [ ] **065.7** Retrieval pipeline live metrics panel [MEDIUM] <!-- story: docs/planning/epics/stories/STORY-065.7.md -->

🔒 **QA GATE — EPIC-065 complete.** `uv run pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-fail-under=95 && ruff check src/ tests/ && mypy --strict src/tapps_brain/`
