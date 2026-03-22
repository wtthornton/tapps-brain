---
id: EPIC-014
title: "Hardening — input validation, interface parity, resilience, and onboarding docs"
status: todo
priority: high
created: 2026-03-21
target_date: 2026-08-01
tags: [hardening, validation, cli, resilience, docs]
---

# EPIC-014: Hardening — Input Validation, Interface Parity, Resilience, and Onboarding Docs

## Context

EPICs 001–013 built a complete memory system with profiles, Hive multi-agent sharing, MCP server, OpenClaw integration, and CLI. A gap audit reveals five focused hardening items that prevent silent bugs, improve resilience, and lower the onboarding barrier.

### Key Gaps

1. **`agent_scope` accepts any string** — typos like `"hivee"` silently default to `"private"`. No validation, no error. Hard to debug.
2. **CLI missing `agent create`** — MCP has a composite `agent_create` tool but the CLI only has `agent register`. Breaks 3-interface parity.
3. **Corrupted SQLite DB causes hard crash** — no recovery guidance when `.db` is corrupt. Users lose time debugging.
4. **No Getting Started guide** — README is comprehensive but doesn't map use cases to interfaces (library vs CLI vs MCP).
5. **No CHANGELOG** — release history not tracked; PyPI publishing needs this.

## Success Criteria

- [ ] `agent_scope` validated against enum; invalid values return clear error
- [ ] CLI `agent create` command matches MCP `agent_create` tool behavior
- [ ] Corrupted DB detected at startup with recovery instructions
- [ ] Getting Started guide helps users pick the right interface
- [ ] CHANGELOG.md tracks releases from v1.0.0 onward

## Stories

### STORY-014.1: Validate `agent_scope` in MCP and store

**Status:** todo
**Effort:** S
**Depends on:** —

Add validation in `memory_save` MCP tool and `MemoryStore.save()`: reject values not in `{"private", "domain", "hive"}` with a clear error message listing valid options.

**Acceptance:**
- `memory_save(key="x", value="y", agent_scope="hivee")` returns `{"error": "invalid_agent_scope", "valid_values": ["private", "domain", "hive"]}`
- `memory_save(key="x", value="y", agent_scope="hive")` works as before
- Unit test for valid and invalid values

### STORY-014.2: CLI `agent create` command

**Status:** todo
**Effort:** S
**Depends on:** —

Add `agent create` subcommand to CLI that mirrors MCP `agent_create`: register agent with profile validation, return namespace and profile summary. Reuse existing `AgentRegistry` and `profile.py` logic.

**Acceptance:**
- `tapps-brain agent create --id qa-1 --profile repo-brain --skills "testing,review"` registers agent and prints summary
- Invalid profile prints error with available profiles
- Unit test for happy path and invalid profile

### STORY-014.3: Graceful SQLite corruption handling

**Status:** todo
**Effort:** S
**Depends on:** —

Wrap `MemoryPersistence.__init__()` and `MemoryStore.__init__()` with try/except for `sqlite3.DatabaseError`. On corruption, log a clear message with recovery steps: backup the corrupt file, delete it, restart.

**Acceptance:**
- Corrupted `.db` file produces: `"Database corrupt: {path}. Back up and delete to recover."`
- Store still raises (does not silently create empty DB) but error is actionable
- Unit test with intentionally corrupted DB file

### STORY-014.4: Getting Started guide

**Status:** todo
**Effort:** S
**Depends on:** —

Create `docs/guides/getting-started.md` mapping use cases to interfaces:
- "I want to add memory to my Python app" → Library
- "I want CLI access for scripts/CI" → CLI
- "I want memory in Claude Code / OpenClaw" → MCP server
- Quick example for each (3-5 lines)
- Link from README

**Acceptance:**
- Guide covers all 3 interfaces with working examples
- README links to it from the Quick Start section

### STORY-014.5: CHANGELOG.md

**Status:** todo
**Effort:** S
**Depends on:** —

Create `CHANGELOG.md` following Keep a Changelog format. Reconstruct from git history:
- v1.1.0: EPIC-013 (Hive MCP surface, agent_create, agent_scope/source_agent params)
- v1.0.0: EPICs 001–012 (core store, profiles, Hive, OpenClaw, federation, CLI, MCP)

**Acceptance:**
- Follows keepachangelog.com format
- Covers v1.0.0 and v1.1.0 releases
- Linked from README and pyproject.toml

## Dependency Graph

```
014.1 (agent_scope validation)     — independent
014.2 (CLI agent create)           — independent
014.3 (SQLite corruption handling) — independent
014.4 (Getting Started guide)      — independent
014.5 (CHANGELOG)                  — independent
```

All tasks are independent and can be done in any order.

## Notes

- All changes must maintain 95% test coverage
- All tasks are small (S effort) — each fits a single Ralph loop
- No new dependencies or architectural changes
