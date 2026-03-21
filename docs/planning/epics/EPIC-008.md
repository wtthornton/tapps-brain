---
id: EPIC-008
title: "MCP server — expose tapps-brain via Model Context Protocol"
status: complete
priority: critical
created: 2026-03-20
target_date: 2026-05-15
tags: [mcp, integration, server]
---

# EPIC-008: MCP Server — Expose tapps-brain via Model Context Protocol

## Progress in tree (2026-03-20)

- `src/tapps_brain/mcp_server.py` — FastMCP server, tools (CRUD, search, list, recall, reinforce, ingest, supersede, history, **index_session, search_sessions, capture**), resources (stats, health, metrics, entry template), prompts (recall, store_summary, remember)
- `mcp` optional extra + included in `dev` for tests; `python -m tapps_brain.mcp_server`
- `tests/unit/test_mcp_server.py` — registration + handler execution coverage (including session & capture tools)
- `docs/guides/mcp.md` — client setup (Claude Code, Cursor, VS Code), full tool/resource/prompt reference

All library-level MemoryStore operations are now exposed as MCP tools, including session indexing and the capture pipeline. Remaining: registry manifest (STORY-008.7 scope).

## Context

tapps-brain is a library and CLI today. The Model Context Protocol (MCP) has become the universal integration standard for AI coding assistants — Claude Code, Cursor, VS Code Copilot, ChatGPT, Gemini CLI, and others all support MCP servers. Adding an MCP server makes tapps-brain's persistent memory available to every major AI tool without custom integration code.

The official Python SDK (`mcp` v1.26.0) includes FastMCP, a high-level decorator-based API for building servers. The spec version is `2025-11-25` with stdio and streamable HTTP transports. tapps-brain's core is synchronous, but FastMCP handles sync-to-async bridging transparently.

MCP primitives map cleanly to tapps-brain's API:

- **Tools** (model-controlled): save, get, delete, search, recall, reinforce, supersede — operations the LLM decides to invoke
- **Resources** (application-controlled): read-only views of store stats, entry details, health reports
- **Prompts** (user-controlled): workflow templates for common memory operations

## Success Criteria

- [ ] `mcp` added as a core dependency with `[mcp]` optional extra
- [ ] `tapps-brain-mcp` entry point runs a stdio MCP server
- [ ] All core MemoryStore operations exposed as MCP tools
- [ ] Read-only store data exposed as MCP resources
- [ ] Workflow templates exposed as MCP prompts
- [ ] Works with Claude Code, Cursor, and VS Code Copilot (stdio transport)
- [ ] In-process and MCP Inspector test coverage
- [ ] Overall coverage stays at 95%+

## Stories

### STORY-008.1: MCP server skeleton and project setup

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `pyproject.toml`, `src/tapps_brain/__init__.py`, `src/tapps_brain/cli.py`
**Verification:** `tapps-brain-mcp --help` starts server; `pytest tests/unit/test_mcp_server.py -v`

#### Why

Before any tools or resources can be implemented, the project needs the `mcp` dependency, a FastMCP server instance, project directory resolution (matching CLI behavior), and an entry point that AI clients can invoke via stdio.

#### Acceptance Criteria

- [ ] `mcp>=1.26.0` added as optional dependency under `[mcp]` extra in `pyproject.toml`
- [ ] `src/tapps_brain/mcp_server.py` module with `FastMCP("tapps-brain")` instance
- [ ] `--project-dir` argument support (defaults to cwd, matches CLI behavior)
- [ ] Shared `get_store()` helper reused or mirrored from CLI
- [ ] Entry point `tapps-brain-mcp = "tapps_brain.mcp_server:main"` in `pyproject.toml`
- [ ] Server starts via stdio transport and responds to `initialize` handshake
- [ ] Unit test confirming server instantiation and capability negotiation

---

### STORY-008.2: Core memory tools — CRUD and search

**Status:** planned
**Effort:** L
**Depends on:** STORY-008.1
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/models.py`
**Verification:** `pytest tests/unit/test_mcp_server.py::TestCoreTools -v`

#### Why

The highest-value MCP integration is letting AI assistants save, retrieve, and search memories. These are the operations that every MCP client will use on every interaction.

#### Acceptance Criteria

- [ ] `memory_save` tool — save a memory entry (key, value, tier, source, tags, scope, confidence)
- [ ] `memory_get` tool — retrieve a single entry by key
- [ ] `memory_delete` tool — delete an entry by key
- [ ] `memory_search` tool — FTS5 search with optional tier/scope filters and `as_of` for point-in-time
- [ ] `memory_list` tool — list entries with optional tier/scope/include_superseded filters
- [ ] All tools return structured JSON content
- [ ] Input validation with clear error messages for invalid tiers/scopes/keys
- [ ] Unit tests for each tool including error cases

---

### STORY-008.3: Recall and lifecycle tools

**Status:** planned
**Effort:** M
**Depends on:** STORY-008.1
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/recall.py`, `src/tapps_brain/reinforcement.py`
**Verification:** `pytest tests/unit/test_mcp_server.py::TestLifecycleTools -v`

#### Why

Beyond basic CRUD, AI assistants need to trigger recall (pre-prompt memory injection), reinforce memories that proved useful, ingest context from conversations, and manage temporal versioning. These lifecycle operations are what make tapps-brain more than a key-value store.

#### Acceptance Criteria

- [ ] `memory_recall` tool — run auto-recall for a message, return ranked memories with scores
- [ ] `memory_reinforce` tool — boost a memory's confidence after it proved useful
- [ ] `memory_ingest` tool — extract and store memories from conversation context
- [ ] `memory_supersede` tool — create a new version of a memory (bi-temporal)
- [ ] `memory_history` tool — show the version chain for a key
- [ ] Unit tests for each tool

---

### STORY-008.4: Resources — read-only store views

**Status:** planned
**Effort:** M
**Depends on:** STORY-008.1
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/metrics.py`, `src/tapps_brain/audit.py`
**Verification:** `pytest tests/unit/test_mcp_server.py::TestResources -v`

#### Why

MCP resources let clients pull context into prompts without invoking tools. Store stats, health reports, and individual entry details are natural read-only resources that AI assistants can reference as context.

#### Acceptance Criteria

- [ ] `memory://stats` resource — entry count, tier distribution, schema version, store path
- [ ] `memory://health` resource — health report (store health check output)
- [ ] `memory://entries/{key}` resource template — full detail view of a single entry
- [ ] `memory://metrics` resource — operation metrics and latency data
- [ ] Resources return structured text/JSON content
- [ ] Unit tests for each resource

---

### STORY-008.5: Federation and maintenance tools

**Status:** planned
**Effort:** M
**Depends on:** STORY-008.2
**Context refs:** `src/tapps_brain/federation.py`, `src/tapps_brain/auto_consolidation.py`, `src/tapps_brain/gc.py`
**Verification:** `pytest tests/unit/test_mcp_server.py::TestFederationAndMaintenance -v`

#### Why

Multi-project setups need federation management, and store maintenance (consolidation, GC) should be triggerable by AI assistants during housekeeping workflows.

#### Acceptance Criteria

- [ ] `federation_status` tool — hub info, registered projects, subscriptions
- [ ] `federation_subscribe` / `federation_unsubscribe` tools
- [ ] `federation_publish` tool — publish memories to the hub
- [ ] `maintenance_consolidate` tool — trigger auto-consolidation
- [ ] `maintenance_gc` tool — trigger garbage collection (with dry_run option)
- [ ] `memory_export` tool — export entries as JSON
- [ ] `memory_import` tool — import entries from JSON
- [ ] Unit tests for each tool

---

### STORY-008.6: Prompts — workflow templates

**Status:** planned
**Effort:** S
**Depends on:** STORY-008.2, STORY-008.3
**Context refs:** `src/tapps_brain/recall.py`
**Verification:** `pytest tests/unit/test_mcp_server.py::TestPrompts -v`

#### Why

MCP prompts are user-invoked workflow templates (like slash commands). They let users trigger common memory workflows explicitly rather than hoping the AI decides to call the right tools.

#### Acceptance Criteria

- [ ] `recall` prompt — "What do you remember about {topic}?" — runs recall and formats results
- [ ] `store-summary` prompt — generates a summary of what's in the memory store
- [ ] `remember` prompt — "Remember that {fact}" — guides the AI to save a memory with appropriate tier/tags
- [ ] Each prompt has clear description and argument definitions
- [ ] Unit tests for each prompt

---

### STORY-008.7: Integration testing and MCP Inspector validation

**Status:** planned
**Effort:** M
**Depends on:** STORY-008.2, STORY-008.3, STORY-008.4, STORY-008.5, STORY-008.6
**Context refs:** `tests/integration/`
**Verification:** `pytest tests/integration/test_mcp_integration.py -v`

#### Why

Unit tests verify individual tools in isolation. Integration tests verify the full MCP protocol flow — handshake, tool discovery, tool execution, resource reads, and prompt rendering — end-to-end through the transport layer.

#### Acceptance Criteria

- [ ] Integration test using in-memory client-server pair (no subprocess)
- [ ] Test covers: initialize handshake, tools/list, tools/call for each tool category
- [ ] Test covers: resources/list, resources/read for each resource
- [ ] Test covers: prompts/list, prompts/get for each prompt
- [ ] Error handling test: invalid tool arguments, missing keys, protocol errors
- [ ] Documentation in `docs/guides/mcp.md` with setup instructions for Claude Code, Cursor, and VS Code
- [ ] Overall test coverage remains at 95%+

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-008.1 — Server skeleton | M | Foundation: FastMCP instance, entry point, project resolution |
| 2 | STORY-008.2 — Core CRUD tools | L | Highest-value operations — save/get/search |
| 3 | STORY-008.3 — Lifecycle tools | M | recall/reinforce/ingest — the "smart" operations |
| 4 | STORY-008.4 — Resources | M | Read-only views for context injection |
| 5 | STORY-008.5 — Federation/maintenance | M | Operational tools for multi-project setups |
| 6 | STORY-008.6 — Prompts | S | Workflow templates — thin layer over tools |
| 7 | STORY-008.7 — Integration tests | M | End-to-end validation across all primitives |

## Dependency Graph

```
008.1 (skeleton) ──┬──→ 008.2 (CRUD tools) ──┬──→ 008.5 (federation/maint)
                   │                          ├──→ 008.6 (prompts)
                   ├──→ 008.3 (lifecycle)  ───┘         │
                   │                                     │
                   └──→ 008.4 (resources)                │
                                                         │
008.2, 008.3, 008.4, 008.5, 008.6 ──────────────→ 008.7 (integration tests)
```

Stories 008.2, 008.3, and 008.4 can be worked in parallel after 008.1 is complete.
