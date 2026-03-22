---
id: EPIC-013
title: "Hive-aware MCP surface — agent identity, scope propagation, and OpenClaw multi-agent wiring"
status: done
priority: high
created: 2026-03-21
target_date: 2026-07-15
tags: [hive, mcp, openclaw, multi-agent, profiles, propagation]
---

# EPIC-013: Hive-Aware MCP Surface — Agent Identity, Scope Propagation, and OpenClaw Multi-Agent Wiring

## Context

EPIC-011 built the Hive core (HiveStore, AgentRegistry, PropagationEngine, conflict resolution, namespace isolation) and EPIC-012 built the OpenClaw ContextEngine plugin. Both work independently, but the MCP server — the bridge between them — does not wire them together.

The result: an OpenClaw orchestrator that creates multiple agents cannot give each agent a unique identity, assign profiles, or have saves auto-propagate to the Hive. The plumbing exists inside `MemoryStore` (it accepts `hive_store` + `hive_agent_id`, `PropagationEngine` routes by `agent_scope`, profiles configure Hive behavior), but the MCP server and OpenClaw plugin don't pass these parameters through.

### Key Gaps

1. **`memory_save` MCP tool** — missing `agent_scope` and `source_agent` parameters; entries always default to `agent_scope="private"`, so nothing auto-propagates.
2. **MCP server CLI** — no `--agent-id` or `--enable-hive` flags; `_get_store()` creates a bare `MemoryStore` without Hive connection.
3. **Hive tools create throwaway `HiveStore` instances** — per-call instead of reusing the store's connection.
4. **`hive_propagate` hardcodes `agent_id="mcp-user"`** — ignores the calling agent's identity.
5. **OpenClaw plugin** — no `agentId` or `hiveEnabled` config; no auto-registration on bootstrap.
6. **No composite "create agent" MCP tool** — orchestrators need multiple calls to register + configure an agent.

## Success Criteria

- [ ] MCP server accepts `--agent-id` and `--enable-hive` CLI flags
- [ ] `memory_save` tool exposes `agent_scope` parameter; saves auto-propagate when Hive is enabled
- [ ] `hive_propagate` and other Hive tools use the server's agent identity, not hardcoded values
- [ ] OpenClaw plugin supports `agentId` and `hiveEnabled` config; auto-registers on bootstrap
- [ ] A composite `agent_create` MCP tool exists for one-call agent setup
- [ ] Multi-agent Hive pattern documented in OpenClaw guide
- [ ] All changes covered by tests; 95% coverage maintained

## Stories

### STORY-013.1: MCP server Hive wiring — CLI flags and shared HiveStore

**Status:** todo
**Effort:** M
**Depends on:** EPIC-011 (done)

Add `--agent-id <id>` and `--enable-hive` CLI arguments to the MCP server entry point. When `--enable-hive` is set, instantiate a single `HiveStore()` and pass it along with `hive_agent_id` to `MemoryStore`. Store the resolved agent ID and HiveStore on the server instance so all tools can access them.

**Acceptance:**
- `tapps-brain-mcp --agent-id my-agent --enable-hive` starts with Hive-connected store
- `tapps-brain-mcp` (no flags) behaves identically to today (backward compatible)
- Unit test confirms store receives `hive_store` and `hive_agent_id` when flags are set

### STORY-013.2: Expose `agent_scope` in `memory_save` MCP tool

**Status:** todo
**Effort:** S
**Depends on:** STORY-013.1

Add `agent_scope: str = "private"` parameter to the `memory_save` MCP tool. Pass it through to `store.save()`. When Hive is enabled and `agent_scope` is `"domain"` or `"hive"`, the store's existing `_propagate_to_hive()` handles propagation automatically.

**Acceptance:**
- `memory_save(key="x", value="y", agent_scope="hive")` propagates to Hive when enabled
- `memory_save(key="x", value="y")` defaults to `"private"` (no behavior change)
- Unit test with mocked HiveStore verifies propagation triggers

### STORY-013.3: Expose `source_agent` in `memory_save` MCP tool

**Status:** todo
**Effort:** S
**Depends on:** STORY-013.1

Add `source_agent: str = ""` parameter to `memory_save`. When empty, fall back to the server's `--agent-id` value (or `"unknown"` if not set). Pass through to `store.save(source_agent=...)`.

**Acceptance:**
- Explicit `source_agent="qa-bot"` is stored on the entry
- Omitted `source_agent` falls back to server's `--agent-id`
- Unit test verifies both paths

### STORY-013.4: Hive tools reuse server's HiveStore and agent identity

**Status:** todo
**Effort:** M
**Depends on:** STORY-013.1

Refactor `hive_status`, `hive_search`, `hive_propagate`, `agent_register`, `agent_list` to use the server's shared `HiveStore` instance (when Hive is enabled) instead of creating throwaway instances. Update `hive_propagate` to use the server's `agent_id` instead of hardcoded `"mcp-user"`. When Hive is not enabled, these tools should still work by creating a temporary `HiveStore` (current behavior, for backward compat).

**Acceptance:**
- `hive_propagate` uses server's agent_id, not `"mcp-user"`
- `hive_status` reuses the shared HiveStore connection
- When `--enable-hive` is not set, tools still function (fallback to throwaway instances)
- Unit tests verify both paths

### STORY-013.5: `agent_create` composite MCP tool

**Status:** todo
**Effort:** M
**Depends on:** STORY-013.1

Add `agent_create` MCP tool that performs in one call: (1) register agent in AgentRegistry with profile and skills, (2) validate profile exists (built-in or project), (3) return namespace assignment and profile summary. This is the single call an OpenClaw orchestrator needs to spin up a new agent with memory.

**Acceptance:**
- `agent_create(agent_id="qa-1", profile="repo-brain", skills="testing,review")` registers and returns profile details
- Invalid profile name returns error with available profiles listed
- Unit test verifies registration + profile validation

### STORY-013.6: OpenClaw plugin — agent identity and Hive config

**Status:** todo
**Effort:** M
**Depends on:** STORY-013.1, STORY-013.5

Add `agentId` and `hiveEnabled` fields to the OpenClaw plugin's `plugin.json` config schema. Update the bootstrap hook to: (1) pass `--agent-id` and `--enable-hive` flags to the MCP server spawn command, (2) auto-call `agent_register` (or `agent_create`) with the configured agent ID and profile on first run.

**Acceptance:**
- Plugin config with `"agentId": "work-agent", "hiveEnabled": true` spawns MCP with correct flags
- Bootstrap calls `agent_register` automatically
- Omitting `agentId`/`hiveEnabled` preserves current behavior (backward compat)

### STORY-013.7: OpenClaw guide — multi-agent Hive patterns

**Status:** todo
**Effort:** S
**Depends on:** STORY-013.6

Update `docs/guides/openclaw.md` with a "Multi-Agent Hive" section documenting:
- How an orchestrator creates child agents with unique profiles sharing a Hive
- Profile inheritance pattern (base profile + per-agent extends)
- Agent scope usage: when to use private/domain/hive
- Example `plugin.json` configurations for orchestrator and child agents

**Acceptance:**
- Guide includes working configuration examples
- Pattern covers shared-profile and per-role-profile scenarios

### STORY-013.8: Integration tests — multi-agent Hive round-trip

**Status:** todo
**Effort:** M
**Depends on:** STORY-013.2, STORY-013.3, STORY-013.4

Integration test with real SQLite: create two agents with different profiles sharing a Hive, save memories with different `agent_scope` values, verify propagation and recall merging. Test conflict resolution across agents.

**Acceptance:**
- Agent A saves with `agent_scope="hive"` → Agent B can recall it
- Agent A saves with `agent_scope="private"` → Agent B cannot see it
- Agent A saves with `agent_scope="domain"`, same profile as B → B can recall it
- Conflict resolution works when both agents write same key
- File: `tests/integration/test_hive_mcp_roundtrip.py`

### STORY-013.9: Final validation and status update

**Status:** todo
**Effort:** S
**Depends on:** All above

Run full test suite, verify coverage >= 95%. Run lint and type checks. Update EPIC-013 status to done. Update fix_plan.md.

**Acceptance:**
- Full test suite passes
- Coverage >= 95%
- Lint and type checks pass
- EPIC-013 marked done

## Dependency Graph

```
013.1 (MCP Hive wiring)
├── 013.2 (agent_scope in memory_save)
├── 013.3 (source_agent in memory_save)
├── 013.4 (Hive tools refactor)
├── 013.5 (agent_create tool)
│   └── 013.6 (OpenClaw plugin config)
│       └── 013.7 (docs)
└── 013.8 (integration tests, after 013.2+013.3+013.4)
    └── 013.9 (final validation)
```

## Notes

- All changes must be backward compatible — no flags = identical behavior to today
- Core design principle: synchronous, deterministic, no LLM calls
- The Hive tools already exist; this epic wires them into the MCP surface properly
