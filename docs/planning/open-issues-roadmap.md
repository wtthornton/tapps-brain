# Open Issues Roadmap

Last updated: 2026-04-09 — **v3.2.0** — EPIC-048 complete (all 6 stories); default embedding → `BAAI/bge-small-en-v1.5`; FlashRank local reranker; FTS5 porter unicode61; schema reset to v1; Docker base → python:3.13-slim; docs synced
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

**Shipped / closed on GitHub:** #30, #15, #45, #12, #23, #17, #19, #18, #40, #21, #20, **#46**, **#48**, **#47** (mitigated), **#49** (v1 `memory_group` epic complete)

**Closed on GitHub (EPIC-041):**

- **[#51](https://github.com/wtthornton/tapps-brain/issues/51)** — federation hub `memory_group` (49-E); STORY-041.1.
- **[#52](https://github.com/wtthornton/tapps-brain/issues/52)** — Hive `agent_scope` `group:<name>` + membership + recall namespace union; STORY-041.2 (**closed** on GitHub — checklist aligned with `main`).
- **[#63](https://github.com/wtthornton/tapps-brain/issues/63)** — `retrieval_effective_mode` / `retrieval_summary` on health (CLI + MCP); STORY-041.3.
- **[#64](https://github.com/wtthornton/tapps-brain/issues/64)** — [`hive-vs-federation.md`](../guides/hive-vs-federation.md); STORY-041.4.

**Next engineering (canonical queue — greenfield v3):**

| Order | Epic | Title | Status | Blocks |
|------:|------|-------|--------|--------|
| 1 | [EPIC-059](epics/EPIC-059.md) | Postgres-Only Persistence Plane | `planned` | 060, 061, 062, 063 |
| 2 | [EPIC-060](epics/EPIC-060.md) | Agent-First Core & Minimal Runtime API | `planned` | 062 |
| 3 | [EPIC-061](epics/EPIC-061.md) | Observability-First Product | `planned` | — |
| 4 | [EPIC-063](epics/EPIC-063.md) | Trust Boundaries & Postgres Enforcement | `planned` | — |
| 5 | [EPIC-062](epics/EPIC-062.md) | MCP-Primary Integration & Env Contract | `planned` | — |
| 6 | [EPIC-032](epics/EPIC-032.md) | OTel GenAI Semantic Conventions | `planned` (low) | — |

See individual epic files for story breakdown (8–10 stories each, ~42 Ralph loops total). ADR: [`ADR-007`](adr/ADR-007-postgres-only-no-sqlite.md).

**Further backlog (no GitHub issue yet — file when scheduled):** architecture review follow-ups; clarity / observability / optional refactors—not emergency fixes.

**Product experience (parallel / post-v3 polish):** [EPIC-064](epics/EPIC-064.md) — brain-visual narrative motion, deep insight panels, **web-cited** WCAG/MDN motion constraints, **NLT Labs style sheet + logo pack** audit (`064.1`), and explicit **docs-mcp** + **tapps-mcp** verification (see epic § *MCP + web coverage*); Ralph [`.ralph/fix_plan.md`](../.ralph/fix_plan.md) § EPIC-064.

**EPIC-052 deferred follow-up (not blocking):**
- **`persistence.delete_relations` O(n) cleanup path** — currently loads all relations, JSON-parses `source_entry_keys` per row in Python, and issues one `DELETE` per match. Called only when deleting a single entry's related rows (low frequency). Optimization candidate: single SQL `DELETE` with `JSON_EACH` / `LIKE` on a canonicalized key string. Kept out of scope for EPIC-052 per non-goals (no structural refactor). File a GitHub issue when relation volume becomes a bottleneck.

**Gating:** Optional slices **B** (save-path metrics **beyond** [`ADR-006`](adr/ADR-006-save-path-observability.md) — histograms + `save_phase_summary` + `memory://metrics` already shipped), **C** (EPIC-042 hygiene), and **in-product NLI/async** conflicts are **backlogged by default** until a trigger in [`PLANNING.md` § Optional backlog gating](PLANNING.md#optional-backlog-gating) applies.

5. **Save-path and maintenance observability** — **Baseline shipped** + **ADR-006** ([`adr/ADR-006-save-path-observability.md`](adr/ADR-006-save-path-observability.md)); optional **deeper** metrics / consolidation–GC correlation only with trigger **(a)**.
6. **Concurrency and scaling notes** — Document realistic expectations for **threading.Lock** + synchronous SQLite under concurrent MCP/CLI use; optional follow-up issue for lock-hold timing or queue depth if product needs it.
7. **MemoryStore modularization (epic)** — Long-term refactor to split orchestration into smaller facades behind a stable public API; only with sustained pain or capacity.

**Closed epic #49 (v1 recap):** schema **v16** `memory_group`, store/retrieval/recall, MCP (`group` + `memory_list_groups`), CLI (`--group`, `store groups`), [`memory-scopes.md`](../guides/memory-scopes.md), relay [`memory-relay.md`](../guides/memory-relay.md). Child slices 49-A–D were delivered on `main` without separate GitHub children; see [#49](https://github.com/wtthornton/tapps-brain/issues/49) closure comment.

**Shipped in repo (2026-03-28 PR):**

- **#46** — `assemble()` / recall injection: MCP `CallToolResult` unwrapping in
  `openclaw-plugin` (`mcp_tool_text` / `McpClient.callTool`) + `value` on recall summaries in
  `inject_memories`. *Manual check:* OpenClaw session → `assemble()` shows text from existing
  memories.
- **#48** — Subagent / odd tiers: `normalize_save_tier` (`tier_normalize.py`) in
  `MemoryStore.save`, `memory_save` MCP, relay import; profile layer names before global
  aliases (e.g. `UNKNOWN_TIER` → `pattern` or profile layer). *Manual check:* save with a
  weird tier and confirm stored tier in DB/recall.
- **#47** — Tool-name collisions mitigated: plugin exposes `tapps_memory_search` /
  `tapps_memory_get`; troubleshooting in `docs/guides/openclaw.md`. Stronger follow-up (e.g.
  MCP `--tool-prefix` on the host) can be a new issue if needed.

**Recently shipped (repo + GitHub closed 2026-03-28):** #18 hive push, #40 adaptive hybrid fusion, #21 store stale, #20 profile migrate

## Roadmap by Week

### Week 1 - Core Retrieval Foundation

- [x] **#30** `feat: integrate sqlite-vec for local vector search`
  - Status: `done` (`sqlite-vec` promoted to core dependency in v2.2.0; `memory_vec` vec0; hybrid KNN enabled by default)
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
  - Notes: `--dry-run`, `--force`; **#18** closed on GitHub 2026-03-28

- [x] **#21** `feat: store stale`
  - Status: `done` (2026-03-28) — `MemoryGarbageCollector.stale_candidate_details`, `MemoryStore.list_gc_stale_details`, CLI `maintenance stale`, MCP `maintenance_stale`; `maintenance gc` / MCP `maintenance_gc` / `store.gc` / `health()` use profile decay + `gc_config`

- [x] **#20** `feat: profile migrate`
  - Status: `done` (2026-03-28) — `profile_migrate` helpers, `MemoryStore.migrate_entry_tiers`, CLI `profile migrate-tiers --map from:to`, MCP `profile_tier_migrate` (`tier_map_json`, `dry_run`); audit action `tier_migrate`

- [x] **#17** `feat: session summarization`
  - Status: `closed` on GitHub (2026-03-28) — `tapps-brain session end`, `session_summary.py`, MCP `tapps_brain_session_end`; optional `--daily-note`
  - Target outcome: end-of-session episodic capture (CLI + Python API + MCP)

## Recommended next steps (2026-04-09)

- **EPIC-053–058** — all done in v3.1.0; epic status files updated to reflect.
- **EPIC-040 / 042 / 044 / 050** — all marked done (status files updated 2026-04-09).
- **Agent / Cursor handoff:** paste-ready task prompt in [`next-session-prompt.md`](next-session-prompt.md); numbered queue mirrored in [`STATUS.md`](STATUS.md) § *Current focus*.

**Next engineering (canonical queue)**

- **EPIC-048** — ✅ **Complete (2026-04-09).** All 6 stories done: 048.1 session GC + token budget, 048.2 relations batch + cycle detection, 048.3 markdown round-trip, 048.4 eval CI, 048.5 doc validation, 048.6 visual PNG capture.
- **EPIC-032** — OTel GenAI semantic conventions. Low priority; defer until stakeholder request.
- **MemoryStore decomposition** (tracking row 22): design-first only; concurrency/scale ADR: [`adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md`](adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md).
- **Backlog-gated slices** (per [`PLANNING.md` § Optional backlog gating](PLANNING.md#optional-backlog-gating)):
  - Extra save-path observability beyond ADR-006 — trigger (a) only.
  - EPIC-042 eval/GitHub hygiene — trigger (b) only.
  - NLI/async conflict wiring — trigger (c) only.

**Done in repo (was backlog item 6)**

- **Concurrency expectations** for operators: [`system-architecture.md`](../engineering/system-architecture.md) § *Concurrency model* — threading + SQLite under MCP/CLI; file a code issue only if benchmarks show a real pain point.

## Tracking Table

| Priority | Issue | Title | Status | Dependency | Target Week | PR | Notes |
|---|---:|---|---|---|---|---|---|
| 1 | #30 | sqlite-vec local vector search | closed | - | 1 | - | GitHub closed |
| 2 | #15 | diagnostics health | closed | - | 1 | - | GitHub closed |
| 3 | #45 | profile-driven onboarding | closed | - | 2 | - | GitHub closed |
| 4 | #12 | hive pub-sub notifications | closed | - | 3 | - | GitHub closed 2026-03-28 |
| 5 | #23 | SQLCipher encryption | closed | - | 4 | - | GitHub closed 2026-03-28 |
| 6 | #19 | sub-agent memory relay | closed | - | 5 | - | Relay v1.0 + CLI import + MCP export |
| 7 | #40 | adaptive hybrid fusion | closed | - | 5 | - | GitHub closed 2026-03-28 |
| 8 | #18 | hive push / push-tagged | closed | - | 6 | - | GitHub closed 2026-03-28 |
| 9 | #21 | store stale | closed | - | 6 | - | GitHub closed 2026-03-28 |
| 10 | #20 | profile migrate | closed | - | 6 | - | GitHub closed 2026-03-28 |
| 11 | #17 | session summarization | closed | - | 6 | - | GitHub closed 2026-03-28 |
| 12 | #46 | OpenClaw assemble / MCP recall text | closed | - | — | — | GitHub closed 2026-03-28 |
| 13 | #48 | save tier normalization | closed | - | — | — | GitHub closed 2026-03-28 |
| 14 | #47 | tool name conflicts | closed | mitigated | — | — | `tapps_memory_*` + openclaw.md |
| 15 | #49 | multi-scope memory epic (v1) | closed | — | — | — | 2026-03-29; backlog → **#51**, **#52** |
| 16 | #51 | federation hub `memory_group` (49-E) | closed | — | — | — | EPIC-041 STORY-041.1; GitHub closed 2026-03-31 / verified |
| 17 | #52 | `agent_scope` group:<name> + membership | closed | — | — | — | Checklist vs `main` 2026-04-02; GitHub closed |
| 18 | #63 | Vector / hybrid discoverability (health & guides) | closed | — | — | — | EPIC-041 STORY-041.3; GitHub closed 2026-04-02 |
| 19 | #64 | Hive vs federation decision guide | closed | — | — | — | EPIC-041 STORY-041.4; GitHub closed 2026-04-02 |
| 20 | — | Save-path / consolidation observability | done | — | — | — | Phase histograms + `save_phase_summary` on `store.health()` / MCP health (live store); `memory://metrics` unchanged |
| 21 | — | Concurrency expectations (docs; metrics optional) | done | — | — | — | 2026-04-01 — `system-architecture.md` § concurrency |
| 22 | — | MemoryStore modularization epic | not_started | — | — | — | Backlog 2026-03-31; long-term refactor |
| 23 | #66 | Async wrapper (`AsyncMemoryStore`) | closed | — | — | — | `tapps_brain/aio.py` + `test_aio.py`; GitHub closed 2026-04-09 |
| 24 | #69 | Default semantic search enabled | closed | — | — | — | sqlite-vec now core dep; GitHub closed 2026-04-09 |
| 25 | #72 | Entry archival strategy for 5K cap | closed | — | — | — | GC + eviction already handles; GitHub closed 2026-04-09 |
| 26 | #70 | Temporal query filtering (`since` / `until` / relative shorthands) | closed | — | — | — | `_parse_relative_time`, MCP params, 11 tests; GitHub closed 2026-04-09 |
| 27 | #71 | Consolidation threshold profile-configurable | closed | — | — | — | `ConsolidationProfileConfig.threshold` wired; `personal-assistant.yaml` 0.65; GitHub closed 2026-04-09 |

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
  - [x] **#21** store stale listing — `maintenance stale`, MCP `maintenance_stale`, `StaleCandidateDetail` / `list_gc_stale_details`.
  - [x] **#20** profile tier migrate — `profile migrate-tiers`, MCP `profile_tier_migrate`, audit `tier_migrate`.
  - [x] **#46** / **#48** / **#47** — shipped + **closed on GitHub** 2026-03-28 (OpenClaw MCP unwrap + tier normalize + mitigated tool names).
  - [x] **#49** — design note + `epic-49-tasks.md`; core + relay `memory_group` shipped in repo
    (2026-03-28). Epic **closed** 2026-03-29; backlog **#51** / **#52** (see Weekly Update 2026-03-29).
- In progress:
  - None.
- Blocked:
  - None.
- Scope changes:
  - None this week (planning sync only).
- Next week plan:
  - Triage **#51** / **#52** when a subscriber or product milestone needs them.

## Weekly Update - 2026-03-29

- Completed:
  - [x] **#49** — closed on GitHub; v1 `memory_group` epic complete on `main`.
  - [x] Backlog filed: **#51** (49-E federation), **#52** (long-term `agent_scope` groups).
- In progress:
  - None.
- Blocked:
  - None.
- Next week plan:
  - **#51** / **#52** — backlog only; prioritize when requested.

## Weekly Update - 2026-03-31

- Completed:
  - [x] Architecture vs documentation review summarized; roadmap updated with **Recommended next steps** and backlog rows **18–22** (file GitHub issues when work is scheduled).
- In progress:
  - None.
- Blocked:
  - None.
- Next week plan:
  - Product triage: **#51** / **#52**; optionally file issues for backlog **18** (vector clarity) and **19** (Hive vs federation guide).

## Weekly Update - 2026-04-01

- Completed:
  - [x] **#51** — subscriber confirmed; roadmap priority updated to implement 49-E.
  - [x] Filed **[#63](https://github.com/wtthornton/tapps-brain/issues/63)** (vector / hybrid discoverability), **[#64](https://github.com/wtthornton/tapps-brain/issues/64)** (Hive vs federation guide); tracking table rows **18–19** updated.
- In progress:
  - As needed: **#51** implementation; **#63** / **#64** doc/health work.
- Blocked:
  - None.
- Next week plan:
  - Ship **#51** per `epic-49-tasks.md` § 49-E; pick up **#63** / **#64** when ready. (**#52** confirmed 2026-04-02 — see Weekly Update 2026-04-02.)

## Weekly Update - 2026-04-02

- Completed:
  - [x] **#52** — product confirmed for delivery; roadmap updated (no longer “defer until milestone”); tracking note reflects epic-sized Hive work.
  - [x] **#51** / 49-E — federation hub `memory_group` shipped in repo (`federation.py`, tests, guides, **EPIC-041** STORY-041.1); GitHub **#51** comment added — close issue when verified.
  - [x] **#63** / **#64** — health retrieval mode + summary; `docs/guides/hive-vs-federation.md` + cross-links; GitHub comments — close issues when verified.
  - [x] **EPIC-041** created (`docs/planning/epics/EPIC-041.md`); **PLANNING.md** epic index updated.
- In progress:
  - **#52** (STORY-041.2) only.
- Blocked:
  - None.
- Next week plan:
  - Slice **#52** per design note; close **#63** / **#64** on GitHub after verify.

## Change Log

- 2026-04-04: **Optional backlog gating** — `PLANNING.md` § *Optional backlog gating*; backlog-by-default for extra save-path observability, EPIC-042 hygiene, and in-product NLI/async unless triggers (a)–(c); `next-session-prompt` + roadmap Priority Order + `STATUS` aligned.
- 2026-04-04: **STORY-044.3** offline slice — `run_save_conflict_candidate_report`, CLI `maintenance save-conflict-candidates`, [`save-conflict-nli-offline.md`](../guides/save-conflict-nli-offline.md); roadmap + `STATUS` + `next-session-prompt` + `EPIC-044.md` synced.
- 2026-04-09: **#70 shipped** — `MemoryStore._parse_relative_time()` expands `7d`/`2w`/`1m` shorthands before SQL layer; MCP `memory_search` gains `since`/`until`/`time_field` params; 11 new tests in `TestMemoryStoreTemporalSearch`; ruff + mypy clean; GitHub **#70** closed.
- 2026-04-09: **#71 verified + closed** — `ConsolidationProfileConfig.threshold` fully wired from profile YAML through `store.py` to `auto_consolidation.py`; `personal-assistant.yaml` ships `consolidation.threshold: 0.65`; GitHub **#71** closed.
- 2026-04-09: **STORY-048.6** — `capture_png()` in `visual_snapshot.py`; `tapps-brain visual capture` CLI; `[visual]` optional extra (playwright); 4 new tests; manual checklist in `docs/guides/visual-snapshot.md`; **EPIC-048 complete** (all 6 stories done).
- 2026-04-09: **Issue hygiene** — closed **#66** (async wrapper shipped as `tapps_brain.aio.AsyncMemoryStore`), **#69** (sqlite-vec core), **#72** (5K eviction/GC already handled); EPIC-048 story order documented; next-session-prompt updated.
- 2026-04-09: **v3.2.0** — EPIC-048 complete (all 6 stories: session GC, relations batch, markdown round-trip, eval CI, doc validation, visual PNG capture); default embedding → `BAAI/bge-small-en-v1.5`; FlashRank local reranker; porter unicode61 FTS5; schema reset to v1; Docker base → python:3.13-slim; README/STATUS/next-session-prompt synced to 3.2.0.
- 2026-04-09: **v3.1.0** — EPIC-053–058 shipped; epic status hygiene sweep (EPIC-040/042/044/050 marked done); roadmap + STATUS + next-session-prompt updated.
- 2026-04-04: Planning **doc sync** — `STATUS` / `next-session-prompt` / `mcp.md` aligned with **044.4** merge undo + CLI-only maintenance commands; snapshot dates refreshed.
- 2026-04-03: **STORY-044.4** — merge **undo** (`undo_consolidation_merge`, `consolidation_merge_undo` audit, CLI **`maintenance consolidation-merge-undo`**); consolidated row **`skip_consolidation=True`**; planning docs + **`CHANGELOG`** [Unreleased].
- 2026-04-03: **EPIC-051** — epic **complete**; **STORY-051.6** / checklist **10.6** [`adr/ADR-006-save-path-observability.md`](adr/ADR-006-save-path-observability.md) (save-phase histograms + `save_phase_summary` + metrics MCP maintained; deeper observability per trigger **(a)**); `features-and-technologies.md` item 6 + section 6 health row; **PLANNING.md** `adr/` in directory tree; **STATUS** / **next-session-prompt** / roadmap gating copy aligned.
- 2026-04-03: **EPIC-051 STORY-051.5** — SQLCipher ops [`adr/ADR-005-sqlcipher-key-backup-operations.md`](adr/ADR-005-sqlcipher-key-backup-operations.md); [`sqlcipher.md`](../guides/sqlcipher.md) — key loss, backup/restore checklist, enterprise KMS note; defer vendor envelope how-tos; `features-and-technologies.md` item 5 + section 2.
- 2026-04-03: **EPIC-051 STORY-051.4** — scale posture [`adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md`](adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md) (single-node SQLite maintained; defer published QPS SLO + mandatory service extraction until evidence); `features-and-technologies.md` section 10 item 4 + section 9; `system-architecture.md` scaling posture link.
- 2026-04-03: **EPIC-051 STORY-051.3** — correctness decision [`adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md`](adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md) (heuristic conflicts + offline review; defer ontology, `needs_review` MVP, MCP review queue until spec + trigger **(c)**); `features-and-technologies.md` section 10 item 3 + section 3 contradiction row updated.
- 2026-04-03: **EPIC-051 STORY-051.2** — freshness decision [`adr/ADR-002-freshness-lazy-decay-vs-ttl.md`](adr/ADR-002-freshness-lazy-decay-vs-ttl.md) (lazy decay + operator GC; defer wall-clock TTL jobs, `maintenance decay-refresh`, daily stale-crossing metrics); `features-and-technologies.md` section 10 item 2 + section 1 stale row updated.
- 2026-04-03: **EPIC-051 STORY-051.1** — maintainer retrieval stack decision [`adr/ADR-001-retrieval-stack.md`](adr/ADR-001-retrieval-stack.md) (embedded BM25 + optional `[vector]` / sqlite-vec + RRF; defer learned sparse, ColBERT, managed vector DB for shipped core); `features-and-technologies.md` section 10 item 1 + section 1 boundaries updated.
- 2026-04-02 (doc+CLI slice): **044.4** — CLI **`maintenance consolidation-threshold-sweep`** (read-only sweep); **044.6** — **`profile_seed_version`** on **`StoreHealthReport`**, **`maintenance health`**, **`run_health_check`**, **`memory://stats`**; **`CHANGELOG`** [Unreleased]; README / guides / engineering docs / epic **`EPIC-044.md`** synced.
- 2026-04-02 (late): **EPIC-044** follow-on slice — **STORY-044.5** GC dry-run report + **`store.gc.archive_bytes`** + health **`gc_*`** + **`archive.jsonl`**; **STORY-044.6** **`SeedingConfig`** / **`profile_seed_version`**; **STORY-044.7** eviction doc in **`data-stores-and-schema.md`**; **STORY-044.4** **`run_consolidation_threshold_sweep`** in **`evaluation.py`**; planning docs (**`STATUS`**, **`next-session-prompt`**, roadmap, feature-tech index) synced.
- 2026-04-02 (late): **STORY-044.4** (partial) — auto-consolidation JSONL audit `consolidation_merge` / `consolidation_source`; `auto_consolidation._persist_consolidated_entry` optional audit kwargs; CLI/MCP audit help; eval threshold sweep in **`evaluation.py`** (CLI surfacing followed in doc+CLI slice same day).
- 2026-04-02 (late): **STORY-044.2** — Bloom nominal FP documentation + `bloom_false_positive_probability` / `BloomFilter.approximate_false_positive_rate`; `normalize_for_dedup` NFKC; `features-and-technologies.md` row updated.
- 2026-04-02 (late): **STORY-044.1** — RAG safety ruleset semver (`SafetyConfig`, `resolve_safety_ruleset_version`); metrics `rag_safety.blocked` / `rag_safety.sanitized`; `StoreHealthReport` rag_safety fields; save/injection wiring; `SafetyCheckResult.ruleset_version`.
- 2026-04-02 (eve): **EPIC-050** residual — WAL **checkpoint** guidance for long-lived MCP: [`sqlite-database-locked.md`](../guides/sqlite-database-locked.md) § *WAL checkpoint*; [`openclaw-runbook.md`](../guides/openclaw-runbook.md) § *Long-lived MCP and SQLite WAL*; `EPIC-050.md` research/themes synced.
- 2026-04-02 (late): **STORY-042.6** — `memory_rerank` / `reranker_failed_fallback_to_original` (latency_ms, provider, candidates_in, top_k, results_out); `MemoryRetriever.last_rerank_stats`, `reranker_provider` ctor arg; `inject_memories` merges `rerank_*` into `injection_telemetry`; `reranker.py` PII note.
- 2026-04-02 (late): **STORY-042.3** — [`sqlite-vec-operators.md`](../guides/sqlite-vec-operators.md) (rebuild/VACUUM, incremental upsert cost, L2 default vs `MATCH` SQL); `sqlite_vec_index.py` / `persistence.py` / `retrieval.py` docstrings; engineering cross-links; sqlite_vec test asserts L2 distance ordering.
- 2026-04-02 (late): **STORY-042.4** — RRF notation + references in `fusion.py`; `profile.HybridFusionConfig` (`top_k_lexical` / `top_k_dense` aliases); `inject_memories` passes hybrid fusion config from real profiles; commented template in `repo-brain.yaml`.
- 2026-04-02 (handoff): Added [`next-session-prompt.md`](next-session-prompt.md) for copy-paste agent continuation; roadmap “Recommended next steps” links it.
- 2026-04-02 (late): **EPIC-042** status sync — **042.1**/**042.5**/**042.7**/**042.8** → **done**; **042.2** → **in_progress** (model card `embedding-model-card.md`, min-max `ScoringConfig.relevance_normalization`); **EPIC-050** **STORY-050.3** → **done** (`connect_sqlite_readonly`, `TAPPS_SQLITE_MEMORY_READONLY_SEARCH`); **EPIC-044.3** note `exclude_key` conflict fix; roadmap “Next engineering” bullets refreshed.
- 2026-04-02 (eve): **STORY-042.2** closed — schema **v17** `embedding_model_id`; `embeddings.py` int8 quantize/dequantize + tests; `MemoryStore.save` sets model id from provider; planning/docs synced.
- 2026-04-02 (late): **Embedding model card** § *Performance review backlog* + `system-architecture.md` cross-link; **STATUS** / **next-session-prompt** / roadmap “recommended next” reordered (**042.3** → **042.4** → **042.6**; **044.3** remainder called out).
- 2026-04-02: **EPIC-042** STORY-042.8 — `docs/guides/memory-decay-and-fsrs.md` (hybrid decay model); `MemoryStore.reinforce` applies `update_stability` when `layer.adaptive_stability`; profile field description aligned; `features-and-technologies.md` link.
- 2026-04-02: **EPIC-042** STORY-042.7 — `InjectionConfig.count_tokens` optional hook; `inject_memories` returns `injection_telemetry` (score-drop, safety-drop, token-budget omit counts, `token_counter` label); ordering documented in `injection.py`.
- 2026-04-02 (eve): **#63** health `retrieval_effective_mode` / `retrieval_summary` + CLI; **#64** `docs/guides/hive-vs-federation.md`; roadmap rows 18–19 `done`; **EPIC-041** STORY-041.3–041.4.
- 2026-04-02 (pm): **#51** / 49-E implemented — `FederatedStore` + `sync_from_hub` + docs; roadmap table row 16 `done`; **EPIC-041**; `epic-49-tasks.md` § 49-E marked shipped.
- 2026-04-02: **#52** product confirmed — prioritized alongside **#51**; recommended next steps + queue item 2 + table row 17 + weekly update.
- 2026-04-02 (eve): **#52** GitHub body checklist reconciled with shipped STORY-041.2; issue **closed**; roadmap + STATUS synced.
- 2026-04-01: **#51** subscriber confirmed; GitHub **#63**, **#64** opened; roadmap queue + table + weekly update synced.
- 2026-03-31: Engineering architecture review — added **Recommended next steps**, **Priority Order** backlog items 3–7, tracking rows **18–22** (no GitHub numbers yet); weekly update 2026-03-31.
- 2026-03-29: **#49** closed on GitHub; **#51** (49-E), **#52** (long-term groups) opened; roadmap + [`epic-49-tasks.md`](epic-49-tasks.md) synced.
- 2026-03-28: **GitHub hygiene** — issue **#49** title/body updated on GitHub (v1 shipped vs backlog); **#46**/**#48**/**#47** already closed with **#50**.
- 2026-03-28: **#49** relay items accept optional `memory_group` / `group`; **49-E** narrowed to
  optional federation hub only ([`epic-49-tasks.md`](epic-49-tasks.md)); roadmap copy updated.
- 2026-03-28: **#49** actionable child spec [`epic-49-tasks.md`](epic-49-tasks.md) (49-A…E); roadmap table links design + tasks.
- 2026-03-28: **#46** OpenClaw `assemble()` / MCP text unwrapping + recall `value` in summaries;
  **#48** tier normalization (`tier_normalize`, store/MCP/relay); **#47** mitigated + doc;
  **#49** design note `design-issue-49-multi-scope-memory.md`; Ruff 0.15.x / mypy CI sweep.
- 2026-03-28: **#21** stale listing + **#20** profile tier migrate shipped (CLI, MCP, store/GC helpers); MCP **63** tools; **#18**, **#40**, **#21**, **#20** closed on GitHub.
- 2026-03-27: Initial roadmap created from open-issue value prioritization.
- 2026-03-27: Marked #30 as in-progress and added first weekly execution update.
- 2026-03-27: Moved **#15** (diagnostics health) to priority 2 and Week 1; renumbered downstream priorities.
- 2026-03-27: **#12** shipped — Hive write revision counter, `hive watch`, MCP `hive_write_revision` / `hive_wait_write`, `.hive_write_notify` sidecar; **#40** unblocked from #30 in roadmap.
- 2026-03-28: **#23** marked `done` in roadmap and tracking table; `.ralph/fix_plan.md` OPEN-ISSUES mirror updated (OR-5 checked); weekly update rewritten to match shipped state; **EPIC-040** epic stub added under `docs/planning/epics/` pointing at fix_plan for full story list.
- 2026-03-28: Closed **#12** and **#23** on GitHub (`completed`); roadmap priority list collapsed to remaining five issues; **#17** marked `closed` in table (was shipped earlier on GitHub).
- 2026-03-28: **#19** sub-agent memory relay shipped (`memory_relay`, `relay import`, `tapps_brain_relay_export`, `docs/guides/memory-relay.md`); GitHub **#19** closed; MCP tool surface **60** (incl. `tapps_brain_session_end` + `tapps_brain_relay_export` in SKILL baseline).
- 2026-03-28: **#40** adaptive hybrid fusion shipped (`fusion.hybrid_rrf_weights_for_query`, weighted RRF, `hybrid_config.adaptive_fusion`); roadmap priority 7 marked `done` (close issue on GitHub after verify).
- 2026-03-28: **#18** hive push / push-tagged shipped (CLI + MCP `hive_push`, batch helpers in `hive.py`); roadmap priority 8 marked `done`; MCP tool count **63** (after **#21**/**#20**).
