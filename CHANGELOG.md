# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [3.7.0] - 2026-04-15

### Added
- connection pool tuning: `max_waiting` (cap queue depth, env `TAPPS_BRAIN_PG_POOL_MAX_WAITING`, default 20) and `max_lifetime` (recycle old connections, env `TAPPS_BRAIN_PG_POOL_MAX_LIFETIME_SECONDS`, default 3600) params on `PostgresConnectionManager` (story-066.7)
- `pool_min`, `pool_max`, `pool_idle` fields on `StoreHealth` and `HiveHealth` for richer `/ready` and `/health` diagnostics (story-066.7)
- live hive pool stats (`pool_size`, `pool_idle`, `pool_waiting`) emitted to `/metrics` Prometheus output (story-066.7)
- `docs/ops/postgres-tde.md` — pg_tde operator runbook covering transparent data encryption setup, key rotation, and emergency key recovery (story-066.10)
- `docs/ops/postgres-backup.md` — Postgres backup and restore runbook with pg_basebackup, WAL archiving, PITR, and verification procedures (story-066.11)
- brain-visual multi-page dashboard: hash-routed navigation with six pages (Overview, Health, Memory, Retrieval, Agents & Hive, Integrity & Export), persistent side-nav, deep-linkable URLs, nav-badge fail counts, and View Transitions API state changes — zero new npm dependencies (EPIC-068)
- brain-visual Integrity & Privacy / Export page: memory export (JSON/CSV), GC controls, contradiction report, privacy audit log, and agent detail drawer (story-068.7)
- 154 new unit tests covering behavioral parity, pg_tde runbook structure, backup runbook structure, docs drift sweep, and Postgres integration test scaffolding (stories 066.9–066.13)

### Changed
- `_collect_metrics` in `http_adapter.py` accepts optional `store` argument to surface live hive pool counters alongside existing DB and OTel metrics (story-066.7)

### Fixed
- brain-visual dashboard: hardcoded hex colour values replaced with CSS custom properties from NLT token source; keyboard navigation audit pass; reduced-motion pass; zero broken doc links (story-068.8)
- pool connection leaks in integration test fixtures (TAP-362)
- `recall`/`remember` tests correctly marked `requires_postgres` after ADR-007 (TAP-363)

## [3.6.0] - 2026-04-15

### Added
- operator-tool separation: `tapps-brain-mcp` (standard, safe for AGENT.md) and `tapps-brain-operator-mcp` (full operator tools, explicit grant required) are now distinct CLI entry points (story-070.9)
- native async parity: explicit `async def` methods on `AsyncMemoryStore` alongside `gc_run` alias; concurrent benchmark test validates throughput (story-070.10)
- `TappsBrainClient` and `AsyncTappsBrainClient` — typed sync/async HTTP network clients with structured error taxonomy, idempotency keys, and automatic retry (story-070.11)
- OTel + Prometheus label enrichment: `project_id`, `agent_id`, `tool`, and `status` labels on all brain counters; bounded cardinality (story-070.12)
- `examples/agentforge_bridge/` — AgentForge BrainBridge reference implementation showing remote-first brain-as-a-shared-service integration pattern (story-070.13)
- `tests/compat/` — embedded AgentBrain v3.5 API parity test suite gated on `TAPPS_BRAIN_DATABASE_URL` (story-070.14)
- CI `compat` job: ephemeral Postgres service container runs `tests/compat/` on every push/PR (story-070.14)
- `--transport {stdio,streamable-http}` flag on both MCP CLI entry points; `TAPPS_BRAIN_MCP_TRANSPORT`, `TAPPS_BRAIN_MCP_HOST`, `TAPPS_BRAIN_MCP_PORT` env overrides; `docker-compose.hive.yaml` adds operator MCP service on port 8090 (story-070.15)

### Fixed
- 7 pre-existing mypy errors in `postgres_migrations.py` (non-null `fetchone` guard), `postgres_connection.py`, `postgres_hive.py`, `project_registry.py`, and `feedback.py` (stale `type: ignore` suppressions removed now that stubs are present)

## [3.5.1] - 2026-04-14

### Fixed
- `AgentBrain.__init__` now honors `TAPPS_BRAIN_PROJECT` when resolving `project_id`, matching `MemoryStore.__init__`. Previously the library's primary entry point unconditionally called `derive_project_id(project_dir)` and bypassed the project registry — every library-path user got a per-directory hash instead of the registered slug. Caught by dogfood after registering the first live tenant. (epic-069)
- `tapps-brain project {register,list,show,approve,delete}` CLI commands crashed with `NameError: name 'os' is not defined` when invoked against a live DSN. `_open_project_registry` was missing the `os` import in its local scope. (epic-069)
- `tests/integration/test_tenant_isolation.py` fixtures — `MemoryProfile(name="repo-brain")` replaced with `get_builtin_profile("repo-brain")` (layers is required), and `PostgresPrivateBackend.get(key)` replaced with `load_all()` filter (no `.get` method exists). 6/6 tests now pass against live Postgres. (story-069.8)

## [3.5.0] - 2026-04-14

### Added
- multi-tenant project registration — `project_profiles` registry table (migration 008), `ProjectRegistry` module, `project_resolver` with `_meta > X-Tapps-Project > TAPPS_BRAIN_PROJECT > "default"` precedence (epic-069, adr-010)
- `tapps-brain project register|list|show|approve|delete` CLI sub-app for profile authoring against a deployed brain (story-069.5)
- HTTP admin surface `GET/POST /admin/projects`, `POST /admin/projects/{id}/approve`, `DELETE /admin/projects/{id}`, gated by `TAPPS_BRAIN_ADMIN_TOKEN` (story-069.5)
- `MemoryStore` honors `TAPPS_BRAIN_PROJECT` env as a human-readable `project_id` slug and consults the project-profile registry before falling back to built-in defaults (story-069.2)
- per-call MCP dispatch — bounded LRU `_StoreCache` keyed by `_meta.project_id` with close-on-evict; `TAPPS_BRAIN_STORE_CACHE_SIZE` env (default 16); stdio path unchanged (story-069.3)
- structured tenant-rejection errors — HTTP 403 `{"error":"project_not_registered","project_id":...}` and JSON-RPC `-32002` with structured `data` payload (story-069.4)
- `project_id` bound into structlog save/recall/feedback contexts; `/snapshot?project=<id>` filter; project dropdown in brain-visual dashboard (story-069.7)
- migration `009_project_rls.sql` enables RLS on `private_memories` (fail-closed — missing `app.project_id` returns zero rows) and `project_profiles` (admin bypass via `app.is_admin='true'`) (story-069.8)
- `PostgresConnectionManager.project_context()` / `admin_context()` using `SET LOCAL` (transaction-scoped) for RLS session vars (story-069.8)
- `tests/integration/test_tenant_isolation.py` — 6 live-Postgres tenant-isolation tests gated on `TAPPS_TEST_POSTGRES_DSN` (story-069.8)
- Agents page with SVG topology diagram + agent-detail drawer (story-068.6)

### Changed
- Profile selection for deployed brains no longer uses filesystem discovery — `.tapps-brain/profile.yaml` is now a seed document consumed by `tapps-brain project register`; in-process `AgentBrain` / `MemoryStore` usage is unchanged (adr-010)
- ADR-009 revisited (2026-04-14): RLS is now shipped on `private_memories` and `project_profiles`, not deferred, now that ADR-010 makes tenancy explicit end-to-end

### Security
- Row-Level Security enabled on private-backend tenanted tables (`private_memories`, `project_profiles`) as defence-in-depth against app-layer filter bugs. A code path that forgets to pass `project_id` now returns zero rows instead of silently leaking another tenant's data. Relies on the application connecting as a non-owner, non-superuser role; migration 009 does NOT set `FORCE ROW LEVEL SECURITY` (matches existing `hive_memories` pattern). (story-069.8)

### Removed
- Demo snapshot fallback in brain-visual dashboard: deleted `brain-visual.demo.json`, the "Load static demo" button, and the "Load snapshot file" manual upload; dashboard is live-only against the `/snapshot` endpoint

## [3.4.0] - 2026-04-12

### Added
- retrieval pipeline live metrics panel (story-065.7)
- add memory velocity panel to dashboard (story-065.6)
- agent registry live table in dashboard (story-065.5)
- Hive hub deep monitoring panel with per-namespace table (story-065.4)
- purge stale and privacy-gated dashboard components (story-065.3)
- dashboard live polling mode (story-065.2)
- add GET /snapshot live endpoint to HttpAdapter (story-065.1)
- add Postgres integration tests replacing deleted SQLite-coupled tests (story-066.13)
- engineering docs drift sweep — zero stale SQLite refs (story-066.12)
- behavioral parity doc + load smoke benchmark (story-066.9)
- auto-migrate private schema on startup via TAPPS_BRAIN_AUTO_MIGRATE=1 (story-066.8)
- connection pool tuning env vars, health JSON pool fields, DSN validation (story-066.7)
- CI workflow with ephemeral Postgres service container (story-066.6)
- bump distribution version strings from 3.2.0 to 3.3.0 (story-066.5)
- GC archive Postgres table (migration 006) (story-066.3)
- bi-temporal as_of filter on PostgresPrivateBackend.search (story-066.2)
- partial — add delete_relations + audit to backends (story-066.1)
- complete SQLite rip-out — Postgres-only persistence plane (stage 2) (adr-007)
- add demo snapshot and Load demo control to brain-visual (story-064.5)
- add deep insight panels — retrieval pipeline, diagnostics, privacy (story-064.4)
- add CSS motion token system with WCAG 2.3.3-compliant reduced-motion gates (story-064.3)
- narrative & IA refresh — decision-first copy, story beats order, microcopy (story-064.2)
- NLT Labs brand audit — gap matrix + fetch path doc (story-064.1)
- add end-to-end OTel integration tests (story-032.10)
- add privacy controls + OTelConfig.capture_content from environment (story-032.9)
- add feedback and diagnostics OTel span events (story-032.7+032.8)
- add tapps_brain.* custom metrics + export hook (story-032.6)
- add standard GenAI + MCP metrics via GenAIMetricsRecorder (story-032.5)
- add non-retrieval OTel spans (delete, reinforce, save) (story-032.4)
- retrieval document events + MCP params._meta traceparent extraction (story-032.3)
- add GenAI semconv v1.35.0 MCP tool call spans (story-032.2)
- add OTelConfig, HAS_OTEL flag, and bootstrap_tracer() (story-032.1)
- CI epic validation gate + regression runbook (story-062.7+062.8)
- canonical env-var contract + .env.example (story-062.5+062.6)
- gate operator/maintenance MCP tools behind --enable-operator-tools flag (story-062.4)
- freeze MCP core tool list and regenerate manifest (story-062.3)
- strict startup — clean stderr + non-zero exit + not-for-prod docs (story-062.2)
- add unit tests for _get_store Hive backend wiring from unified DSN (story-062.1)
- scope audit matrix doc and code checklist (story-063.5+063.6)
- RLS benchmark script + ADR-009 ship decision (story-063.4)
- RLS spike — namespace isolation on hive_memories (story-063.3)
- add least-privilege DB roles migration and runbook (story-063.1+063.2)
- add MemoryBodyRedactionFilter log handler and OTel metric Views (story-061.7)
- K8s liveness/readiness probe docs + explicit liveness test (story-061.4/061.5)
- metrics gauges, error counters, pool stats, bounded label policy (story-061.2)
- add OTel trace spans to remember/recall/search/hive hot paths (story-061.1)
- rewrite agentforge-integration.md for v3 Postgres DSN (story-060.7+060.8)
- ADR-008 no HTTP without MCP parity + CODEOWNERS guardrails (story-060.5+060.6)
- HTTP adapter optional routes, auth middleware, and OpenAPI spec (story-060.4)
- add minimal HTTP adapter with /health, /ready, /metrics (story-060.3)
- add typed exception taxonomy + v3 breaking changes docs (story-060.2)
- Compose, Makefile, and AGENTS.md onboarding for v3 Postgres dev workflow (story-059.7)
- DSN table, pool idle timeout, pool saturation + migration version in health JSON (story-059.7)
- behavioral parity doc + concurrent-agent load smoke (story-059.6)
- private memory integration tests — round-trip save/recall with N entries (story-059.5)
- private memory Postgres schema + migrations (story-059.4)
- no silent SQLite in runtime + v3 doc sweep (story-059.3)
- remove SQLite hive/federation; move AgentRegistration/AgentRegistry to models/backends (story-059.2)
- add edge-case tests for Postgres-only backend factories (story-059.1)
- remove SQLite backends, add Postgres-only factory and CI (epic-059)
- add tapps-visual nginx service for brain-visual frontend (docker)
- visual snapshot PNG capture with Playwright + scorecard branch coverage (STORY-048.6)
- doc validation strict mode + pluggable lookup engine guide (EPIC-048.5)
- complete stories 048.1–048.4 (session, relations, markdown, eval) (EPIC-048)
- temporal query filtering + consolidation threshold profile-config (#70/#71)
- implement EPIC-053–058 — per-agent brains, Postgres Hive, unified API, Docker deployment

### Changed
- stage-delete scorecard-derive.js missed in prior commit (story-065.3)
- add Postgres backup and restore runbook (story-066.11)
- pg_tde 2.1.2 operator runbook (story-066.10)
- full suite runs at deployment only — never during ralph loops (ralph)
- remove premature QA gates — all testing deferred to 066.14 (ralph)
- session continuity, team mode, effort scaling by task size (ralph)
- raise maxTurns 50→100 for main agent and architect (ralph)
- speed optimizations — stop loop, harden deferred-QA rule (ralph)
- check off 066.1 — 5 consolidation audit tests fixed (story-066.1)
- completed tasks delete from fix_plan, append to archive (ralph)
- archive completed tasks to fix_plan_archive.md (ralph)
- shrink fix_plan to story pointers only — was 11k tokens (ralph)
- reorder fix_plan — EPIC-066 (bug fixes) before EPIC-065 (new feature) (ralph)
- update PROMPT.md for EPIC-065/066 campaign (ralph)
- enable agent mode, bump effort, tighten timeout (ralph)
- WIP private backend, Ralph state, planning updates (checkpoint)
- add EPIC-065 live always-on dashboard epic with 7 stories (065)
- bump to v3.3.0 — Docker infrastructure rebuild (release)
- doc + a11y + MCP gate — EPIC-064 complete (064.CLEAN)
- add "See it in action" CTA and cross-links for brain-visual dashboard (064.6)
- fix mcp.md doc drift — add 6 undocumented core tools, remove phantom tool (062.CLEAN)
- manual security scan + doc cross-ref validation (063.CLEAN)
- add negative scope-enforcement tests (story-063.7)
- add STRIDE threat model one-pager for v3.0 (story-063.8)
- add operator observability runbook with alert examples (story-061.8)
- add telemetry policy doc and PR template review slot (story-061.6)
- check off already-implemented trace context propagation task (061.3)
- add ADR-007/008 to doc index, fix broken db-roles link (060.CLEAN)
- refresh agent-integration guide with full AgentBrain API surface (story-060.1)
- sweep stale SQLite references from docs and source docstrings (059.CLEAN)
- check off story-059.2 in fix_plan (ralph)
- Merge branch 'worktree-agent-a030f3aa'
- use uv sync --group dev; Ralph setup verified (dev)
- Claude MCP for tapps/docs, fix_plan cleanup tasks, roadmap v3 queue (ralph)
- refine ADR-007, greenfield epics EPIC-032/059-063, CLAUDE backend note (planning)
- add v3 greenfield epics and fix review findings (planning)
- add index, contributing, llms.txt; fix internal links and IDE config
- sync docs and Docker to v3.2.0 (release)
- sync all engineering docs to EPIC-053–058 architecture (v3.1.0)
- add agentforge-integration.md — generic guide for connecting projects
- bump version to 3.2.0, finalize CHANGELOG for EPIC-048
- epic status hygiene sweep — mark EPIC-040/042/044/050/053-058 done (planning)
- add EPIC-053–058 — per-agent brains, Postgres Hive, unified API
- bump version to v3.1.0
- phase 11 — replace Cohere reranker with FlashRank local cross-encoder
- phases 7/10/12 — env var docs, embedding model upgrade, SQLite best practices
- execute phases 5-6 — remove sigmoid normalization + collapse schema migrations
- execute phases 3-4 — formalize core deps + remove backwards compat
- reduce GitHub Actions cost — drop cross-platform from PRs, add caching + concurrency
- execute phases 1-2 — dead code removal + dependency updates
- v2.2.0 — sqlite-vec promoted to core, async wrapper fixes + tests

### Fixed
- resolve all 136 unit test failures — zero failures achieved (066.14)
- enable tapps-mcp permissions + upgrade to v2.4.0 (ralph)
- enable operator tools in GC/consolidation MCP test fixtures (story-066.4)
- resolve 18 ruff errors across OTel and HTTP adapter files (lint)
- OTel code quality + span names in architecture doc (061.CLEAN)
- fix remaining test files importing deleted SQLite modules (story-059.2)
- update test expectations for STORY-048.1 and STORY-048.2 (tests)
- install from local wheel + psycopg, fix entrypoint duplication (docker)
- explicitly disable embedding provider in no-embedding test (test)
- additional pre-existing test failures from full suite run
- quality gate — ruff, mypy, format, and pre-existing test failures

## [2.1.0] - 2026-04-06

### Changed
- v2.1.0 — async API, PA extraction, procedural tier, temporal filtering, profile consolidation

## [2.0.4] - 2026-04-05

### Added
- operator docs, observability, verify-integrity CLI (epic-043/045/046/047/049)
- offline save-conflict export; docs: ADR-001-006 and planning sync (044)
- merge undo, per-group entry caps, docs sync (epic-044)
- consolidation sweep CLI, seed version on health/stats, docs sync (epic-044)
- GC metrics, consolidation sweep, seeding version, eviction docs (epic-044)
- embeddings v17, hybrid profile RRF, RO sqlite, conflict exclude_key (epic-042,044,050)
- decay/FSRS decision doc and reinforce stability (epic-042.8)
- injection tokenizer hook and telemetry (epic-042.7)
- align composite scoring weight validation and docs (epic-042.5)
- SQLite busy tuning, locked runbook, lexical retrieval (epic-050, epic-042)
- save-path phase latency histograms for observability (store)
- Hive group agent_scope, recall union, and test alignment (story-041.2)
- engineering Phase 2 (#55-62) (docs,federation,mcp)
- carry publisher memory_group through hive propagation (closes #51) (hive)

### Changed
- v2.0.4 — EPIC-052 code review sweep fixes + doc sync
- add EPIC-052 full codebase code review sweep (planning)
- troubleshoot provenance warning (#65) (openclaw)
- expand help coverage and document help keys (brain-visual)
- refresh next-session prompt with prioritized next slices (planning)
- help pills for Hive, Entries, DB tiles and guide notes (brain-visual)
- record GitHub #52 reopened for checklist alignment (planning)
- sync roadmap after closing GitHub #52 #63 #64 (#51 already closed) (planning)
- close EPIC-041 loop, refresh roadmap, document concurrency (planning)
- add features-and-technologies map and link from architecture (engineering)
- align EPIC-042-051 stories with tests and verification (planning)
- sync CLAUDE, Cursor rules, Ralph AGENT with v16 + manifest (ai)
- remove mem0-review vendored tree

### Fixed
- 2026-Q2 code review sweep — write-through consistency + hygiene (epic-052)

## [2.0.3] - 2026-03-30

### Added
- recall diagnostics, agent integration, OpenClaw capture
- optional memory_group on relay import; plan 49-E federation-only (relay)
- project-local memory_group (schema v16, retrieval, MCP/CLI) (#49)
- GC stale listing and profile tier migrate (#21, #20)
- adaptive hybrid fusion (#40) and hive batch push (#18)
- sub-agent memory relay import/export (GitHub #19) (relay)
- optional SQLCipher at-rest and planning sync (encryption)
- session summarization — CLI, Python API, and MCP tool (#17)
- write notifications, hive watch, MCP poll (#12) (hive)
- sqlite-vec index, health sqlite-vec fields, profile onboarding MCP (week1-2)

### Changed
- tapps-brain v2.0.3 — version and OpenClaw manifest alignment (release)
- restore ≥95% gate for Linux/Python 3.12 (coverage)
- v2.0.2 — changelog, STATUS, OpenClaw manifests (release)
- close epic #49; track backlog #51 and #52 (planning)
- bump to v2.0.1 (PyPI, plugin, manifests) (release)
- sync roadmap and fix_plan with GitHub issue closures (planning)
- feature intake governance, GitHub templates, and agent rules
- update uv.lock
- check off 040.22 in fix_plan

### Fixed
- update stale schema version and entry limit assertions (v15→v16, 500→5000) (tests)
- Merge pull request #50 from wtthornton/fix/openclaw-tier-normalize-ci
- MCP tool text unwrap; feat(store): tier normalization (openclaw)
- singleton McpClient — one MCP process per workspace, not per session (plugin)
- add SIGTERM/SIGINT handler to prevent stray MCP process leak (plugin)
- profile-aware tier validation in MCP memory_save (closes #16) (story-022)

## [2.0.0] - 2026-03-25

### Added
- Groups as first-class Hive layer — create, manage, search across groups (GitHub #37) (040.21)
- per-entry conflict detection on save (GitHub #44) (040.16)
- PageRank scoring for memory relationship graphs (GitHub #33) (040.15)
- Louvain community detection for smarter consolidation (GitHub #36) (040.13)
- tapps-brain openclaw init/upgrade commands (GitHub #26) (040.20)
- assemble() injects memory recall nudge (GitHub #27) (040.19)
- periodic mid-session memory flush every N messages (GitHub #25) (040.18)
- flush recentMessages on dispose() — prevent session context loss (GitHub #24) (040.17)
- write deduplication with Bloom filter fast-path (GitHub #31) (040.14)
- TextRank conversation summarization — no LLM required (GitHub #32) (040.12)
- RAKE keyword extraction for automatic key generation (GitHub #42) (040.11)
- enhanced 6-signal composite scoring formula (GitHub #41) (040.8)
- stability-based promotion/demotion strategy (GitHub #39) (040.7)
- Bayesian confidence updates — learn from actual usage (GitHub #35) (040.6)
- adaptive stability schema + FSRS-style stability updates (GitHub #28) (040.5)
- memory health stats CLI command (GitHub #43) (040.4)
- temporal fact validity — valid_from/valid_until columns, query filtering, historical support (GitHub #29) (040.3)
- add provenance metadata columns — source_session_id, source_channel, source_message_id, triggered_by (GitHub #38) (040.2)
- switch BM25 to BM25+ variant with lower-bound delta (GitHub #34) (040.1)

### Changed
- tapps-brain v2.0.0 — research-driven upgrades (EPIC-040) (release)
- check off 040.21 in fix_plan.md
- check off 040.16 in fix_plan.md
- check off 040.15 in fix_plan.md
- check off 040.13 in fix_plan.md
- check off 040.20 in fix_plan.md
- check off 040.19 in fix_plan.md
- check off 040.18 in fix_plan
- check off 040.17 in fix_plan.md
- check off 040.14 in fix_plan.md
- check off 040.12 in fix_plan.md
- check off 040.11 in fix_plan.md
- check off 040.8 in fix_plan.md
- check off 040.7 in fix_plan.md
- check off 040.6 in fix_plan.md
- check off 040.5 in fix_plan.md
- check off 040.4 in fix_plan
- mark 040.3 complete
- mark 040.2 complete
- mark 040.1 complete

### Fixed
- resolve tool name conflicts, tier fallback, hive status counts (#9, #11, #22)

## [1.4.3] - 2026-03-25

### Added
- recalibrate profile limits based on research benchmarks (v1.4.2)
- replace custom MCP client with official @modelcontextprotocol/sdk (epic-039)
- realign OpenClaw plugin with real SDK, remove dead compat layers (epic-037-038)

### Changed
- bump tapps-brain to v1.4.3 (release)
- fix stale references after EPIC-039 SDK transport migration
- bump tapps-brain to v1.4.0 (release)

### Fixed
- add ephemeral and session tiers to MemoryTier enum
- normalize message.content and improve logging (fixes #8, #10) (openclaw-plugin)
- eliminate top-level require("openclaw") crash (openclaw-plugin)
- harden f-string SQL and replace silent exception swallowing (v1.4.1)
- add all optional ContextEngine methods to ambient types (openclaw-sdk)
- bump minimumVersion, remove stale toolGroups schema, accept bootstrap params (openclaw-plugin)
- fix BootstrapResult field name and compact param types (epic-039)

## [1.3.1] - 2026-03-24

### Added
- QA gate, OpenClaw docs, release automation (epic-034-036)
- diagnostics scorecard, v10 history, MCP/CLI, QA fixes (EPIC-030)
- MCP/CLI feedback tools, Hive propagation, integration test (story-029)
- implicit feedback reformulation and correction detection (story-029.3)
- implicit positive/negative feedback tracking (story-029.3)
- add explicit feedback API to MemoryStore (story-029.2)
- add FeedbackConfig with custom event types and strict validation (story-029.2)
- add FeedbackEvent model, FeedbackStore, and v8→v9 migration (story-029.1a)
- fix migration script to read config.plugins.entries/installs (story-033.4)
- import SDK types and fix API drift in openclaw plugin (story-033.1,033.2,033.3)
- per-agent tool routing and permissions (story-027.8)
- expose MCP resources and prompts as OpenClaw tools (story-027.7)
- register federation tools as OpenClaw native tools (story-027.2)
- register maintenance and config tools (story-027.4)
- register audit, tags, profile tools (story-027.5)
- register knowledge graph tools as OpenClaw native tools (story-027.3)
- register Hive tools as OpenClaw native tools (story-027.1)
- register lifecycle tools as OpenClaw native tools (story-027.6)
- memory-core migration tool (story-026.5)
- bidirectional MEMORY.md sync (story-026.4)
- register tapps-brain as OpenClaw memory slot plugin (story-026.1)
- add OpenClaw version compatibility layer (story-028.6)
- integrate session memory search (story-028.5)
- add citation support to recall results (story-028.4)
- add MCP client auto-reconnection (story-028.1)
- source trust multipliers for per-source scoring (M2)
- add Hive awareness to OpenClaw agents, integrity hashing, and rate limiting

### Changed
- bump tapps-brain to v1.3.1 (release)
- note 41-tool historical scope vs 54 tools today (epic-027)
- reconcile EPIC-034/035/036 and story statuses (planning)
- update STATUS.md — mark EPIC-017–028 done, add missing epics (HK-002.1)
- close resolved GitHub issues #4, #5, #6 (HK-001.1)
- Git-only install and upgrade guide (openclaw)
- v1.3.0 — flywheel/eval, docs, OpenClaw sync
- fix_plan roadmap for EPIC-029 QA through EPIC-032 (ralph)
- add unit tests for FeedbackStore.record/query (story-029.1b)
- add EPICs 029-032 for feedback, diagnostics, flywheel, and OTel
- prune fix_plan.md — all 94 tasks complete
- mark EPICs 017-028 as done with all stories checked off
- add epic planning docs and Ralph runtime artifacts
- fix pre-existing ruff lint and format violations
- complete tool reference and integration guide (story-027.9)
- integration tests for OpenClaw memory replacement (story-026.6)
- mark 026-B and 026-C as done (already implemented in 026-A commit)
- comprehensive OpenClaw integration guide (story-028.8)
- add TypeScript tests for ContextEngine (story-028.3)
- add TypeScript tests for MCP client (story-028.3)
- configuration and manifest files review (story-025.7)
- OpenClaw TypeScript plugin review (story-025.6)
- test infrastructure and benchmarks review (story-025.5)
- remaining integration tests review (story-025.4)
- federation, cross-profile, validation integration tests review (story-025.3)
- OpenClaw, profile, Hive integration tests review (story-025.2)
- MCP and retrieval integration tests review (story-025.1)
- remaining small unit tests review (story-024.14)
- trust, consolidation config, decay, BM25 tests review (story-024.13)
- contradictions, models, GC, relations tests review (story-024.12)
- markdown, reranker, embeddings tests review (story-024.11)
- foundation, promotion, IO tests review (story-024.10)
- concurrency and recall tests review (story-024.9)
- similarity and safety tests review (story-024.8)
- consolidation tests review (story-024.7)
- profile and retrieval tests review (story-024.6)
- federation and hive tests review (story-024.5)
- coverage gaps and validation tests review (story-024.4)
- store and persistence tests review (story-024.3)
- test_cli.py review (story-024.2)
- test_mcp_server.py review (story-024.1)
- auto-reformat 10 files with ruff format
- fix pre-existing lint/format issues from prior epic reviews
- metrics and OTel review (story-023.3)
- profile YAML files review (story-023.2)
- profile.py review (story-023.1)
- markdown_import.py review (story-022.7)
- io.py import/export review (story-022.6)
- cli.py advanced commands review (story-022.5)
- cli.py core commands review (story-022.4)
- mcp_server.py config and agent tools review (story-022.3)
- mcp_server.py Hive and graph tools review (story-022.2)
- mcp_server.py core tools review (lines 1–500) (story-022.1)
- relations.py knowledge graph review (story-021.4)
- hive.py registry and propagation review (story-021.3)
- hive.py HiveStore core review (story-021.2)
- federation.py cross-project review (story-021.1)
- rate limiter review (story-020.5)
- seeding bootstrap review (story-020.4)
- contradictions detection review (story-020.3)
- doc_validation.py review (story-020.2)
- safety and injection defense review (story-020.1)
- reinforcement and extraction review (story-019.5)
- GC and promotion review (story-019.4)
- auto_consolidation.py lifecycle review (story-019.3)
- consolidation.py merging review (story-019.2)
- decay.py exponential decay review (story-019.1)
- embeddings and reranker review (story-018.5)
- similarity computation review (story-018.4)
- BM25 and fusion scoring review (story-018.3)
- recall.py orchestration review (story-018.2)
- retrieval.py scoring engine review (story-018.1)
- integrity verification review (story-017.8)
- audit and session index review (story-017.7)
- protocols and feature flags review (story-017.6)
- __init__.py public API review (story-017.5)
- models.py data model review (story-017.4)
- persistence.py SQLite layer review (story-017.3)
- store.py advanced features review (story-017.2)
- style and quality cleanup from prior review loops
- store.py core CRUD review (story-017.1)
- verify updated consolidation thresholds in repo-brain profile
- sync fix_plan.md — mark BUG-001-B/C/D/E/G complete
- v1.2.0 — modernize README, update docs, bump version

### Fixed
- optional SDK imports for mypy; CliRunner; WSL/Windows venv note
- fix openclaw-plugin test failures — defensive agent guard + test mocks (033-QA)
- resolve plugin load failures and missing migration path (openclaw-plugin)
- add structured error logging to OpenClaw plugin (story-028.7)
- resolve bootstrap race condition in OpenClaw plugin (story-028.2)
- update integrity hash computation for new model fields
- update schema version assertions from v7 to v8
- inject_memories respects profile scoring weights (BUG-002-B)
- thread scoring_config through inject_memories to prevent source trust regression
- narrow exception handling in MCP Hive tools
- include server.json in version consistency check
- log warning on unknown tier fallback in decay
- prevent HiveStore connection leak on MCP handler exceptions
- restore type safety in decay_config_from_profile
- select_tier handles custom profile tier priorities
- rewrite OpenClaw plugin against real ContextEngine API (v2026.3.7)
- update openclaw-plugin/plugin.json version to 1.2.0

## [1.1.0] - 2026-03-22

### Added
- agent lifecycle and Hive stats (story-015.9)
- auto-consolidation config MCP tools and CLI (story-015.8)
- GC config MCP tools and CLI (story-015.7)
- tag management CLI commands (story-015.6)
- tag management MCP tools (story-015.5)
- audit trail CLI command (story-015.4)
- audit trail MCP tool (story-015.3)
- knowledge graph CLI commands (story-015.2)
- knowledge graph MCP tools (story-015.1)
- graceful SQLite corruption handling (story-014.3)
- CLI agent create command (story-014.2)
- validate agent_scope enum values (story-014.1)
- deploy v1.8.7 performance optimizations (ralph)
- OpenClaw plugin agent identity and Hive config (story-013.6)
- agent_create composite MCP tool (story-013.5)
- hive_propagate uses server agent identity (story-013.4)
- Hive tools reuse shared HiveStore (story-013.4)
- source_agent parameter in memory_save (story-013.3)
- agent_scope parameter in memory_save (story-013.2)
- MCP server --agent-id and --enable-hive flags (story-013.1)
- ClawHub skill directory (story-012.6)
- pyproject.toml metadata for PyPI (story-012.6)
- pre-compaction compact hook (story-012.5)
- auto-capture afterTurn hook (story-012.4)
- auto-recall ingest hook (story-012.3)
- bootstrap hook with MCP spawn (story-012.2)
- openclaw plugin skeleton (story-012.2)
- daily note import and workspace importer (story-012.1)
- markdown import parser (story-012.1)
- implement Hive — multi-agent shared brain with domain namespaces (EPIC-011)
- add configurable memory profiles with pluggable layers and scoring (EPIC-010)
- add EPICs 010-012 for configurable profiles, hive, and OpenClaw
- expose session index, search, and capture as MCP tools
- optional OpenTelemetry exporter (story-007.5)
- store.audit() convenience method (story-007.3)
- instrument lifecycle operation metrics (story-007.2)
- instrument save/get/search metrics (story-007.2)
- MCP registry server.json (story-009.4)
- entry points and unified version (story-009.3)
- optional extras for cli and mcp (story-009.1)
- curated __all__ and py.typed (story-009.2)
- merge relations on consolidation (story-006.5)
- transfer relations on supersede (story-006.5)
- graph-based recall boost (story-006.4)
- query_relations filter API (story-006.3)
- find_related graph traversal (story-006.3)
- load relations on cold start (story-006.2)
- auto-extract relations on save/ingest (story-006.2)
- relation persistence methods (story-006.1)
- MCP protocol-level integration tests (story-008.7)
- federation & maintenance MCP tools (story-008.5)
- MCP prompts, console script entry point, fix_plan update (story-008.6)
- schema v6, store health/metrics, MCP deps and tests
- add MCP server interfaces and tighten Ralph task execution (epic-008)
- implement bi-temporal fact versioning with validity windows (epic-004)
- implement auto-recall orchestrator with capture pipeline (epic-003)
- wire standalone modules into MemoryStore runtime — 839 tests, 97.17% coverage (epic-002)
- raise test suite to A+ — 792 tests, 96.59% coverage (epic-001)

### Changed
- final validation and status update (epic-016)
- unicode and boundary value tests (story-016.6)
- concurrent GC and Hive stress tests (story-016.4)
- concurrent save and recall stress tests (story-016.3)
- CLI gc archive and agent create error tests (story-016.2)
- CLI federation command tests (story-016.1)
- final validation and status update (epic-015)
- final validation and status update (epic-014)
- CHANGELOG.md (story-014.5)
- getting started guide (story-014.4)
- add EPIC-015 — Analytics & Operational Surface (graph, audit, tags, GC, consolidation)
- fix grep stat parsing in hooks, strengthen QA-skip rules, add mypy ignores
- add EPIC-014 — hardening (validation, CLI parity, resilience, onboarding docs)
- mark EPIC-013 complete — update all status markers, acceptance criteria, and tool count
- remove stale noqa E501 directive in test_mcp_server.py
- final validation and status update (epic-013)
- sync Ralph config updates and EPIC-013 test/formatting artifacts
- multi-agent Hive round-trip integration tests (story-013.8)
- multi-agent Hive patterns in OpenClaw guide (story-013.7)
- add EPIC-013 — Hive-aware MCP surface for multi-agent OpenClaw wiring
- add Profile Design Guide, Hive Guide, Profile Catalog; rewrite README
- sync pending doc and config updates
- mark EPIC-012 complete — update all status markers and acceptance criteria
- deploy Ralph v1.2.0 hooks, agents, and skills
- clean orphaned temp files, add feedback report, update gitignore
- final validation and status update (epic-012)
- ClawHub submission guide (story-012.6)
- PyPI publish checklist (story-012.6)
- version consistency check (story-012.6)
- openclaw guide with ContextEngine plugin (story-012.7)
- recall capture round-trip integration (story-012.7)
- markdown import integration tests (story-012.7)
- markdown import unit tests (story-012.1)
- add Ralph hooks, Claude Code project config, and updated tooling
- break EPIC-011 into Ralph tasks and update project status
- sync epic statuses — mark EPICs 005-010 stories done, check all acceptance criteria
- mark EPICs 006-009 done, add EPIC-010 tasks to fix_plan
- add MCP protocol integration tests and fix persistence method call
- add configurable memory profiles and hive architecture design
- add OpenClaw integration guide and deployment plan
- rewrite README in polished GitHub style
- update all docs for session index, search, and capture MCP tools
- bump Ralph timeout and document JSONL crash bug
- observability integration tests (story-007.6)
- extras-aware test markers (story-009.5)
- add ralph runtime files to gitignore, stage pending changes
- graph lifecycle integration tests (story-006.6)
- tune for Max Plan unattended operation (ralph)
- configure for unattended overnight runs (ralph)
- MCP server guide with client config examples (story-008)
- sync fix_plan with completed work, require checkoffs (ralph)
- Ralph setup guide, WSL scripts, optimized .ralphrc settings
- default integrated terminal to WSL Ubuntu (workspace) (vscode)
- add WSL Claude Code upgrade script (user-local npm) (scripts)
- add WSL background Ralph launcher and PS1 wrapper (scripts)
- add WSL Ralph setup scripts and CLAUDE.md notes
- add explicit done-when criteria to fix plan (ralph)
- add Ralph autonomous loop configuration
- bump version to 1.1.0, fix lint and format issues
- update README for EPIC-003/004, fix mypy error, plan EPICs 005-007
- add planned EPIC-003 (auto-recall) and EPIC-004 (bi-temporal versioning)
- update README, PLANNING.md for Epic 2 completion
- mark all acceptance criteria complete after CI pass (epic-001)
- fix ruff lint errors (TC001, F841, E501, I001)

### Fixed
- close leaked SQLite connections in tests (story-016.5)
- repair Ralph WSL/Windows version divergence and on-stop hook parsing
- reinforce STATUS rules with scenarios and CRITICAL note (ralph)
- use IN_PROGRESS status for completed tasks with remaining work (ralph)
- run background Ralph in tmux (survives WSL exit) (scripts)

## [1.0.1] - 2026-03-19

### Changed
- run ruff format on all files for CI compliance
- remove PyPI publish workflow (private repo, not needed)

### Fixed
- use venv for build job, add .gitattributes for LF enforcement (ci)
- resolve all ruff, mypy, and formatting issues for CI

## [1.0.0] - 2026-03-19

### Added
- initial tapps-brain v1.0.0 - standalone memory system

### Changed
- add PyPI publish workflow via OIDC trusted publishing
