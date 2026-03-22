---
id: EPIC-012
title: "OpenClaw integration — ContextEngine plugin and ClawHub skill"
status: done
priority: high
created: 2026-03-21
target_date: 2026-06-15
tags: [openclaw, contextengine, clawhub, mcp, integration]
---

# EPIC-012: OpenClaw Integration — ContextEngine Plugin and ClawHub Skill

## Context

tapps-brain can already serve OpenClaw as an MCP server (documented in `docs/guides/openclaw.md`). But this is a sidecar integration — the agent has to explicitly call memory tools. OpenClaw's ContextEngine plugin system (v2026.3.7) allows tapps-brain to *replace* the built-in memory entirely, injecting recalled memories before each turn and capturing facts after each response automatically.

This epic has two tracks:
1. **ContextEngine plugin** — deep integration that makes tapps-brain the default brain for an OpenClaw agent
2. **ClawHub skill** — packaging and publishing for one-command install by the OpenClaw community

This depends on EPIC-010 (profiles, so each OpenClaw agent can use a different profile) and benefits from EPIC-011 (Hive, so multiple OpenClaw agents share knowledge).

## Success Criteria

- [x] ContextEngine plugin implements `bootstrap`, `ingest`, `afterTurn`, `compact` hooks
- [x] Auto-recall injects relevant memories before each turn (no explicit tool call needed)
- [x] Auto-capture extracts facts from agent responses after each turn
- [x] Pre-compaction flush saves important context before OpenClaw compresses the context window
- [x] MEMORY.md import migrates existing OpenClaw memories into tapps-brain on first run
- [x] ClawHub skill published with one-command install
- [x] PyPI package published and installable via `pip install tapps-brain[mcp]`

## Stories

### STORY-012.1: Markdown import — migrate existing MEMORY.md

**Status:** done
**Effort:** M
**Depends on:** EPIC-010 (STORY-010.3)
**Context refs:** `src/tapps_brain/io.py`, `src/tapps_brain/store.py`
**Verification:** `pytest tests/unit/test_markdown_import.py -v --cov=tapps_brain.markdown_import --cov-report=term-missing`

#### Why

OpenClaw users have existing `MEMORY.md` and `memory/*.md` files containing accumulated knowledge. A smooth migration path is essential — users won't adopt tapps-brain if they lose their existing memories. This must work on first run, silently importing existing knowledge.

#### Acceptance Criteria

- [x] New `src/tapps_brain/markdown_import.py` module
- [x] `import_memory_md(path: Path, store: MemoryStore) -> int` — parses MEMORY.md, creates entries, returns count imported
- [x] Parses markdown structure: headings become keys (slugified), body becomes value
- [x] Detects and imports `memory/YYYY-MM-DD.md` daily note files as `context`-tier entries with appropriate dates
- [x] Deduplication: skips entries whose keys already exist in the store
- [x] Tier inference: heading-level heuristic — H1/H2 → `architectural`, H3 → `pattern`, H4+ → `procedural`, daily notes → `context`
- [x] `import_openclaw_workspace(workspace_dir: Path, store: MemoryStore) -> dict` — imports both MEMORY.md and memory/*.md, returns counts
- [x] Unit test: import a sample MEMORY.md, verify entries created with correct tiers
- [x] Unit test: import twice, verify no duplicates
- [x] Unit test: import daily notes with date extraction

---

### STORY-012.2: ContextEngine plugin skeleton

**Status:** done
**Effort:** M
**Depends on:** STORY-012.1
**Context refs:** `src/tapps_brain/mcp_server.py`, `docs/guides/openclaw.md`
**Verification:** manual test with OpenClaw (plugin loads and registers)

#### Why

The ContextEngine plugin is the deepest integration point with OpenClaw. It replaces the built-in memory system entirely. This story creates the plugin structure and implements the `bootstrap` hook — the entry point that runs when an OpenClaw session starts.

#### Acceptance Criteria

- [x] New `openclaw-plugin/` directory at repo root with plugin structure
- [x] `openclaw-plugin/plugin.json` — plugin manifest with ContextEngine slot registration
- [x] `openclaw-plugin/src/index.ts` — TypeScript entry point that spawns `tapps-brain-mcp` as a child process
- [x] `bootstrap` hook: opens MemoryStore, imports MEMORY.md if first run, runs initial `recall()` for session primer
- [x] Plugin reads `--project-dir` from OpenClaw workspace path
- [x] Plugin respects profile from `{workspace}/.tapps-brain/profile.yaml`
- [x] README with install instructions: `openclaw plugin install ./openclaw-plugin`
- [x] Manual test: install plugin in local OpenClaw, verify it loads and bootstrap runs

---

### STORY-012.3: Auto-recall via `ingest` hook

**Status:** done
**Effort:** M
**Depends on:** STORY-012.2
**Context refs:** `src/tapps_brain/recall.py`, `src/tapps_brain/injection.py`
**Verification:** manual test with OpenClaw (memories injected before each turn)

#### Why

This is the primary user-facing feature. Before each agent turn, tapps-brain searches for relevant memories and injects them into the context. The agent doesn't need to explicitly call any memory tool — recall happens automatically.

#### Acceptance Criteria

- [x] `ingest` hook receives the user's message text
- [x] Calls `memory_recall(message)` via MCP to get ranked memories
- [x] Injects the `memory_section` into the context window as a system-level prefix
- [x] Respects token budget from profile (`recall.default_token_budget`)
- [x] Skips injection if no relevant memories found (empty `memory_section`)
- [x] Deduplication: tracks keys already injected in this session to avoid repeating them
- [x] Latency: recall + injection completes in <500ms for typical stores (<500 entries)
- [x] Manual test: ask OpenClaw about something previously stored, verify it recalls without explicit tool call

---

### STORY-012.4: Auto-capture via `afterTurn` hook

**Status:** done
**Effort:** S
**Depends on:** STORY-012.2
**Context refs:** `src/tapps_brain/recall.py`
**Verification:** manual test with OpenClaw (facts extracted after agent response)

#### Why

Auto-recall is half the loop. The other half is capturing new facts from the agent's response and persisting them. Without capture, the memory store grows stale. The `afterTurn` hook runs after each agent response, making it the natural capture point.

#### Acceptance Criteria

- [x] `afterTurn` hook receives the agent's response text
- [x] Calls `memory_capture(response)` via MCP to extract and persist facts
- [x] Only captures if response contains decision-like statements (delegates to existing extraction logic)
- [x] Rate-limited: captures at most once every 3 turns to avoid flooding the store
- [x] Logs captured keys for observability
- [x] Manual test: have OpenClaw make a decision ("let's use Redis for caching"), verify it's captured as a memory entry

---

### STORY-012.5: Pre-compaction flush via `compact` hook

**Status:** done
**Effort:** M
**Depends on:** STORY-012.2
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/extraction.py`
**Verification:** manual test with OpenClaw (memories saved before context compaction)

#### Why

OpenClaw's killer feature is the pre-compaction memory flush — before the context window is compressed, important information is saved. Without this hook, tapps-brain loses context that the agent hasn't explicitly saved. This is the #1 gap identified in the OpenClaw comparison.

#### Acceptance Criteria

- [x] `compact` hook receives the full conversation context about to be compacted
- [x] Calls `memory_ingest(context)` via MCP to extract durable facts
- [x] Extracts session summary and indexes it via `memory_index_session(session_id, chunks)`
- [x] Session ID derived from OpenClaw session/conversation identifier
- [x] Only extracts from the portion of context being compacted (not already-persisted memories)
- [x] Manual test: have a long OpenClaw conversation, verify memories are saved when compaction triggers

---

### STORY-012.6: PyPI publish and ClawHub skill packaging

**Status:** done
**Effort:** M
**Depends on:** STORY-012.2
**Context refs:** `pyproject.toml`, `docs/planning/DEPLOY-OPENCLAW.md`
**Verification:** `pip install tapps-brain[mcp]` from PyPI; `openclaw skill install tapps-brain-memory` from ClawHub

#### Why

Distribution is what turns a working integration into adoption. PyPI publish makes `pip install tapps-brain[mcp]` work globally. ClawHub publish makes `openclaw skill install tapps-brain-memory` a one-command setup.

#### Acceptance Criteria

- [x] `pyproject.toml` updated with `project.urls` (homepage, repository, documentation, changelog)
- [x] `uv build` produces clean wheel and sdist
- [x] Published to PyPI: `pip install tapps-brain[mcp]` works
- [x] `openclaw-skill/` directory with `SKILL.md` (YAML frontmatter + instructions) and `openclaw.plugin.json`
- [x] SKILL.md declares all 21 MCP tools, triggers, capabilities, permissions
- [x] `openclaw.plugin.json` auto-configures MCP server on install
- [x] Submitted to ClawHub registry (PR to `github.com/openclaw/clawhub` or `openclaw skill publish`)
- [x] Version consistency check: pyproject.toml version = SKILL.md version = plugin.json version
- [x] End-to-end test: fresh OpenClaw install → `openclaw skill install tapps-brain-memory` → "remember X" → "recall X" works

---

### STORY-012.7: Integration tests and documentation

**Status:** done
**Effort:** S
**Depends on:** STORY-012.3, STORY-012.4, STORY-012.5
**Context refs:** `docs/guides/openclaw.md`, `tests/integration/`
**Verification:** `pytest tests/integration/test_openclaw_integration.py -v`

#### Why

The OpenClaw integration involves multiple hooks, external process communication, and Markdown import. Integration tests validate the wiring. Documentation updates ensure the guide reflects the ContextEngine plugin (not just MCP sidecar).

#### Acceptance Criteria

- [x] Integration test: Markdown import — create a mock MEMORY.md with headings and content, import into store, verify entries with correct tiers
- [x] Integration test: Markdown import idempotency — import twice, verify no duplicates
- [x] Integration test: recall + capture round-trip — save a memory, recall it via orchestrator, capture a response containing new facts, verify new entries created
- [x] `docs/guides/openclaw.md` updated with ContextEngine plugin instructions alongside MCP sidecar instructions
- [x] Documentation covers: install, bootstrap, auto-recall, auto-capture, pre-compaction, profile switching, Hive integration
- [x] Overall coverage stays at 95%+

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | 012.1 — Markdown import | M | Migration path: must work before anyone switches |
| 2 | 012.2 — Plugin skeleton | M | Foundation for all hooks |
| 3 | 012.3 — Auto-recall (ingest) | M | Primary user value: automatic memory injection |
| 4 | 012.4 — Auto-capture (afterTurn) | S | Completes the recall→capture loop |
| 5 | 012.5 — Pre-compaction flush | M | Prevents memory loss on context compression |
| 6 | 012.6 — PyPI + ClawHub publish | M | Distribution: makes adoption possible |
| 7 | 012.7 — Integration tests + docs | S | Validation and polish |

## Dependency Graph

```
EPIC-010.3 (profiles wired)
    │
    └──→ 012.1 (markdown import) → 012.2 (plugin skeleton) ──┬──→ 012.3 (auto-recall) ──┐
                                                               ├──→ 012.4 (auto-capture)  ├──→ 012.7 (tests + docs)
                                                               ├──→ 012.5 (compaction)  ──┘
                                                               └──→ 012.6 (publish)
```

## Testability Checkpoints

| After Story | What You Can Test |
|-------------|-------------------|
| 012.1 | Import an existing MEMORY.md into tapps-brain, verify entries |
| 012.2 | Install plugin in OpenClaw, verify it loads and bootstraps |
| 012.3 | Ask OpenClaw a question, see recalled memories in the response |
| 012.5 | Have a long conversation, verify memories survive compaction |
| 012.6 | `pip install tapps-brain[mcp]` from PyPI works globally |
