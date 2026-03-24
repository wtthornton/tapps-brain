---
id: EPIC-036
title: "Release gate hardening for production-ready OpenClaw distribution"
status: done
priority: high
created: 2026-03-24
target_date: 2026-03-31
tags: [release, ci, gating, openclaw, packaging]
---

# EPIC-036: Release Gate Hardening for Production-Ready OpenClaw Distribution

## Context

Readiness is currently assessed manually and can regress between releases. To keep production readiness durable, the project needs a deterministic release gate that validates packaging, QA checks, plugin integrity, and documentation consistency before publish/install recommendations.

## Success Criteria

- [ ] One automated "release-ready" gate exists and is runnable locally and in CI
- [ ] Gate fails on lint/format/type/test failures and plugin test runner instability
- [ ] Gate checks version consistency and packaging build/install sanity
- [ ] Publish checklist and status docs reference the same gate command

## Stories

### STORY-036.1: Add production-release gate script

**Status:** planned
**Effort:** M
**Depends on:** EPIC-034
**Context refs:** `scripts/publish-checklist.md`, `pyproject.toml`, `openclaw-plugin/package.json`
**Verification:** `bash scripts/release-ready.sh` (or platform-equivalent)

#### Why

A single executable gate reduces human error and makes go/no-go decisions objective.

#### Acceptance Criteria

- [ ] New script/run target executes ordered checks for Python + OpenClaw plugin
- [ ] Script exits non-zero on first failing gate and reports failing stage clearly
- [ ] Includes: build, version consistency test, lint, format check, strict mypy, integration sanity, plugin build/test
- [ ] Script is documented for Windows + WSL usage

---

### STORY-036.2: Add docs consistency check for OpenClaw command/status drift

**Status:** planned
**Effort:** S
**Depends on:** EPIC-035
**Context refs:** `docs/guides/openclaw.md`, `docs/guides/openclaw-install-from-git.md`, `openclaw-plugin/UPGRADING.md`, `README.md`
**Verification:** `python scripts/check_openclaw_docs_consistency.py`

#### Why

Known doc drift caused conflicting install and capability guidance; automated checks prevent recurrence.

#### Acceptance Criteria

- [ ] Script validates canonical install command usage across OpenClaw docs
- [ ] Script flags contradictory tool-count/version claims against declared baseline
- [ ] Script is included in release gate and fails with actionable diagnostics

---

### STORY-036.3: Wire release gate into CI and publish checklist

**Status:** planned
**Effort:** S
**Depends on:** STORY-036.1, STORY-036.2
**Context refs:** `.github/workflows/`, `scripts/publish-checklist.md`, `docs/planning/STATUS.md`
**Verification:** CI dry run + `rg "release-ready|check_openclaw_docs_consistency" scripts docs .github/workflows`

#### Why

A local-only gate is insufficient; CI must enforce the same readiness criteria before release actions.

#### Acceptance Criteria

- [ ] CI workflow includes the release-ready command
- [ ] Publish checklist references the exact same gate command
- [ ] Status snapshot references the gate as production-readiness criterion
- [ ] Failure output links directly to remediation docs/epics

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-036.1 - Release gate script | M | Foundation for objective readiness |
| 2 | STORY-036.2 - Docs consistency checker | S | Prevents known recurring documentation failures |
| 3 | STORY-036.3 - CI and checklist wiring | S | Enforces gate continuously |

## Dependency Graph

```
036.1 (release gate script) ─┐
                             ├──→ 036.3 (CI + checklist wiring)
036.2 (docs consistency) ────┘
```
