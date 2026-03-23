---
id: EPIC-026
title: "OpenClaw Memory Replacement — replace memory-core with tapps-brain"
status: done
priority: high
created: 2026-03-23
target_date: 2026-05-15
tags: [openclaw, memory-core, integration, plugin]
---

# EPIC-026: OpenClaw Memory Replacement — Replace memory-core with tapps-brain

## Context

EPIC-012 delivered a ContextEngine plugin that adds auto-recall and auto-capture hooks.
But OpenClaw's built-in `memory-core` plugin still owns the `memory` slot — its
`memory_search` and `memory_get` tools read from plain Markdown files (`MEMORY.md` +
`memory/*.md`), completely bypassing tapps-brain. The result: **two parallel memory
systems** that don't talk to each other. Telegram/Discord queries show 0 memories
because they hit OpenClaw's markdown-backed memory, not tapps-brain's SQLite store.

This epic makes tapps-brain the **sole memory provider** by:
1. Registering as the `memory` slot plugin (not just `contextEngine`)
2. Replacing `memory_search` and `memory_get` with tapps-brain-backed implementations
3. Adding bidirectional MEMORY.md sync so tools that read markdown still work
4. Providing migration and fallback for users switching from memory-core

Depends on EPIC-012 (done).

## Success Criteria

- [x] `plugins.slots.memory = "tapps-brain-memory"` disables memory-core and routes all memory calls through tapps-brain
- [x] OpenClaw's `memory_search` tool returns results from tapps-brain's BM25/FTS5 index
- [x] OpenClaw's `memory_get` tool returns entries from tapps-brain's SQLite store
- [x] MEMORY.md is kept in sync: tapps-brain writes → markdown export; markdown edits → tapps-brain import
- [x] Daily note files (`memory/YYYY-MM-DD.md`) are synced on session start
- [x] Existing memory-core users can migrate with zero data loss
- [x] Integration tests validate the full replacement works end-to-end

## Stories

### STORY-026.1: Register as memory slot plugin

**Status:** done
**Effort:** M
**Depends on:** EPIC-012.2 (plugin skeleton)
**Context refs:** `openclaw-plugin/src/index.ts`, `openclaw-plugin/openclaw.plugin.json`
**Verification:** `openclaw plugins inspect tapps-brain-memory` shows both `contextEngine` and `memory` slots

#### Why

The ContextEngine plugin only hooks into context assembly and compaction. OpenClaw's
`memory_search` and `memory_get` are provided by whatever plugin owns the `memory` slot
(default: `memory-core`). Until tapps-brain claims this slot, those tools bypass it.

#### Acceptance Criteria

- [x] `openclaw.plugin.json` declares both `kind: "context-engine"` and `slots.memory`
- [x] Plugin's `register()` calls `api.registerTool("memory_search", ...)` backed by tapps-brain
- [x] Plugin's `register()` calls `api.registerTool("memory_get", ...)` backed by tapps-brain
- [x] When `plugins.slots.memory = "tapps-brain-memory"`, OpenClaw's built-in `memory_search`/`memory_get` are replaced
- [x] When memory slot is not claimed, falls back gracefully (plugin still works as contextEngine only)
- [x] Unit test: mock plugin API, verify both tools are registered

---

### STORY-026.2: Implement memory_search tool replacement

**Status:** done
**Effort:** M
**Depends on:** STORY-026.1
**Context refs:** `openclaw-plugin/src/index.ts`, `src/tapps_brain/mcp_server.py`
**Verification:** manual test — ask OpenClaw to search memories, verify results come from tapps-brain

#### Why

OpenClaw's `memory_search` does semantic search over ~400-token markdown chunks.
tapps-brain's `memory_search` uses FTS5 with BM25 ranking over SQLite. The replacement
must return results in the format OpenClaw's agent expects (snippet text, file path,
line range, relevance score) while actually querying tapps-brain.

#### Acceptance Criteria

- [x] `memory_search` tool registered via plugin API accepts the same parameters as memory-core's version
- [x] Calls tapps-brain's `memory_search` MCP tool under the hood
- [x] Returns results formatted as OpenClaw expects: `{ snippets: [{ text, path, lineRange, score }] }`
- [x] Maps tapps-brain fields: `value` → `text`, `key` → synthetic `path`, `confidence` → `score`
- [x] Handles empty results gracefully (returns empty array, not error)
- [x] Respects OpenClaw's `memorySearch.query.hybrid` config when applicable
- [x] Performance: <200ms for typical queries against stores with <500 entries

---

### STORY-026.3: Implement memory_get tool replacement

**Status:** done
**Effort:** S
**Depends on:** STORY-026.1
**Context refs:** `openclaw-plugin/src/index.ts`, `src/tapps_brain/mcp_server.py`
**Verification:** manual test — OpenClaw retrieves a specific memory entry via memory_get

#### Why

OpenClaw's `memory_get` reads specific Markdown files by workspace-relative path. When
tapps-brain owns the memory slot, `memory_get` should retrieve entries by key from the
SQLite store instead.

#### Acceptance Criteria

- [x] `memory_get` tool registered via plugin API
- [x] Accepts a key/path parameter; extracts the memory key from path if needed (e.g., `memory/my-key.md` → `my-key`)
- [x] Calls tapps-brain's `memory_get` MCP tool
- [x] Returns the entry value as Markdown text (matching memory-core's format)
- [x] Returns empty string (not error) for missing keys (matching memory-core's graceful degradation)
- [x] Supports optional line-range parameters (returns full entry if no range specified)

---

### STORY-026.4: Bidirectional MEMORY.md sync

**Status:** done
**Effort:** L
**Depends on:** STORY-026.1
**Context refs:** `src/tapps_brain/markdown_import.py`, `src/tapps_brain/io.py`
**Verification:** `pytest tests/integration/test_markdown_sync.py -v`

#### Why

Many tools and humans read `MEMORY.md` directly. If tapps-brain saves a memory but
doesn't update the markdown file, those tools see stale data. Conversely, if a human
edits `MEMORY.md`, tapps-brain should pick up the change. Bidirectional sync prevents
the two representations from diverging.

#### Acceptance Criteria

- [x] New `src/tapps_brain/markdown_sync.py` module
- [x] `sync_to_markdown(store, workspace_dir)` — exports all tapps-brain entries to `MEMORY.md`, organized by tier
- [x] `sync_from_markdown(store, workspace_dir)` — re-imports `MEMORY.md` + `memory/*.md`, updating changed entries and adding new ones
- [x] Deduplication: entries with matching keys are updated, not duplicated
- [x] Conflict resolution: tapps-brain entry wins if both modified since last sync (tapps-brain is source of truth)
- [x] Sync timestamp tracked in `.tapps-brain/sync_state.json`
- [x] ContextEngine plugin calls `sync_from_markdown()` during `bootstrap`
- [x] ContextEngine plugin calls `sync_to_markdown()` during `compact` (alongside existing memory_ingest)
- [x] Daily notes (`memory/YYYY-MM-DD.md`) included in sync
- [x] Integration test: save via tapps-brain → verify in MEMORY.md; edit MEMORY.md → verify in tapps-brain

---

### STORY-026.5: Migration tool for memory-core users

**Status:** done
**Effort:** M
**Depends on:** STORY-026.4
**Context refs:** `src/tapps_brain/markdown_import.py`, `src/tapps_brain/cli.py`
**Verification:** `tapps-brain openclaw migrate --dry-run` shows what would be imported

#### Why

Existing OpenClaw users have months of accumulated knowledge in `MEMORY.md` and
`memory/*.md` files. A smooth, zero-data-loss migration from memory-core to
tapps-brain is essential for adoption.

#### Acceptance Criteria

- [x] CLI command: `tapps-brain openclaw migrate [--workspace DIR] [--dry-run]`
- [x] Imports all `MEMORY.md` content with tier inference (H1/H2→architectural, H3→pattern, H4+→procedural)
- [x] Imports all `memory/YYYY-MM-DD.md` daily notes as `context`-tier entries
- [x] Imports memory-core's SQLite index if it exists (`~/.openclaw/memory/<agentId>.sqlite`)
- [x] `--dry-run` shows what would be imported without making changes
- [x] Reports: entries imported, duplicates skipped, errors encountered
- [x] Idempotent: running twice produces no duplicates
- [x] MCP tool: `openclaw_migrate` exposed via MCP server for programmatic migration

---

### STORY-026.6: Integration tests for memory replacement

**Status:** done
**Effort:** M
**Depends on:** STORY-026.2, STORY-026.3, STORY-026.4
**Context refs:** `tests/integration/test_openclaw_integration.py`
**Verification:** `pytest tests/integration/test_openclaw_memory_replacement.py -v`

#### Why

Replacing OpenClaw's default memory system is high-risk. Integration tests must validate
the full chain: plugin registration → tool replacement → tapps-brain queries → correct
response format. Regressions here silently break all memory for all users.

#### Acceptance Criteria

- [x] Integration test: register plugin, call `memory_search`, verify results from tapps-brain
- [x] Integration test: register plugin, call `memory_get`, verify entry from tapps-brain
- [x] Integration test: save via tapps-brain → `memory_search` finds it → `memory_get` retrieves it
- [x] Integration test: bidirectional sync round-trip (save → export to markdown → import from markdown → verify)
- [x] Integration test: migration from mock memory-core data → verify all entries in tapps-brain
- [x] Integration test: plugin with `memory` slot active, verify `memory-core` is not invoked
- [x] Overall coverage stays at 95%+

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | 026.1 — Memory slot registration | M | Foundation: everything else depends on claiming the slot |
| 2 | 026.2 — memory_search replacement | M | Primary user-facing feature: search must work |
| 3 | 026.3 — memory_get replacement | S | Completes the tool replacement pair |
| 4 | 026.4 — Bidirectional MEMORY.md sync | L | Backward compat for tools/humans reading markdown |
| 5 | 026.5 — Migration tool | M | Adoption blocker: users need smooth transition |
| 6 | 026.6 — Integration tests | M | Validation of the full replacement |

## Dependency Graph

```
EPIC-012 (done)
    │
    └──→ 026.1 (memory slot) ──┬──→ 026.2 (memory_search) ──┐
                                ├──→ 026.3 (memory_get)      ├──→ 026.6 (integration tests)
                                └──→ 026.4 (markdown sync) ──┤
                                                              └──→ 026.5 (migration)
```
