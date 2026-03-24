---
id: EPIC-034
title: "Production readiness QA remediation - lint, format, typing, test stability"
status: planned
priority: critical
created: 2026-03-24
target_date: 2026-03-28
tags: [qa, lint, mypy, tests, release]
---

# EPIC-034: Production Readiness QA Remediation - Lint, Format, Typing, Test Stability

## Context

The production-readiness review found hard blockers: failing Ruff checks, formatting drift, and unstable plugin test execution (`npm test` exits non-zero due to unhandled rejection despite passing assertions). These are release blockers for OpenClaw install/upgrade confidence and must be resolved before claiming production readiness.

## Success Criteria

- [ ] `ruff check src/ tests/` passes cleanly on a fresh environment
- [ ] `ruff format --check src/ tests/` passes with zero files to reformat
- [ ] `mypy --strict src/tapps_brain/` completes and passes on the supported CI/dev path
- [ ] `cd openclaw-plugin && npm test` exits zero with no unhandled errors
- [ ] Full QA gate runs green from documented commands in `CLAUDE.md` / `STATUS.md`

## Stories

### STORY-034.1: Eliminate current Ruff violations and lock formatting baseline

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/`, `tests/`, `pyproject.toml`
**Verification:** `ruff check src/ tests/ && ruff format --check src/ tests/`

#### Why

A clean lint/format baseline is the minimum bar for predictable release behavior and lowers future review/merge friction.

#### Acceptance Criteria

- [ ] Existing Ruff violations are fixed or justified with narrow, documented ignores
- [ ] Formatting check passes without local-only drift
- [ ] Any changed linter policy is documented in `pyproject.toml` comments or planning notes
- [ ] No new warnings are introduced in touched modules

---

### STORY-034.2: Stabilize openclaw-plugin test runner exit behavior

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `openclaw-plugin/src/mcp_client.ts`, `openclaw-plugin/tests/mcp_client.test.ts`, `openclaw-plugin/package.json`
**Verification:** `cd openclaw-plugin && npm test`

#### Why

A non-zero test command with "all tests passed" masks real reliability issues and breaks CI/release gating.

#### Acceptance Criteria

- [ ] Reproduce and fix the unhandled timeout rejection in Vitest runs
- [ ] `npm test` exits 0 with no unhandled rejection/error output
- [ ] Regression test added/updated for timeout path cleanup
- [ ] Plugin test output is deterministic across at least two consecutive runs

---

### STORY-034.3: Resolve strict mypy pass path for release gating

**Status:** planned
**Effort:** M
**Depends on:** STORY-034.1
**Context refs:** `src/tapps_brain/`, `pyproject.toml`, `CLAUDE.md`
**Verification:** `mypy --strict src/tapps_brain/`

#### Why

Strict typing is part of the documented quality contract and must be runnable within the intended environment.

#### Acceptance Criteria

- [ ] `mypy --strict src/tapps_brain/` finishes successfully in the supported execution environment
- [ ] Any environment-specific constraints (Windows vs WSL) are documented with explicit command guidance
- [ ] Type issues found during this pass are fixed without reducing strictness globally
- [ ] Verification command in docs matches the actually reliable workflow

---

### STORY-034.4: Re-run and record full QA evidence for release candidate

**Status:** planned
**Effort:** S
**Depends on:** STORY-034.1, STORY-034.2, STORY-034.3
**Context refs:** `CLAUDE.md`, `docs/planning/STATUS.md`, `scripts/publish-checklist.md`
**Verification:** `pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95 && ruff check src/ tests/ && ruff format --check src/ tests/ && mypy --strict src/tapps_brain/ && cd openclaw-plugin && npm test`

#### Why

Production readiness must be proven by one end-to-end green gate, not inferred from partial checks.

#### Acceptance Criteria

- [ ] Full QA command set executes successfully in one runbook
- [ ] Results are reflected in planning/status docs for release decisioning
- [ ] Any remaining non-blocking risks are explicitly listed

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-034.1 - Ruff baseline remediation | M | Unblocks clean code-quality gate |
| 2 | STORY-034.2 - Plugin test stabilization | M | Removes hard CI/release blocker |
| 3 | STORY-034.3 - Strict mypy pass path | M | Restores documented type-safety gate |
| 4 | STORY-034.4 - Full QA evidence pass | S | Final production readiness proof |

## Dependency Graph

```
034.1 (ruff baseline) ──→ 034.3 (mypy pass path)
034.2 (plugin test stability) ────────────────┐
034.3 (mypy pass path) ───────────────────────┼──→ 034.4 (full QA evidence)
034.1 (ruff baseline) ────────────────────────┘
```
