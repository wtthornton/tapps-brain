# Ralph Fix Plan — tapps-brain

Aligned with the repo as of **2026-03-21**. For full story text, see `docs/planning/epics/EPIC-*.md`.

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

## Active — EPIC-013: Hive-Aware MCP Surface

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
- [ ] Integration test with real SQLite: two agents with different profiles sharing a Hive. Save with different `agent_scope` values, verify propagation and recall merging. Test conflict resolution across agents. Agent A `hive` scope → B can recall. Agent A `private` → B cannot see. Agent A `domain` same profile as B → B can recall. File: `tests/integration/test_hive_mcp_roundtrip.py`. Commit: `test(story-013.8): multi-agent Hive round-trip integration tests`

#### 013-J: Final validation and status update
- [ ] Run full test suite, verify coverage >= 95%. Run lint and type checks. Update EPIC-013 status to done. Update this fix_plan. Commit: `chore(epic-013): final validation and status update`

## Notes

- **One task per loop.** Each task is sized for ~15 min. If a task is too large, split it and check off the part you finished.
- **Dependency graph (EPIC-013):** 013-A → 013-B, 013-C (memory_save params). 013-A → 013-D, 013-E (Hive tools refactor). 013-A + 013-F (agent_create). 013-F → 013-G (OpenClaw plugin). 013-G → 013-H (docs). 013-B + 013-C + 013-D → 013-I (integration tests). 013-J last.
- Always cross-check **`docs/planning/epics/EPIC-013.md`** before starting a task.
- Maintain **95%** test coverage; run full lint / type / test suite before committing.
- After completing a task, update this file: change `- [ ]` to `- [x]`.
