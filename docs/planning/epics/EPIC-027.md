---
id: EPIC-027
title: "OpenClaw Full Feature Surface — expose all 41 MCP tools as native tools"
status: done
priority: high
created: 2026-03-23
target_date: 2026-05-31
tags: [openclaw, mcp, tools, native-tools, integration]
---

# EPIC-027: OpenClaw Full Feature Surface — Expose All 41 MCP Tools as Native Tools

## Context

**Note:** This epic was written against a **41-tool** MCP surface; tapps-brain exposes **54** tools as of v1.3.1. Counts in the narrative below are historical unless otherwise stated.

EPIC-012's ContextEngine plugin uses only ~8 of tapps-brain's 41 MCP tools internally
(recall, capture, ingest, import, session index, agent register). The remaining 33 tools
— including federation, Hive, knowledge graph, audit trail, tags, profiles, GC,
consolidation, export/import, and relations — are invisible to the OpenClaw agent unless
the user separately configures `tapps-brain-mcp` as an MCP sidecar in `mcpServers`.

This creates a fragmented experience: auto-recall works, but the agent can't
consolidate memories, query the knowledge graph, manage tags, or share via Hive unless
the user does extra configuration. The goal is to expose **every** tapps-brain feature
as a native OpenClaw tool through the plugin, so `openclaw skill install tapps-brain-memory`
gives the agent the full 41-tool surface with zero additional config.

Depends on EPIC-012 (done) and benefits from EPIC-026 (memory slot).

## Success Criteria

- [x] All 41 MCP tools are registered as native OpenClaw tools via the plugin API
- [x] All 4 MCP resources exposed as data sources the agent can query
- [x] All 3 MCP prompts registered as OpenClaw slash commands
- [x] Tools are grouped by category with clear descriptions for the agent
- [x] Per-agent routing works: admin agent gets all tools, read-only agent gets search/recall only
- [x] Documentation: complete tool reference and configuration guide
- [x] `openclaw-skill/SKILL.md` updated to declare MCP tools (54 as of v1.3.1)

## Stories

### STORY-027.1: Register Hive tools as native OpenClaw tools

**Status:** done
**Effort:** M
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/index.ts`, `src/tapps_brain/mcp_server.py:1082-1353`
**Verification:** manual test — OpenClaw agent calls `hive_status`, `hive_search`, `hive_propagate`

#### Why

Hive is tapps-brain's multi-agent shared brain. OpenClaw runs multiple agents
(main, coder, researcher, etc.) — Hive is the natural way for them to share knowledge.
Currently, Hive tools are only available via MCP sidecar config.

#### Acceptance Criteria

- [x] `hive_status`, `hive_search`, `hive_propagate` registered via `api.registerTool()`
- [x] `agent_register`, `agent_create`, `agent_list`, `agent_delete` registered
- [x] Tools proxy to `tapps-brain-mcp` child process via MCP client
- [x] Tools return JSON matching the MCP server's response format
- [x] Graceful degradation: if Hive is disabled, tools return `{ "error": "hive_disabled" }` instead of crashing
- [x] SKILL.md updated to declare Hive tools

---

### STORY-027.2: Register federation tools as native OpenClaw tools

**Status:** done
**Effort:** S
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/index.ts`, `src/tapps_brain/mcp_server.py:625-753`
**Verification:** manual test — OpenClaw agent calls `federation_status`

#### Why

Federation enables cross-project memory sharing. OpenClaw users often have multiple
workspaces (personal projects, work projects, shared docs). Federation lets knowledge
flow between them.

#### Acceptance Criteria

- [x] `federation_status`, `federation_subscribe`, `federation_unsubscribe`, `federation_publish` registered
- [x] Tools proxy to MCP client
- [x] Returns correct JSON format
- [x] SKILL.md updated to declare federation tools

---

### STORY-027.3: Register knowledge graph tools as native OpenClaw tools

**Status:** done
**Effort:** S
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/index.ts`, `src/tapps_brain/mcp_server.py:1354-1410`
**Verification:** manual test — OpenClaw agent calls `memory_relations`, `memory_find_related`

#### Why

The knowledge graph allows the agent to explore relationships between memories —
e.g., "what depends on this architecture decision?" Powerful for complex codebases,
but currently hidden behind MCP sidecar config.

#### Acceptance Criteria

- [x] `memory_relations`, `memory_find_related`, `memory_query_relations` registered
- [x] Tools proxy to MCP client
- [x] Returns correct JSON format
- [x] SKILL.md updated to declare graph tools

---

### STORY-027.4: Register maintenance and config tools

**Status:** done
**Effort:** M
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/index.ts`, `src/tapps_brain/mcp_server.py:755-915`
**Verification:** manual test — OpenClaw agent calls `maintenance_consolidate`, `memory_gc_config`

#### Why

Without maintenance tools, the memory store grows unbounded until the 500-entry limit.
Consolidation, GC, and config tools let the agent (or the user) manage the store's
health proactively.

#### Acceptance Criteria

- [x] `maintenance_consolidate`, `maintenance_gc` registered
- [x] `memory_gc_config`, `memory_gc_config_set` registered
- [x] `memory_consolidation_config`, `memory_consolidation_config_set` registered
- [x] `memory_export`, `memory_import` registered
- [x] Tools proxy to MCP client
- [x] SKILL.md updated to declare maintenance tools

---

### STORY-027.5: Register audit, tags, and profile tools

**Status:** done
**Effort:** M
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/index.ts`, `src/tapps_brain/mcp_server.py:1412-1525`
**Verification:** manual test — OpenClaw agent calls `memory_audit`, `memory_list_tags`, `profile_info`

#### Why

Audit trail, tags, and profiles are essential for managing a knowledge store at scale.
Tags let the agent categorize and filter memories. Audit provides accountability.
Profiles let different agents use different scoring/decay strategies.

#### Acceptance Criteria

- [x] `memory_audit` registered
- [x] `memory_list_tags`, `memory_update_tags`, `memory_entries_by_tag` registered
- [x] `profile_info`, `profile_switch` registered
- [x] Tools proxy to MCP client
- [x] SKILL.md updated to declare all tools (final count: 41)

---

### STORY-027.6: Register remaining lifecycle tools

**Status:** done
**Effort:** S
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/index.ts`, `src/tapps_brain/mcp_server.py:300-504`
**Verification:** manual test — OpenClaw agent calls `memory_reinforce`, `memory_supersede`

#### Why

`memory_reinforce`, `memory_supersede`, `memory_history`, and `memory_search_sessions`
are lifecycle tools that the ContextEngine doesn't use automatically but that an agent
should be able to call explicitly — e.g., "reinforce this memory because it was useful"
or "show me the history of this decision."

#### Acceptance Criteria

- [x] `memory_reinforce`, `memory_supersede`, `memory_history` registered
- [x] `memory_search_sessions` registered
- [x] Tools proxy to MCP client
- [x] Returns correct JSON format

---

### STORY-027.7: Expose MCP resources and prompts

**Status:** done
**Effort:** S
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/index.ts`, `src/tapps_brain/mcp_server.py:506-623`
**Verification:** manual test — OpenClaw agent reads `memory://stats` resource

#### Why

MCP resources (stats, health, metrics, entry detail) provide read-only data views.
MCP prompts (recall, store_summary, remember) provide user-invokable workflows. These
should be accessible in OpenClaw without requiring MCP sidecar config.

#### Acceptance Criteria

- [x] Resource URIs (`memory://stats`, `memory://health`, `memory://metrics`, `memory://entries/{key}`) exposed via registered tools or data provider
- [x] Prompts (`recall`, `store_summary`, `remember`) registered as OpenClaw commands or tools
- [x] Resources return JSON matching MCP server format
- [x] SKILL.md updated to declare resources and prompts

---

### STORY-027.8: Per-agent tool routing and permissions

**Status:** done
**Effort:** M
**Depends on:** STORY-027.1 through STORY-027.7
**Context refs:** `openclaw-plugin/openclaw.plugin.json`
**Verification:** `openclaw plugins inspect tapps-brain-memory` shows tool groups

#### Why

Not every agent should have access to every tool. A read-only research agent should
only call `memory_search` and `memory_recall`, not `maintenance_gc` or `memory_delete`.
OpenClaw supports per-agent MCP server routing — tapps-brain should declare tool groups
for this.

#### Acceptance Criteria

- [x] Tools organized into groups: `core` (CRUD), `lifecycle` (recall/reinforce/supersede), `search` (search/sessions/tags), `admin` (GC/consolidation/config/export), `hive` (Hive/agents), `federation` (federation), `graph` (relations)
- [x] `openclaw.plugin.json` updated with `toolGroups` configuration
- [x] Documentation: how to configure per-agent tool access in `openclaw.json`
- [x] Example config: "coder" agent gets core+lifecycle+search; "admin" agent gets all groups
- [x] Tool group names match OpenClaw's `tools.sandbox.tools.allow` pattern

---

### STORY-027.9: Update SKILL.md and documentation

**Status:** done
**Effort:** S
**Depends on:** STORY-027.1 through STORY-027.8
**Context refs:** `openclaw-skill/SKILL.md`, `docs/guides/openclaw.md`
**Verification:** `openclaw skill info tapps-brain-memory` reflects the published tool list

#### Why

The SKILL.md currently declares only 28 tools. Users and ClawHub need the complete
tool listing to understand what tapps-brain provides. Documentation must cover all
integration modes: ContextEngine, memory slot, MCP sidecar, and mcp-adapter.

#### Acceptance Criteria

- [x] SKILL.md declares tools with descriptions (keep in sync with MCP server)
- [x] SKILL.md declares all 4 resources and 3 prompts
- [x] `docs/guides/openclaw.md` updated with four integration modes:
  1. **ContextEngine only** (auto-recall/capture, minimal config)
  2. **ContextEngine + memory slot** (replaces memory-core, EPIC-026)
  3. **MCP sidecar** (full 41-tool access via mcpServers config)
  4. **mcp-adapter** (full access via openclaw-mcp-adapter plugin)
- [x] Configuration examples for each mode
- [x] Troubleshooting guide: common issues (transport mismatch, SQLite lock, etc.)

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | 027.6 — Lifecycle tools | S | Most immediately useful: reinforce, supersede, history |
| 2 | 027.1 — Hive tools | M | Multi-agent is the killer feature for OpenClaw |
| 3 | 027.3 — Knowledge graph tools | S | Quick win: 3 tools, high value |
| 4 | 027.5 — Audit, tags, profiles | M | Management surface for power users |
| 5 | 027.4 — Maintenance and config | M | Store health management |
| 6 | 027.2 — Federation tools | S | Cross-project sharing |
| 7 | 027.7 — Resources and prompts | S | Read-only views and workflows |
| 8 | 027.8 — Per-agent routing | M | Admin-level configuration |
| 9 | 027.9 — Documentation update | S | Final polish |

## Dependency Graph

```
EPIC-012 (done)
    │
    ├──→ 027.1 (Hive tools) ──────────┐
    ├──→ 027.2 (Federation tools) ────┤
    ├──→ 027.3 (Graph tools) ─────────┤
    ├──→ 027.4 (Maintenance tools) ───┼──→ 027.8 (Per-agent routing) ──→ 027.9 (Docs)
    ├──→ 027.5 (Audit/tags/profiles) ─┤
    ├──→ 027.6 (Lifecycle tools) ─────┤
    └──→ 027.7 (Resources/prompts) ───┘
```
