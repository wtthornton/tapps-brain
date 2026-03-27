# Open Issues Roadmap

Last updated: 2026-03-27
Owner: @wtthornton

## Purpose

Track delivery status for currently open GitHub issues, prioritized by value and dependency order.

## Status Legend

- `not_started` - No implementation work started
- `in_progress` - Active implementation underway
- `blocked` - Waiting on dependency/decision
- `done` - Implemented, validated, and ready to close
- `closed` - GitHub issue closed

## Priority Order

1. #30 - sqlite-vec integration
2. #15 - diagnostics health (verify current status, likely close)
3. #45 - profile-driven onboarding
4. #12 - hive pub-sub notifications
5. #23 - SQLCipher at-rest encryption
6. #19 - sub-agent memory relay
7. #40 - adaptive hybrid fusion (depends on #30)
8. #18 - hive push / push-tagged
9. #21 - store stale listing
10. #20 - profile tier migration
11. #17 - session summarization workflow

## Roadmap by Week

### Week 1 - Core Retrieval Foundation

- [x] **#30** `feat: integrate sqlite-vec for local vector search`
  - Status: `done` (optional `sqlite-vec` in `[vector]` + dev; `memory_vec` vec0; hybrid prefers KNN when enabled)
  - Target outcome: local semantic search available without external API dependency
  - Notes: establish optional dependency + safe fallback path; begin with schema + embedding write path

- [x] **#15** `feat: diagnostics health command + MCP tool`
  - Status: `done` (CLI `diagnostics health`, MCP `tapps_brain_health`, store report includes sqlite-vec fields)
  - Target outcome: verify acceptance criteria against current code and close if complete
  - Notes: quick validation/documentation; floated from former Week 6 slot so the issue can close early

### Week 2 - Adoption and Usage Quality

- [x] **#45** `feat: Profile-driven agent onboarding`
  - Status: `done` (`tapps-brain profile onboard`, MCP `memory_profile_onboarding`)
  - Target outcome: agents receive structured profile-based memory usage guidance
  - Notes: include CLI and MCP access

### Week 3 - Real-Time Multi-Agent Coordination

- [ ] **#12** `feat: Hive push notifications / pub-sub`
  - Status: `not_started`
  - Target outcome: subscribed agents can react to hive writes in near real time
  - Notes: prioritize reliability and simple fan-out semantics

### Week 4 - Security Hardening

- [ ] **#23** `feat: SQLCipher support`
  - Status: `not_started`
  - Target outcome: optional encrypted-at-rest SQLite backend with migration paths
  - Notes: include key management docs and fallback behavior

### Week 5 - Interop + Search Quality

- [ ] **#19** `feat: Sub-agent memory relay`
  - Status: `not_started`
  - Target outcome: portable relay format + import/export path
  - Notes: tolerate partial invalid relay items

- [ ] **#40** `feat: adaptive query-aware hybrid search fusion`
  - Status: `blocked`
  - Dependency: #30
  - Target outcome: query-aware BM25/vector weighting improves mixed-query relevance
  - Notes: keep deterministic and testable weighting heuristics

### Week 6 - Workflow and Ops Polish

- [ ] **#18** `feat: hive push / push-tagged`
  - Status: `not_started`
  - Target outcome: low-friction promotion of project memories to hive

- [ ] **#21** `feat: store stale`
  - Status: `not_started`
  - Target outcome: list stale entries for review with machine-readable output

- [ ] **#20** `feat: profile migrate`
  - Status: `not_started`
  - Target outcome: safe tier remapping with audit and dry-run support

- [ ] **#17** `feat: session summarization`
  - Status: `not_started`
  - Target outcome: complete end-of-session capture flow (CLI/API/MCP)
  - Notes: reassess implementation overlap before starting

## Tracking Table

| Priority | Issue | Title | Status | Dependency | Target Week | PR | Notes |
|---|---:|---|---|---|---|---|---|
| 1 | #30 | sqlite-vec local vector search | done | - | 1 | - | Foundation for hybrid quality |
| 2 | #15 | diagnostics health | done | - | 1 | - | Validate and close if complete |
| 3 | #45 | profile-driven onboarding | done | - | 2 | - | Adoption multiplier |
| 4 | #12 | hive pub-sub notifications | not_started | - | 3 | - | Real-time coordination |
| 5 | #23 | SQLCipher encryption | not_started | - | 4 | - | Security/compliance |
| 6 | #19 | sub-agent memory relay | not_started | - | 5 | - | Interop and continuity |
| 7 | #40 | adaptive hybrid fusion | blocked | #30 | 5 | - | Quality optimization |
| 8 | #18 | hive push / push-tagged | not_started | - | 6 | - | Sharing ergonomics |
| 9 | #21 | store stale | not_started | - | 6 | - | Maintenance visibility |
| 10 | #20 | profile migrate | not_started | - | 6 | - | Migration utility |
| 11 | #17 | session summarization | not_started | - | 6 | - | Episodic workflow |

## Weekly Update Template

Copy this section at the end of each week:

```md
## Weekly Update - YYYY-MM-DD

- Completed:
  - [ ] #XX ...
- In progress:
  - [ ] #XX ...
- Blocked:
  - [ ] #XX ... (reason)
- Scope changes:
  - None / details
- Next week plan:
  - #XX, #YY
```

## Weekly Update - 2026-03-27

- Completed:
  - [ ] Triage pass completed for all open feature issues (labels + decision comments).
- In progress:
  - [ ] #30 sqlite-vec local vector search (core foundation).
- Blocked:
  - [ ] #40 adaptive hybrid fusion (blocked on #30 dependency).
- Scope changes:
  - Added agent governance and feature-intake enforcement docs/templates.
  - Raised **#15** (diagnostics health) to priority 2 / Week 1 for early validation and close.
- Next week plan:
  - #30, #15, #45

## Change Log

- 2026-03-27: Initial roadmap created from open-issue value prioritization.
- 2026-03-27: Marked #30 as in-progress and added first weekly execution update.
- 2026-03-27: Moved **#15** (diagnostics health) to priority 2 and Week 1; renumbered downstream priorities.
