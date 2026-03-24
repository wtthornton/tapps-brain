---
id: EPIC-035
title: "OpenClaw install and upgrade UX consistency"
status: planned
priority: high
created: 2026-03-24
target_date: 2026-03-27
tags: [openclaw, docs, upgrade, install, supportability]
---

# EPIC-035: OpenClaw Install and Upgrade UX Consistency

## Context

The readiness review found documentation and command inconsistencies that can cause failed installs, failed upgrades, and support churn: mixed `openclaw plugin install` vs `openclaw plugins install`, stale "planned" wording for shipped behavior, and inconsistent feature/tool-count messaging across OpenClaw-facing docs.

## Success Criteria

- [ ] All OpenClaw install/upgrade docs use one validated command form
- [ ] No contradictions in feature status (planned vs shipped) across docs
- [ ] Tool/resource counts and compatibility statements are consistent where stated
- [ ] A single "source of truth" section exists for OpenClaw compatibility and install matrix

## Stories

### STORY-035.1: Normalize OpenClaw CLI command usage in docs

**Status:** planned
**Effort:** S
**Depends on:** none
**Context refs:** `docs/guides/openclaw.md`, `docs/guides/openclaw-install-from-git.md`, `openclaw-plugin/README.md`, `openclaw-plugin/UPGRADING.md`, `openclaw-skill/SKILL.md`
**Verification:** `rg "openclaw plugin install|openclaw plugins install" docs openclaw-plugin openclaw-skill`

#### Why

Install command drift is a direct production risk: users copy/paste docs and fail on first run.

#### Acceptance Criteria

- [ ] One canonical install command is chosen and used everywhere
- [ ] Any version-specific alternate command (if required) is explicitly scoped and explained
- [ ] Upgrade docs match install docs terminology and examples

---

### STORY-035.2: Reconcile OpenClaw feature status and capability claims

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `docs/guides/openclaw.md`, `README.md`, `openclaw-skill/SKILL.md`, `openclaw-plugin/src/index.ts`, `docs/planning/epics/EPIC-026.md`, `docs/planning/epics/EPIC-027.md`
**Verification:** `rg "planned|Status: Planned|41 MCP tools|54 tools|7 resources" docs README.md openclaw-skill openclaw-plugin`

#### Why

Conflicting status text erodes trust and causes incorrect operational assumptions during rollout.

#### Acceptance Criteria

- [ ] OpenClaw guide no longer marks shipped behavior as planned
- [ ] Tool and resource counts align with current implementation or are clearly version-scoped
- [ ] Version compatibility matrix matches plugin/runtime behavior
- [ ] Claims are tied to concrete references (code or released epic)

---

### STORY-035.3: Create a canonical OpenClaw install/upgrade runbook

**Status:** planned
**Effort:** S
**Depends on:** STORY-035.1, STORY-035.2
**Context refs:** `docs/guides/openclaw.md`, `docs/guides/openclaw-install-from-git.md`, `openclaw-plugin/UPGRADING.md`
**Verification:** `rg "runbook|upgrade|install|restart" docs/guides/openclaw*.md openclaw-plugin/UPGRADING.md`

#### Why

Operators need a single, deterministic sequence for install, upgrade, validation, and rollback checks.

#### Acceptance Criteria

- [ ] Canonical runbook includes PyPI path and Git-only path with explicit verification steps
- [ ] Upgrade flow includes plugin rebuild/reinstall and restart requirements
- [ ] Includes quick post-upgrade smoke checks (`--version`, recall/capture sanity)
- [ ] Cross-links replace duplicated contradictory instructions

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-035.1 - Normalize CLI commands | S | Fastest way to eliminate immediate install failures |
| 2 | STORY-035.2 - Reconcile capability/status claims | M | Removes operational ambiguity |
| 3 | STORY-035.3 - Canonical runbook | S | Consolidates and hardens operator workflow |

## Dependency Graph

```
035.1 (command normalization) ─┐
                               ├──→ 035.3 (canonical runbook)
035.2 (status/capability sync) ┘
```
