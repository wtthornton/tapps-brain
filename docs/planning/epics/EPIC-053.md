---
id: EPIC-053
title: "Per-Agent Brain Identity — isolated storage with automatic registration"
status: done
priority: high
created: 2026-04-08
tags: [agent-identity, hive, storage, multi-agent]
completed: 2026-04-09
---

# EPIC-053: Per-Agent Brain Identity

## Context

Today `agent_id` defaults to `"unknown"` across all of tapps-brain. The MCP server accepts `--agent-id` but most callers never set it. `MemoryStore` creates a single `memory.db` per project directory, meaning all agents in a project share one brain.

The target architecture requires **each agent to own its own brain** — a private `memory.db` with its own memories, plans, decay curves, and access patterns. When AgentForge's `frontend-dev` agent saves a memory, it must not pollute `backend-dev`'s local store. When the same `frontend-dev` archetype runs in project-1 and project-2, each instance has independent local memory.

This epic introduces first-class agent identity into tapps-brain so that:
1. Each agent gets an isolated local store keyed by `(project, agent_id)`
2. Agent identity propagates automatically through save, recall, hive, and federation
3. The `AgentRegistry` becomes the single source of truth for who exists

**Upstream dependency for:** EPIC-054 (backend abstraction), EPIC-056 (group membership), EPIC-057 (unified API).

## Success Criteria

- [x] `MemoryStore(project_dir, agent_id="frontend-dev")` creates storage at `{project_dir}/.tapps-brain/agents/frontend-dev/memory.db`
- [x] Omitting `agent_id` falls back to `{project_dir}/.tapps-brain/memory/memory.db` (backward compatible)
- [x] `AgentRegistry.register()` auto-creates on first `MemoryStore` instantiation when `agent_id` is provided
- [x] MCP server passes `--agent-id` through to `MemoryStore` constructor
- [x] CLI commands accept `--agent-id` and operate on that agent's store
- [x] All saves carry `source_agent` automatically from the store's identity
- [x] Migration path exists for projects currently using a single shared `memory.db`

## Stories

### STORY-053.1: Agent-scoped storage paths

**Status:** done (2026-04-09)
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/persistence.py`, `src/tapps_brain/models.py`
**Verification:** `pytest tests/unit/test_agent_identity.py -v --tb=short -m "not benchmark"`

#### Why

A single `memory.db` per project means all agents share state. When agent A's ephemeral context decays, it might consolidate with agent B's architectural memory. Per-agent storage isolation is the foundation for everything else in this architecture.

#### Acceptance Criteria

- [x] `MemoryStore(project_dir, agent_id="frontend-dev")` resolves storage to `{project_dir}/.tapps-brain/agents/frontend-dev/memory.db`
- [x] `MemoryStore(project_dir)` (no agent_id) resolves to `{project_dir}/.tapps-brain/memory/memory.db` (backward compat)
- [x] `MemoryStore.agent_id` property returns the configured identity (or `None` for legacy stores)
- [x] `Persistence` class accepts the computed path — no changes to schema or SQL
- [x] Directory creation is automatic (no manual `mkdir` required)
- [x] Audit log, archive, and FTS index are per-agent (co-located with `memory.db`)

---

### STORY-053.2: Automatic source_agent propagation

**Status:** done (2026-04-09)
**Effort:** S
**Depends on:** STORY-053.1
**Context refs:** `src/tapps_brain/store.py` (`save` method), `src/tapps_brain/models.py` (`MemoryEntry`)
**Verification:** `pytest tests/unit/test_agent_identity.py -v --tb=short -m "not benchmark"`

#### Why

Callers currently must pass `source_agent=` on every save call. With per-agent stores, the store already knows who it belongs to. Propagating `source_agent` automatically removes boilerplate and prevents identity mismatches.

#### Acceptance Criteria

- [x] `MemoryStore.save()` auto-fills `source_agent` from `self.agent_id` when caller omits it
- [x] Explicit `source_agent=` still overrides (for relay/import scenarios)
- [x] `MemoryEntry.source_agent` is never `None` when saved from an agent-scoped store
- [x] Hive propagation carries the correct `source_agent` without caller intervention

---

### STORY-053.3: AgentRegistry auto-registration

**Status:** done (2026-04-09)
**Effort:** M
**Depends on:** STORY-053.1
**Context refs:** `src/tapps_brain/hive.py` (`AgentRegistry`, `AgentRegistration`), `src/tapps_brain/store.py`
**Verification:** `pytest tests/unit/test_agent_identity.py tests/unit/test_hive.py -v --tb=short -m "not benchmark"`

#### Why

Today `AgentRegistry` is a standalone YAML file that must be manually populated. If an agent creates a `MemoryStore` with an `agent_id` it should auto-register in the Hive registry so the ecosystem knows it exists. This is the "hide complexity" principle — agents shouldn't manage their own registration.

#### Acceptance Criteria

- [x] When `MemoryStore(agent_id="X", hive_store=hive)` is constructed, `hive.registry.register()` is called if agent `X` is not already registered
- [x] Auto-registration populates: `id`, `name` (from agent_id), `project_root`
- [x] Optional fields (`profile`, `skills`, `groups`) can be set later or via config
- [x] Auto-registration is idempotent — creating the same store twice does not duplicate
- [x] Auto-registration can be disabled via `auto_register=False` for testing

---

### STORY-053.4: CLI and MCP agent-id passthrough

**Status:** done (2026-04-09)
**Effort:** S
**Depends on:** STORY-053.1
**Context refs:** `src/tapps_brain/cli.py`, `src/tapps_brain/mcp_server.py`
**Verification:** `pytest tests/unit/test_cli.py tests/unit/test_mcp_server.py -v --tb=short -m "not benchmark"`

#### Why

The MCP server already accepts `--agent-id` but doesn't use it to scope storage. The CLI has no `--agent-id` flag at all. Both interfaces must route to the correct per-agent store.

#### Acceptance Criteria

- [x] `tapps-brain-mcp --agent-id frontend-dev --project-dir /app` opens the agent-scoped store
- [x] CLI global option `--agent-id` added; all subcommands operate on that agent's store
- [x] `TAPPS_BRAIN_AGENT_ID` environment variable supported as fallback
- [x] MCP `memory://stats` resource reflects the agent-scoped store
- [x] Omitting `--agent-id` uses legacy shared store (backward compat)

---

### STORY-053.5: Migration tooling for shared-to-agent stores

**Status:** done (2026-04-09)
**Effort:** M
**Depends on:** STORY-053.1
**Context refs:** `src/tapps_brain/persistence.py`, `src/tapps_brain/cli.py`
**Verification:** `pytest tests/unit/test_migration.py -v --tb=short -m "not benchmark"`

#### Why

Existing projects have a single `memory.db` with memories from multiple agents (identified by `source_agent` field). A migration tool should split these into per-agent stores so existing deployments can adopt the new model.

#### Acceptance Criteria

- [x] CLI command `tapps-brain maintenance split-by-agent` reads shared `memory.db` and creates per-agent stores
- [x] Memories are routed by `source_agent` field; memories with `source_agent=None` go to a `_legacy` agent store
- [x] Original `memory.db` is not modified (copy, don't move)
- [x] FTS indexes are rebuilt in each new agent store
- [x] Dry-run mode (`--dry-run`) reports what would happen without writing
- [x] Summary output shows memory counts per agent

## Priority Order

| Order | Story | Rationale |
|-------|-------|-----------|
| 1 | STORY-053.1 | Foundation — storage paths must exist before anything else |
| 2 | STORY-053.2 | Quick win once paths work; removes boilerplate for all callers |
| 3 | STORY-053.3 | Auto-registration enables Hive awareness without manual setup |
| 4 | STORY-053.4 | CLI/MCP passthrough makes the feature usable end-to-end |
| 5 | STORY-053.5 | Migration is needed for adoption but not for greenfield |
