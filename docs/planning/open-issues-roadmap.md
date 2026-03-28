# Open Issues Roadmap

Last updated: 2026-03-28 (repo: **#18** hive batch push shipped; close on GitHub when verified)
Owner: @wtthornton

## Purpose

Track delivery status for currently open GitHub issues, prioritized by value and dependency order.

**Canonical queue for shipped product work** in this repo (humans, Cursor, CI context): status and order here are what matter for releases and issue hygiene. Ralph’s `.ralph/fix_plan.md` is a **separate, non-packaged** loop driver — see [Open issues roadmap vs Ralph tooling](PLANNING.md#open-issues-roadmap-vs-ralph-tooling) in `PLANNING.md`.

## Status Legend

- `not_started` - No implementation work started
- `in_progress` - Active implementation underway
- `blocked` - Waiting on dependency/decision
- `done` - Implemented, validated, and ready to close
- `closed` - GitHub issue closed

## Priority Order

**Shipped / closed on GitHub:** #30, #15, #45, #12, #23, #17, #19

**Remaining queue (dependency order):**

1. #21 - store stale listing
2. #20 - profile tier migration

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

- [x] **#12** `feat: Hive push notifications / pub-sub`
  - Status: `done` (monotonic `hive_write_notify` revision; `tapps-brain hive watch`; MCP `hive_write_revision` / `hive_wait_write`; sidecar `~/.tapps-brain/hive/.hive_write_notify`)
  - Target outcome: subscribed agents can react to hive writes in near real time
  - Notes: v1 is poll/long-poll + file signal; optional native push (WAL/socket) later

### Week 4 - Security Hardening

- [x] **#23** `feat: SQLCipher support`
  - Status: `done` (`[encryption]` extra / `pysqlcipher3`; `sqlcipher_util` + `encryption_migrate`; `MemoryPersistence` / `FeedbackStore` / `DiagnosticsHistoryStore` / `HiveStore` / `MemoryStore`; CLI `maintenance encrypt-db|decrypt-db|rekey-db`; `StoreHealthReport.sqlcipher_enabled`; `docs/guides/sqlcipher.md`)
  - Target outcome: optional encrypted-at-rest SQLite backend with migration paths
  - Notes: include key management docs and fallback behavior

### Week 5 - Interop + Search Quality

- [x] **#19** `feat: Sub-agent memory relay`
  - Status: `done` (`memory_relay` parser + `import_relay_to_store`; CLI `tapps-brain relay import` file or `--stdin`; MCP `tapps_brain_relay_export`; `docs/guides/memory-relay.md`; batch context `memory_relay`)
  - Target outcome: portable relay format + import/export path
  - Notes: invalid rows skipped with warnings

- [x] **#40** `feat: adaptive query-aware hybrid search fusion`
  - Status: `done` (2026-03-28) — `hybrid_rrf_weights_for_query`, weighted RRF in `MemoryRetriever._get_hybrid_candidates`; `hybrid_config.adaptive_fusion=False` restores 1:1 weights
  - Dependency: #30 sqlite-vec shipped
  - Target outcome: query-aware BM25/vector weighting improves mixed-query relevance
  - Notes: deterministic heuristics; no LLM

### Week 6 - Workflow and Ops Polish

- [x] **#18** `feat: hive push / push-tagged`
  - Status: `done` (2026-03-28) — CLI `hive push` / `hive push-tagged`; MCP `hive_push`; `PropagationEngine` `dry_run` / `bypass_profile_hive_rules`; `select_local_entries_for_hive_push` + `push_memory_entries_to_hive`
  - Target outcome: low-friction promotion of project memories to hive
  - Notes: `--dry-run`, `--force`; close **#18** on GitHub when verified

- [ ] **#21** `feat: store stale`
  - Status: `not_started`
  - Target outcome: list stale entries for review with machine-readable output

- [ ] **#20** `feat: profile migrate`
  - Status: `not_started`
  - Target outcome: safe tier remapping with audit and dry-run support

- [x] **#17** `feat: session summarization`
  - Status: `closed` on GitHub (2026-03-28) — `tapps-brain session end`, `session_summary.py`, MCP `tapps_brain_session_end`; optional `--daily-note`
  - Target outcome: end-of-session episodic capture (CLI + Python API + MCP)

## Tracking Table

| Priority | Issue | Title | Status | Dependency | Target Week | PR | Notes |
|---|---:|---|---|---|---|---|---|
| 1 | #30 | sqlite-vec local vector search | closed | - | 1 | - | GitHub closed |
| 2 | #15 | diagnostics health | closed | - | 1 | - | GitHub closed |
| 3 | #45 | profile-driven onboarding | closed | - | 2 | - | GitHub closed |
| 4 | #12 | hive pub-sub notifications | closed | - | 3 | - | GitHub closed 2026-03-28 |
| 5 | #23 | SQLCipher encryption | closed | - | 4 | - | GitHub closed 2026-03-28 |
| 6 | #19 | sub-agent memory relay | closed | - | 5 | - | Relay v1.0 + CLI import + MCP export |
| 7 | #40 | adaptive hybrid fusion | done | - | 5 | - | Close on GitHub after verify |
| 8 | #18 | hive push / push-tagged | done | - | 6 | - | MCP `hive_push` + CLI; close on GitHub when verified |
| 9 | #21 | store stale | not_started | - | 6 | - | Maintenance visibility |
| 10 | #20 | profile migrate | not_started | - | 6 | - | Migration utility |
| 11 | #17 | session summarization | closed | - | 6 | - | GitHub closed 2026-03-28 |

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

## Weekly Update - 2026-03-28

- Completed:
  - [x] #30, #15, #45 — already closed on GitHub.
  - [x] #12 hive pub-sub — **closed on GitHub** 2026-03-28 (comment + state).
  - [x] #23 SQLCipher — **closed on GitHub** 2026-03-28 (comment + state).
  - [x] #17 session summarization — **closed on GitHub** 2026-03-28 (prior close).
  - [x] **#19** sub-agent memory relay — shipped + **closed on GitHub** 2026-03-28.
- In progress:
  - None (next queue item: **#21** store stale).
- Blocked:
  - None.
- Scope changes:
  - None this week (planning sync only).
- Next week plan:
  - #21, #20; close **#18** and **#40** on GitHub when verified.

## Change Log

- 2026-03-27: Initial roadmap created from open-issue value prioritization.
- 2026-03-27: Marked #30 as in-progress and added first weekly execution update.
- 2026-03-27: Moved **#15** (diagnostics health) to priority 2 and Week 1; renumbered downstream priorities.
- 2026-03-27: **#12** shipped — Hive write revision counter, `hive watch`, MCP `hive_write_revision` / `hive_wait_write`, `.hive_write_notify` sidecar; **#40** unblocked from #30 in roadmap.
- 2026-03-28: **#23** marked `done` in roadmap and tracking table; `.ralph/fix_plan.md` OPEN-ISSUES mirror updated (OR-5 checked); weekly update rewritten to match shipped state; **EPIC-040** epic stub added under `docs/planning/epics/` pointing at fix_plan for full story list.
- 2026-03-28: Closed **#12** and **#23** on GitHub (`completed`); roadmap priority list collapsed to remaining five issues; **#17** marked `closed` in table (was shipped earlier on GitHub).
- 2026-03-28: **#19** sub-agent memory relay shipped (`memory_relay`, `relay import`, `tapps_brain_relay_export`, `docs/guides/memory-relay.md`); GitHub **#19** closed; MCP tool surface **60** (incl. `tapps_brain_session_end` + `tapps_brain_relay_export` in SKILL baseline).
- 2026-03-28: **#40** adaptive hybrid fusion shipped (`fusion.hybrid_rrf_weights_for_query`, weighted RRF, `hybrid_config.adaptive_fusion`); roadmap priority 7 marked `done` (close issue on GitHub after verify).
- 2026-03-28: **#18** hive push / push-tagged shipped (CLI + MCP `hive_push`, batch helpers in `hive.py`); roadmap priority 8 marked `done`; MCP tool count **61**.
