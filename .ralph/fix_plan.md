# Ralph Fix Plan — tapps-brain

Aligned with the repo as of **2026-03-22**. For full story text, see `docs/planning/epics/EPIC-*.md`.

**Task sizing:** Each item is scoped to ONE Ralph loop (~15 min). Do one, check it off, commit.

## Completed Epics

- [x] EPIC-001: Test suite quality — A+ (done)
- [x] EPIC-002: Integration wiring (done)
- [x] EPIC-003: Auto-recall orchestrator (done)
- [x] EPIC-004: Bi-temporal fact versioning (done)
- [x] EPIC-005: CLI tool (done)
- [x] EPIC-006: Knowledge Graph (done)
- [x] EPIC-007: Observability (done)
- [x] EPIC-008: MCP Server (done)
- [x] EPIC-009: Multi-Interface Distribution (done)
- [x] EPIC-010: Configurable Memory Profiles (done — 14 tasks, all checked)
- [x] EPIC-011: Hive — Multi-Agent Shared Brain (done — 14 tasks, all checked)
- [x] EPIC-012: OpenClaw Integration (done — 17 tasks, all checked)
- [x] EPIC-013: Hive-Aware MCP Surface (done — 10 tasks, all checked)
- [x] EPIC-015: Analytics & Operational Surface (done — 10 tasks, all checked)

## Completed — EPIC-012: OpenClaw Integration

**Depends on:** EPIC-010 ✅, EPIC-011 ✅
**Target:** 2026-06-15
**Design:** `docs/planning/epics/EPIC-012.md`

**Goal:** ContextEngine plugin for OpenClaw with auto-recall/capture hooks, pre-compaction flush, Markdown import, PyPI publish, and ClawHub skill packaging.

### Phase 1: Markdown Import (Python — sequential)

#### 012-A: Markdown import module — parser core
- [x] Create `src/tapps_brain/markdown_import.py` with `import_memory_md(path, store) -> int`. Parse markdown headings into keys (slugified), body into values. Tier inference from heading levels: H1/H2 → architectural, H3 → pattern, H4+ → procedural. Deduplication by key. Commit: `feat(story-012.1): markdown import parser`

#### 012-B: Daily note import and workspace importer
- [x] Add `import_openclaw_workspace(workspace_dir, store) -> dict` to `markdown_import.py`. Parse `memory/YYYY-MM-DD.md` daily notes as context-tier entries with date extraction from filename. Return counts: `memory_md`, `daily_notes`, `skipped`. Commit: `feat(story-012.1): daily note import and workspace importer`

#### 012-C: Markdown import unit tests
- [x] Unit tests: import sample MEMORY.md with H1-H4 headings → correct tiers. Import twice → no duplicates. Daily note date extraction. Edge cases: empty files, malformed markdown, missing MEMORY.md. Commit: `test(story-012.1): markdown import unit tests`

### Phase 2: OpenClaw Plugin Skeleton (TypeScript)

#### 012-D: OpenClaw plugin directory and manifest
- [x] Create `openclaw-plugin/` directory: `plugin.json` (ContextEngine slot), `package.json`, `tsconfig.json`, `README.md`. Minimal TypeScript skeleton in `src/index.ts` that exports hook stubs. Commit: `feat(story-012.2): openclaw plugin skeleton`

#### 012-E: Bootstrap hook — spawn MCP and first-run import
- [x] Implement `bootstrap` hook in `src/index.ts`: spawn `tapps-brain-mcp` as child process, import MEMORY.md on first run via `memory_import` MCP tool, run initial `recall()` for session primer. Read `--project-dir` from OpenClaw workspace path. Commit: `feat(story-012.2): bootstrap hook with MCP spawn`

### Phase 3: OpenClaw Hooks (TypeScript — parallel after 012-E)

#### 012-F: Auto-recall via ingest hook
- [x] Implement `ingest` hook in `src/index.ts`: receive user message, call `memory_recall(message)` via MCP, inject `memory_section` into context as system prefix, respect token budget, track injected keys for dedup within session. Commit: `feat(story-012.3): auto-recall ingest hook`

#### 012-G: Auto-capture via afterTurn hook
- [x] Implement `afterTurn` hook in `src/index.ts`: receive agent response, call `memory_capture(response)` via MCP. Rate limit: max once every 3 turns (turn counter in plugin state). Log captured keys. Commit: `feat(story-012.4): auto-capture afterTurn hook`

#### 012-H: Pre-compaction flush via compact hook
- [x] Implement `compact` hook in `src/index.ts`: receive context being compacted, call `memory_ingest(context)` + `memory_index_session(session_id, chunks)` via MCP. Session ID from OpenClaw session identifier. Only process non-persisted context. Commit: `feat(story-012.5): pre-compaction compact hook`

### Phase 4: Integration Tests (Python)

#### 012-I: Markdown import integration tests
- [x] Integration tests with real SQLite: import mock MEMORY.md with multiple heading levels, verify entries with correct tiers. Idempotency: import twice, no duplicates. Daily notes with real date extraction. File in `tests/integration/test_openclaw_integration.py`. Commit: `test(story-012.7): markdown import integration tests`

#### 012-J: Recall + capture round-trip integration test
- [x] Integration test: save memory → recall via RecallOrchestrator → capture response with new facts → verify new entries created. Tests the full loop that ContextEngine hooks exercise. Commit: `test(story-012.7): recall capture round-trip integration`

### Phase 5: Documentation

#### 012-K: OpenClaw documentation update
- [x] Update `docs/guides/openclaw.md` with ContextEngine plugin instructions alongside existing MCP sidecar docs. Cover: install, bootstrap, auto-recall, auto-capture, pre-compaction, profile switching, Hive integration. Commit: `docs(story-012.7): openclaw guide with ContextEngine plugin`

### Phase 6: Distribution & Publishing

#### 012-L: pyproject.toml metadata for PyPI
- [x] Add `project.urls` (homepage, repository, documentation, changelog) to `pyproject.toml`. Verify `uv build` produces clean wheel and sdist. Test install from wheel works. Commit: `feat(story-012.6): pyproject.toml metadata for PyPI`

#### 012-M: ClawHub skill directory and SKILL.md
- [x] Create `openclaw-skill/` with `SKILL.md` (YAML frontmatter: all MCP tools, triggers, capabilities, permissions) and `openclaw.plugin.json` (auto-configures MCP server). Commit: `feat(story-012.6): ClawHub skill directory`

#### 012-N: Version consistency check
- [x] Add unit test in `tests/unit/test_version_consistency.py` that verifies version string matches across `pyproject.toml`, `openclaw-skill/SKILL.md`, `openclaw-plugin/package.json`, and `openclaw-skill/openclaw.plugin.json`. Commit: `test(story-012.6): version consistency check`

#### 012-O: PyPI publish preparation
- [x] Create `scripts/publish-checklist.md` documenting manual PyPI publish process. Verify install from wheel works end-to-end: `pip install dist/*.whl && tapps-brain --version && tapps-brain-mcp --help`. Commit: `docs(story-012.6): PyPI publish checklist`

#### 012-P: ClawHub submission preparation
- [x] Create `openclaw-skill/README.md` for ClawHub listing. Document submission process in `docs/guides/clawhub-submission.md`. Verify skill directory matches ClawHub schema requirements. Commit: `docs(story-012.6): ClawHub submission guide`

### Phase 7: Final Validation

#### 012-Q: Final validation and STATUS.md update
- [x] Run full test suite, verify coverage >= 95%. Run lint and type checks. Update `docs/planning/STATUS.md` to mark EPIC-012 done. Update `__init__.py` exports if new public API surfaces were added. Commit: `chore(epic-012): final validation and status update`

## Completed — EPIC-013: Hive-Aware MCP Surface

**Depends on:** EPIC-011 ✅, EPIC-012 ✅
**Target:** 2026-07-15
**Design:** `docs/planning/epics/EPIC-013.md`

**Goal:** Wire Hive agent identity and scope propagation through the MCP server and OpenClaw plugin so orchestrators can create agents with unique profiles sharing a Hive.

### Phase 1: MCP Server Hive Wiring (Python — sequential)

#### 013-A: MCP server CLI flags — `--agent-id` and `--enable-hive`
- [x] Add `--agent-id <id>` and `--enable-hive` arguments to MCP server argparse. When `--enable-hive` is set, instantiate `HiveStore()` and pass it + `hive_agent_id` to `MemoryStore`. Store resolved agent ID and HiveStore on the server so all tools can access them. Backward compatible: no flags = identical to today. Add unit test confirming store receives `hive_store` and `hive_agent_id` when flags are set. Commit: `feat(story-013.1): MCP server --agent-id and --enable-hive flags`

#### 013-B: Expose `agent_scope` in `memory_save` MCP tool
- [x] Add `agent_scope: str = "private"` parameter to `memory_save`. Pass through to `store.save()`. When Hive is enabled and scope is `"domain"` or `"hive"`, the store's `_propagate_to_hive()` handles propagation automatically. Add unit test with mocked HiveStore verifying propagation triggers. Commit: `feat(story-013.2): agent_scope parameter in memory_save`

#### 013-C: Expose `source_agent` in `memory_save` MCP tool
- [x] Add `source_agent: str = ""` parameter to `memory_save`. When empty, fall back to server's `--agent-id` (or `"unknown"`). Pass through to `store.save(source_agent=...)`. Add unit test verifying both explicit and fallback paths. Commit: `feat(story-013.3): source_agent parameter in memory_save`

### Phase 2: Hive Tools Refactor (Python)

#### 013-D: Hive tools reuse server's HiveStore instance
- [x] Refactor `hive_status`, `hive_search`, `hive_propagate`, `agent_register`, `agent_list` to reuse the server's shared `HiveStore` when available, instead of creating throwaway instances per call. When `--enable-hive` is not set, fall back to creating a temporary `HiveStore` (current behavior). Add unit tests for both paths. Commit: `feat(story-013.4): Hive tools reuse shared HiveStore`

#### 013-E: `hive_propagate` uses server's agent identity
- [x] Update `hive_propagate` to read agent_id from the store's `_hive_agent_id` instead of hardcoded `"mcp-user"`. Read profile from the store's resolved profile instead of defaulting separately. Add unit test verifying correct agent_id flows through. Commit: `feat(story-013.4): hive_propagate uses server agent identity`

### Phase 3: Composite Tool (Python)

#### 013-F: `agent_create` composite MCP tool
- [x] Add `agent_create` MCP tool: (1) register agent in AgentRegistry with profile and skills, (2) validate profile exists (built-in or project), (3) return namespace assignment and profile summary. Invalid profile returns error with available profiles listed. Add unit test for happy path and invalid profile. Commit: `feat(story-013.5): agent_create composite MCP tool`

### Phase 4: OpenClaw Plugin (TypeScript)

#### 013-G: OpenClaw plugin — `agentId` and `hiveEnabled` config
- [x] Add `agentId` and `hiveEnabled` fields to plugin `plugin.json` config schema. Update bootstrap hook to pass `--agent-id` and `--enable-hive` flags to MCP spawn. Auto-call `agent_register` on first run with configured agent ID and profile. Omitting config fields preserves current behavior. Commit: `feat(story-013.6): OpenClaw plugin agent identity and Hive config`

### Phase 5: Documentation & Testing

#### 013-H: OpenClaw guide — multi-agent Hive patterns
- [x] Add "Multi-Agent Hive" section to `docs/guides/openclaw.md`. Cover: orchestrator creating child agents, profile inheritance (base + extends), agent scope usage (private/domain/hive), example `plugin.json` for orchestrator and child agents. Include shared-profile and per-role-profile scenarios. Commit: `docs(story-013.7): multi-agent Hive patterns in OpenClaw guide`

#### 013-I: Integration tests — multi-agent Hive round-trip
- [x] Integration test with real SQLite: two agents with different profiles sharing a Hive. Save with different `agent_scope` values, verify propagation and recall merging. Test conflict resolution across agents. Agent A `hive` scope → B can recall. Agent A `private` → B cannot see. Agent A `domain` same profile as B → B can recall. File: `tests/integration/test_hive_mcp_roundtrip.py`. Commit: `test(story-013.8): multi-agent Hive round-trip integration tests`

#### 013-J: Final validation and status update
- [x] Run full test suite, verify coverage >= 95%. Run lint and type checks. Update EPIC-013 status to done. Update this fix_plan. Commit: `chore(epic-013): final validation and status update`

## Active — EPIC-014: Hardening — Validation, Parity, Resilience, Docs

**Depends on:** EPIC-013 ✅
**Target:** 2026-08-01
**Design:** `docs/planning/epics/EPIC-014.md`

**Goal:** Close five high-value gaps: input validation, CLI parity, DB resilience, onboarding docs, and release tracking.

### Phase 1: Validation & Parity (all independent)

#### 014-A: Validate `agent_scope` enum in MCP and store
- [x] Add validation in `memory_save` MCP tool and `MemoryStore.save()`: reject values not in `{"private", "domain", "hive"}` with clear error listing valid options. Add unit test for valid and invalid values. Commit: `feat(story-014.1): validate agent_scope enum values`

#### 014-B: CLI `agent create` command
- [x] Add `agent create` subcommand to CLI mirroring MCP `agent_create`: register agent with profile validation, print namespace and profile summary. Reuse `AgentRegistry` and `profile.py`. Add unit test for happy path and invalid profile. Commit: `feat(story-014.2): CLI agent create command`

### Phase 2: Resilience

#### 014-C: Graceful SQLite corruption handling
- [x] Wrap `MemoryPersistence.__init__()` / `MemoryStore.__init__()` with try/except for `sqlite3.DatabaseError`. Log actionable message: `"Database corrupt: {path}. Back up and delete to recover."` Store still raises but error is clear. Add unit test with corrupted DB file. Commit: `feat(story-014.3): graceful SQLite corruption handling`

### Phase 3: Documentation

#### 014-D: Getting Started guide
- [x] Create `docs/guides/getting-started.md` mapping use cases to interfaces (Library / CLI / MCP). Quick example for each (3-5 lines). Link from README Quick Start section. Commit: `docs(story-014.4): getting started guide`

#### 014-E: CHANGELOG.md
- [x] Create `CHANGELOG.md` (keepachangelog.com format). v1.1.0: EPIC-013 features. v1.0.0: EPICs 001–012 summary. Link from README and pyproject.toml. Commit: `docs(story-014.5): CHANGELOG.md`

#### 014-F: Final validation and status update
- [x] Run full test suite, verify coverage >= 95%. Run lint and type checks. Update EPIC-014 status to done. Update this fix_plan. Commit: `chore(epic-014): final validation and status update`

## Completed — EPIC-015: Analytics & Operational Surface

**Depends on:** EPIC-014
**Target:** 2026-09-01
**Design:** `docs/planning/epics/EPIC-015.md`

**Goal:** Expose hidden analytics (knowledge graph, audit trail, tags) and operational controls (GC thresholds, auto-consolidation config, agent lifecycle) through MCP and CLI so production teams can inspect, tune, and debug their memory stores.

### Phase 1: Knowledge Graph Exposure (MCP → CLI)

#### 015-A: Knowledge graph MCP tools — relations, find_related, query_relations
- [x] Add 3 MCP tools: `memory_relations(key)`, `memory_find_related(key, max_hops=2)`, `memory_query_relations(subject, predicate, object_entity)`. All delegate to existing store methods. Return JSON. Add unit tests. Commit: `feat(story-015.1): knowledge graph MCP tools`

#### 015-B: Knowledge graph CLI commands — relations, related
- [x] Add CLI commands: `memory relations <key>` and `memory related <key> --hops 2`. Table output with `--format json` option. Add unit tests. Commit: `feat(story-015.2): knowledge graph CLI commands`

### Phase 2: Audit Trail Exposure

#### 015-C: Audit trail MCP tool
- [x] Add `memory_audit(key, event_type, since, until, limit=50)` MCP tool delegating to `store.audit()`. Return JSON array of events. Add unit test. Commit: `feat(story-015.3): audit trail MCP tool`

#### 015-D: Audit trail CLI command
- [x] Add `memory audit [key] --type save --since 2026-01-01 --limit 20` CLI command. Table output with `--format json`. Add unit test. Commit: `feat(story-015.4): audit trail CLI command`

### Phase 3: Tag Management

#### 015-E: Tag management MCP tools — list_tags, update_tags, entries_by_tag
- [x] Add 3 MCP tools: `memory_list_tags()`, `memory_update_tags(key, add, remove)`, `memory_entries_by_tag(tag, tier)`. Add `store.update_tags(key, add, remove)` method for atomic tag modification. Add unit tests. Commit: `feat(story-015.5): tag management MCP tools`

#### 015-F: Tag management CLI commands
- [x] Add CLI commands: `memory tags` (list all with counts) and `memory tag <key> --add tag1 --remove tag2`. Add unit tests. Commit: `feat(story-015.6): tag management CLI commands`

### Phase 4: Operational Controls (all independent)

#### 015-G: GC config MCP tools and CLI
- [x] Add MCP tools: `memory_gc_config()` and `memory_gc_config_set(floor_retention_days, session_expiry_days, contradicted_threshold)`. Add CLI: `maintenance gc-config [--set key=value]`. Make GC accept runtime config updates. Add unit tests. Commit: `feat(story-015.7): GC config MCP tools and CLI`

#### 015-H: Auto-consolidation config MCP tools and CLI
- [x] Add MCP tools: `memory_consolidation_config()` and `memory_consolidation_config_set(enabled, threshold, min_entries)`. Add CLI: `maintenance consolidation-config [--set key=value]`. Delegate to existing `store.set_consolidation_config()`. Add unit tests. Commit: `feat(story-015.8): auto-consolidation config MCP tools and CLI`

#### 015-I: Agent lifecycle — delete, CLI parity, Hive stats
- [x] Add `agent_delete(agent_id)` MCP tool with `AgentRegistry.unregister()`. Add CLI: `agent list` and `agent delete <id>`. Add per-namespace entry counts to `hive_status` output. Add unit tests. Commit: `feat(story-015.9): agent lifecycle and Hive stats`

### Phase 5: Final Validation

#### 015-J: Final validation and status update
- [x] Run full test suite, verify coverage >= 95%. Run lint and type checks. Update EPIC-015 status to done. Update this fix_plan. Commit: `chore(epic-015): final validation and status update`

## Active — EPIC-016: Test Suite Hardening

**Depends on:** EPIC-015 ✅
**Target:** 2026-04-15
**Design:** `docs/planning/epics/EPIC-016.md`

**Goal:** Close testing gaps: untested CLI commands, missing concurrency tests, resource leaks, and unicode/boundary edge cases.

### Phase 1: CLI Coverage Gaps (independent)

#### 016-A: CLI federation command tests — subscribe, unsubscribe, publish
- [x] Add unit tests for `federation subscribe` (happy path + non-existent dir error). Add unit tests for `federation unsubscribe` (happy path + unknown project error). Add unit tests for `federation publish` (happy path). Use Click CliRunner with isolated tmp dirs. Commit: `test(story-016.1): CLI federation command tests`

#### 016-B: CLI maintenance gc archive path and agent create error path
- [x] Add unit test for `maintenance gc` non-dry-run: create stale entries, run gc, verify entries archived. Add unit test for `agent create` with invalid profile: verify error message lists available profiles. Commit: `test(story-016.2): CLI gc archive and agent create error tests`

### Phase 2: Concurrency Tests (sequential)

#### 016-C: Concurrent save and recall stress tests
- [x] Create `tests/unit/test_concurrent.py`. Test: 10 threads saving 50 entries each — all 500 persisted, no corruption. Test: 5 threads saving while 5 threads recalling — no exceptions. Test: concurrent save at max capacity (500) — eviction correct under contention. 30-second timeout on all tests. Commit: `test(story-016.3): concurrent save and recall stress tests`

#### 016-D: Concurrent GC and Hive stress tests
- [x] In `test_concurrent.py`, add: GC running while saves happen — no exceptions, archive consistent. Multiple agents propagating to HiveStore concurrently — all entries arrive. Concurrent recall from Hive during propagation — no exceptions. 30-second timeout. Commit: `test(story-016.4): concurrent GC and Hive stress tests`

### Phase 3: Resource Cleanup (independent)

#### 016-E: Fix unclosed SQLite connections in tests
- [ ] Run `pytest tests/ -W error::ResourceWarning` to identify all offending tests. Add proper teardown (store.close(), connection.close()) to affected fixtures and tests. Verify zero ResourceWarning with strict warnings enabled. Commit: `fix(story-016.5): close leaked SQLite connections in tests`

### Phase 4: Edge Cases (independent)

#### 016-F: Unicode and boundary value tests
- [ ] Create `tests/unit/test_edge_cases.py`. Tests: emoji in key/value (save + recall + FTS search). CJK characters (save + recall). Mixed RTL/LTR text. Key at exactly MAX_KEY_LENGTH — accepted. Key at MAX_KEY_LENGTH+1 — rejected. Value at MAX_VALUE_LENGTH — accepted. Value at MAX_VALUE_LENGTH+1 — rejected. Commit: `test(story-016.6): unicode and boundary value tests`

### Phase 5: Final Validation

#### 016-G: Final validation and status update
- [ ] Run full test suite, verify coverage >= 95%. Run lint and type checks. Verify zero ResourceWarning. Update EPIC-016 status to done. Update STATUS.md with new test count. Update this fix_plan. Commit: `chore(epic-016): final validation and status update`

## Notes

- **One task per loop.** Each task is sized for ~15 min. If a task is too large, split it and check off the part you finished.
- **Dependency graph (EPIC-014):** All tasks 014-A through 014-E are independent. 014-F last.
- **Dependency graph (EPIC-015):** 015-A → 015-B. 015-C → 015-D. 015-E → 015-F. 015-G, 015-H, 015-I independent. 015-J last.
- **Dependency graph (EPIC-016):** 016-A, 016-B, 016-E, 016-F independent. 016-C → 016-D. 016-G last.
- Always cross-check the relevant epic file before starting a task.
- Maintain **95%** test coverage; run full lint / type / test suite before committing.
- After completing a task, update this file: change `- [ ]` to `- [x]`.
