# Ralph Fix Plan — tapps-brain

**Scope: housekeeping, quality, and critical issues only.** Feature work (EPIC-032, DEPLOY-OPENCLAW) is deferred — see epic files for details.

**Task sizing:** Each item is scoped to ONE Ralph loop (~15 min). Do one, check it off, commit.

## Completed Epics

- EPIC-001 through EPIC-016 (core features, test hardening)
- BUG-001: Pre-review critical fixes (7 bugs)
- BUG-002: Source trust regression & uncommitted WIP (6 tasks)
- EPIC-017 through EPIC-025: Code review cycle (53 tasks)
- EPIC-026: OpenClaw Memory Replacement (6 tasks)
- EPIC-027: OpenClaw Full Feature Surface — All 41 MCP Tools (9 tasks)
- EPIC-028: OpenClaw Plugin Hardening (9 tasks)
- EPIC-029: Feedback Collection (explicit + implicit signals, MCP/CLI, Hive propagation)
- EPIC-030: Diagnostics & Self-Monitoring (scorecard, EWMA, circuit breaker, MCP/CLI)
- EPIC-031: Continuous Improvement Flywheel (evaluation harness, Bayesian confidence, gaps, reports, MCP/CLI)
- EPIC-033: OpenClaw Plugin SDK Alignment (GitHub #4–#7)

## Next Tasks

---

### HOUSEKEEPING-001: Close resolved GitHub issues

**Priority: HIGH — public issue tracker shows bugs that are already fixed**

- [x] **HK-001.1** Close GitHub issues #4, #5, #6: these were fixed by EPIC-033 (commits reference STORY-033.2, 033.3, 033.1). Close each with a comment linking to the fixing commit.

---

### HOUSEKEEPING-002: Update stale planning docs

**Priority: MEDIUM — STATUS.md is out of date**

- [x] **HK-002.1** Update `docs/planning/STATUS.md`: mark EPIC-017 through EPIC-025 as `done`, mark EPIC-029/030/031/033 with completion dates, update epics summary table, verify current focus section reflects reality.
- [ ] **HK-002.2** Update `docs/planning/PLANNING.md` epic directory listing: mark EPIC-026 through EPIC-033 with correct done/planned status annotations.

---

### QUALITY-001: Full QA gate

**Priority: MEDIUM — verify project health after all recent changes**

- [ ] **QA-001.1** Run full test suite: `pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95`. Fix any failures.
- [ ] **QA-001.2** Run lint + format: `ruff check src/ tests/ && ruff format --check src/ tests/`. Fix any violations.
- [ ] **QA-001.3** Run type check: `mypy --strict src/tapps_brain/`. Fix any errors.

---

## Deferred (feature work, not in scope)

| Epic | Title | Priority | Notes |
|------|-------|----------|-------|
| EPIC-032 | OTel GenAI Semantic Conventions | LOW | 6 tasks, optional observability upgrade |
| DEPLOY-OPENCLAW | PyPI publish + ClawHub listing | — | 8 tasks, distribution/packaging |
