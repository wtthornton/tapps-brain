---
id: EPIC-076
title: "Dev/deploy stack isolation and brain-ops hardening"
status: done
priority: high
created: 2026-06-09
completed: 2026-06-09
linear_epic: TAP-3176
tags: [docker, devops, mcp, testing, docs]
depends_on: [EPIC-073]
blocks: []
---

# EPIC-076: Dev/deploy stack isolation and brain-ops hardening

## Context

**Incident (2026-06-09):** Dev quick-start Postgres hijacked the `tapps-brain-db`
hostname on `tapps-brain_default`, breaking hive MCP. Root cause fixed in
STORY-076.1; remaining stories were triaged — automation-heavy items cancelled in
favour of docs + minimal test fixes.

## Success Criteria

- [x] `make brain-up` and `make hive-up` cannot silently hijack each other's Postgres hostname (076.1, 076.2)
- [x] `make brain-healthcheck` exits 0 with `coder` profile (076.3)
- [x] Incident recovery documented in `hive-deployment.md` (076.4 + 076.8 merged)
- [x] `test_profile_filter.py` drift counts updated for EPIC-075 (076.7)
- [x] ~~Auto-register in hive-deploy~~ — cancelled (one-time manual `project register` is enough)
- [x] ~~wire-repo-to-brain.sh~~ — deferred until a second consumer repo needs it

## Stories

| Story | Linear | Status | Notes |
|-------|--------|--------|-------|
| 076.1 DNS isolation | [TAP-3177](https://linear.app/tappscodingagents/issue/TAP-3177) | Done | `DEV_COMPOSE=-p tapps-brain-dev` |
| 076.2 Makefile guard | [TAP-3179](https://linear.app/tappscodingagents/issue/TAP-3179) | Done | `check-compose-isolation` |
| 076.3 Profile healthcheck | [TAP-3178](https://linear.app/tappscodingagents/issue/TAP-3178) | Done | Coder vs full tool expectations |
| 076.4 Pool recovery | [TAP-3180](https://linear.app/tappscodingagents/issue/TAP-3180) | Done (docs) | Troubleshooting rows only — no pool-reset code |
| 076.5 wire-repo script | [TAP-3181](https://linear.app/tappscodingagents/issue/TAP-3181) | **Cancelled** | Defer until 2+ repos need onboarding |
| 076.6 hive-deploy register | [TAP-3182](https://linear.app/tappscodingagents/issue/TAP-3182) | **Cancelled** | One-liner in hive-deployment.md |
| 076.7 test_profile_filter | [TAP-3183](https://linear.app/tappscodingagents/issue/TAP-3183) | Done | 80 registered / 67 callable |
| 076.8 Docs runbook | [TAP-3184](https://linear.app/tappscodingagents/issue/TAP-3184) | Done (merged) | Merged into 076.4 + existing postgres-dsn.md |

## Linear

Epic: [TAP-3176](https://linear.app/tappscodingagents/issue/TAP-3176) — **Done**
