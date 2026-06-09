---
id: EPIC-077
title: "Retire Ralph autonomous loop from tapps-brain"
status: done
priority: medium
created: 2026-06-09
linear_epic: TAP-3198
tags: [docs, devops, cleanup]
---

# EPIC-077: Retire Ralph autonomous loop from tapps-brain

## Context

Team confirmed Ralph is no longer the delivery mechanism (2026-06-09). Linear +
`docs/planning/epics/` is the canonical queue.

## Success criteria

- [x] Ralph scripts archived under `scripts/archive/ralph/`
- [x] Ralph guides archived under `docs/planning/archive/ralph-retired/`
- [x] `.ralph/` reduced to retired pointer README; `.ralphrc` removed
- [x] Ralph agents/skills removed from `.claude/` and `.cursor/`
- [x] Core docs (`CLAUDE.md`, `AGENTS.md`, `STATUS.md`, `PLANNING.md`, rules) — Linear-only queue
- [x] Claude hooks: Ralph hooks removed; Tapps hooks use `.tapps-mcp/` paths
- [x] `grep` on active docs returns only retired/archive/CHANGELOG mentions
- [x] `bash scripts/release-ready.sh` green

## Stories

| Story | Scope | Status |
|-------|-------|--------|
| 077.1 | Docs de-Ralph | Done |
| 077.2 | Control plane removal | Done |
| 077.3 | Agents/skills | Done |
| 077.4 | Scripts/guides archive | Done |
| 077.5 | Hooks + IDE | Done |

## Out of scope

- Ralph removal in other repos (tapps-mcp, nlt-portfolio)
- Deleting CHANGELOG history
- TAP-1845 Linear poller credential (ops)
