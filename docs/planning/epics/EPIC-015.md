---
id: EPIC-015
title: "Analytics & Operational Surface — expose graph, audit, tags, GC, and consolidation controls"
status: done
priority: high
created: 2026-03-22
target_date: 2026-09-01
tags: [analytics, observability, graph, audit, tags, gc, consolidation, mcp, cli]
---

# EPIC-015: Analytics & Operational Surface

## Context

The tapps-brain library layer has ~36 public methods on `MemoryStore`, but 7 are not exposed via MCP or CLI. The knowledge graph (relations, BFS traversal), audit trail, tag management, GC thresholds, and auto-consolidation config are all implemented and tested internally but invisible to MCP/CLI users. This means production teams can't inspect, tune, or debug their memory stores without dropping to Python.

### Key Gaps

1. **Knowledge graph** — `get_relations()`, `find_related()`, `query_relations()` exist but have zero MCP tools or CLI commands.
2. **Audit trail** — JSONL recorded on every mutation but no query surface.
3. **Tag management** — tags stored and searchable but can't be listed, added, or removed independently.
4. **GC thresholds** — hardcoded (`floor_retention_days=30`, `session_expiry_days=7`). Not configurable at runtime.
5. **Auto-consolidation** — `set_consolidation_config()` exists but isn't exposed via MCP/CLI.
6. **Agent lifecycle** — no `agent delete`, no `agent list` CLI, no Hive statistics.

## Success Criteria

- [ ] Knowledge graph operations exposed: `memory_relations`, `memory_find_related`, `memory_query_relations` MCP tools + CLI equivalents
- [ ] Audit trail queryable via MCP tool and CLI command
- [ ] Tag management: list all tags, update tags on entries, filter entries by tag — via MCP + CLI
- [ ] GC thresholds configurable at runtime via MCP tool and CLI command
- [ ] Auto-consolidation config readable/writable via MCP tool and CLI command
- [ ] Agent lifecycle: `agent delete` MCP tool + CLI, `agent list` CLI, Hive stats in `hive_status`
- [ ] All changes covered by tests; 95% coverage maintained

## Stories

### STORY-015.1: Knowledge graph MCP tools

**Status:** todo
**Effort:** M
**Depends on:** —

Add 3 MCP tools exposing the existing knowledge graph:
- `memory_relations(key)` — return relations for a given entry key
- `memory_find_related(key, max_hops=2)` — BFS graph traversal, return connected entries
- `memory_query_relations(subject="", predicate="", object_entity="")` — filter all relations

All delegate to existing `store.get_relations()`, `store.find_related()`, `store.query_relations()`.

**Acceptance:**
- Each tool returns JSON with relations or related entries
- Empty results return `[]`, not errors
- Unit tests for each tool

### STORY-015.2: Knowledge graph CLI commands

**Status:** todo
**Effort:** S
**Depends on:** STORY-015.1

Add CLI commands:
- `tapps-brain memory relations <key>` — list relations for an entry
- `tapps-brain memory related <key> --hops 2` — find related entries via graph traversal

**Acceptance:**
- Output formatted as table (subject | predicate | object)
- `--format json` flag for machine-readable output
- Unit tests

### STORY-015.3: Audit trail MCP tool

**Status:** todo
**Effort:** S
**Depends on:** —

Add `memory_audit(key="", event_type="", since="", until="", limit=50)` MCP tool. Delegates to `store.audit()`. Returns JSON array of audit events.

**Acceptance:**
- Filter by key, event_type, date range
- Default limit 50
- Unit test

### STORY-015.4: Audit trail CLI command

**Status:** todo
**Effort:** S
**Depends on:** STORY-015.3

Add `tapps-brain memory audit [key] --type save --since 2026-01-01 --limit 20` CLI command.

**Acceptance:**
- Tabular output with timestamp, event_type, key, details
- `--format json` flag
- Unit test

### STORY-015.5: Tag management MCP tools

**Status:** todo
**Effort:** M
**Depends on:** —

Add 3 MCP tools:
- `memory_list_tags()` — return all distinct tags across entries
- `memory_update_tags(key, add=[], remove=[])` — modify tags without re-saving entire entry
- `memory_entries_by_tag(tag, tier="")` — list entries with a given tag

`memory_update_tags` requires a new `store.update_tags(key, add, remove)` method that updates both the in-memory dict and SQLite.

**Acceptance:**
- `memory_list_tags()` returns sorted list of all tags
- `memory_update_tags` adds/removes tags atomically
- `memory_entries_by_tag` filters by tag and optionally by tier
- Unit tests for each tool

### STORY-015.6: Tag management CLI commands

**Status:** todo
**Effort:** S
**Depends on:** STORY-015.5

Add CLI commands:
- `tapps-brain memory tags` — list all tags with entry counts
- `tapps-brain memory tag <key> --add tag1 --remove tag2` — modify tags on an entry

**Acceptance:**
- Tabular output
- Unit tests

### STORY-015.7: GC config MCP tools and CLI

**Status:** todo
**Effort:** S
**Depends on:** —

Add MCP tools:
- `memory_gc_config()` — return current GC thresholds (floor_retention_days, session_expiry_days, contradicted_threshold)
- `memory_gc_config_set(floor_retention_days, session_expiry_days, contradicted_threshold)` — update thresholds

Add CLI:
- `tapps-brain maintenance gc-config` — show thresholds
- `tapps-brain maintenance gc-config --set floor_retention_days=60`

Requires making `MemoryGarbageCollector` accept runtime config updates.

**Acceptance:**
- Thresholds readable and writable via MCP and CLI
- Invalid values rejected with clear error
- Unit tests

### STORY-015.8: Auto-consolidation config MCP tools and CLI

**Status:** todo
**Effort:** S
**Depends on:** —

Add MCP tools:
- `memory_consolidation_config()` — return current auto-consolidation config
- `memory_consolidation_config_set(enabled, threshold, min_entries)` — update config

Add CLI:
- `tapps-brain maintenance consolidation-config` — show config
- `tapps-brain maintenance consolidation-config --set threshold=0.85`

Delegates to existing `store.set_consolidation_config()`.

**Acceptance:**
- Config readable and writable via MCP and CLI
- Unit tests

### STORY-015.9: Agent lifecycle — delete and CLI parity

**Status:** todo
**Effort:** S
**Depends on:** —

Add:
- `agent_delete(agent_id)` MCP tool — remove agent from registry
- `tapps-brain agent list` CLI command (mirrors MCP `agent_list`)
- `tapps-brain agent delete <id>` CLI command
- Hive statistics in `hive_status` output: per-namespace entry counts, registered agent count

Requires `AgentRegistry.unregister(agent_id)` method.

**Acceptance:**
- Agent can be deleted and re-registered
- CLI `agent list` shows same data as MCP `agent_list`
- `hive_status` includes per-namespace breakdown
- Unit tests

### STORY-015.10: Final validation and status update

**Status:** todo
**Effort:** S
**Depends on:** All above

Run full test suite, verify coverage >= 95%. Run lint and type checks. Update EPIC-015 status to done. Update fix_plan.md.

**Acceptance:**
- Full test suite passes
- Coverage >= 95%
- Lint and type checks pass
- EPIC-015 marked done

## Dependency Graph

```
015.1 (graph MCP)       → 015.2 (graph CLI)
015.3 (audit MCP)       → 015.4 (audit CLI)
015.5 (tag mgmt MCP)    → 015.6 (tag mgmt CLI)
015.7 (GC config)       — independent
015.8 (consolidation)   — independent
015.9 (agent lifecycle) — independent
015.10 (validation)     — last
```

## Notes

- All changes must maintain 95% test coverage
- Most stories delegate to already-implemented store methods — the work is wiring, not new logic
- No new dependencies or architectural changes
- MCP tool count will grow from 29 to ~39
