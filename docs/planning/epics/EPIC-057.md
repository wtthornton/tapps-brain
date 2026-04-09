---
id: EPIC-057
title: "Unified Agent API — hide the complexity"
status: done
priority: critical
created: 2026-04-08
tags: [api, agent-facing, simplification, mcp, cli, documentation]
completed: 2026-04-09
---

# EPIC-057: Unified Agent API — Hide the Complexity

## Context

After EPIC-053 through EPIC-056, tapps-brain has per-agent brains, backend abstraction, Postgres backends, declarative groups, and expert publishing. But these are **infrastructure features**. The agents and humans interacting with tapps-brain should never think about:

- Which backend is being used (SQLite vs Postgres)
- How propagation routing works
- Conflict resolution policies
- Connection pooling or DSN strings
- Group namespace prefixes
- The difference between local, group, and hive scopes during recall

**This epic is the API that agents and LLMs actually use.** It wraps all the complexity behind a simple, declarative interface:

```python
# Agent setup — declared once
brain = AgentBrain(
    agent_id="frontend-dev",
    project_dir="/app/project-1",
    groups=["dev-pipeline", "frontend-guild"],
    expert_domains=[],  # not an expert
)

# Usage — no scope thinking required
brain.remember("Use Tailwind for all styling in this project")
results = brain.recall("how should I style components?")
brain.learn_from_success(task_id="abc123")
brain.learn_from_failure("CSS modules don't work with our build", task_id="abc123")
```

The agent doesn't know about Hive, groups, SQLite, Postgres, propagation, or conflict policies. It just remembers and recalls.

**Depends on:** EPIC-053, EPIC-054, EPIC-055, EPIC-056
**Consumed by:** AgentForge EPIC-37, any future project using tapps-brain

## Success Criteria

- [x] `AgentBrain` class as the primary agent-facing API (wraps `MemoryStore` + `HiveBackend`)
- [x] 5 core methods: `remember()`, `recall()`, `learn_from_success()`, `learn_from_failure()`, `forget()`
- [x] Configuration via environment variables or constructor — no YAML editing required for basic use
- [x] MCP tools simplified to match the `AgentBrain` vocabulary
- [x] CLI commands simplified with agent-friendly aliases
- [x] LLM-readable documentation: system prompt snippets that teach an LLM how to use brain
- [x] Zero knowledge of backends, scopes, or propagation required by callers

## Stories

### STORY-057.1: AgentBrain facade class

**Status:** done (2026-04-09)
**Effort:** L
**Depends on:** EPIC-056.5 (group-aware recall)
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/hive.py`, `src/tapps_brain/profile.py`
**Verification:** `pytest tests/unit/test_agent_brain.py -v --tb=short -m "not benchmark"`

#### Why

`MemoryStore` has 38+ public methods reflecting internal architecture (save, search, recall, reinforce, consolidate, gc, etc.). Agents don't need this surface area. `AgentBrain` is a **facade** that exposes what agents actually do: remember things, recall things, and learn from outcomes.

#### Acceptance Criteria

- [x] `AgentBrain` class in `src/tapps_brain/agent_brain.py`:
  ```python
  class AgentBrain:
      def __init__(
          self,
          agent_id: str,
          project_dir: str | Path,
          groups: list[str] | None = None,
          expert_domains: list[str] | None = None,
          profile: str = "repo-brain",
          hive_dsn: str | None = None,       # env: TAPPS_BRAIN_HIVE_DSN
          encryption_key: str | None = None,  # env: TAPPS_BRAIN_ENCRYPTION_KEY
      ): ...
  ```
- [x] Constructor internally creates: `MemoryStore`, `HiveBackend` (via factory), sets up groups/expert domains
- [x] All configuration can come from env vars (zero-arg construction with env):
  - `TAPPS_BRAIN_AGENT_ID`, `TAPPS_BRAIN_PROJECT_DIR`, `TAPPS_BRAIN_HIVE_DSN`, `TAPPS_BRAIN_GROUPS` (comma-separated), `TAPPS_BRAIN_EXPERT_DOMAINS` (comma-separated)
- [x] `AgentBrain` is exported from `tapps_brain` package top-level: `from tapps_brain import AgentBrain`
- [x] Underlying `MemoryStore` and `HiveBackend` accessible via `brain.store` and `brain.hive` for advanced use cases
- [x] `close()` method for graceful shutdown

---

### STORY-057.2: Core methods — remember, recall, forget

**Status:** done (2026-04-09)
**Effort:** L
**Depends on:** STORY-057.1
**Context refs:** `src/tapps_brain/store.py` (save, recall methods), `src/tapps_brain/retrieval.py`
**Verification:** `pytest tests/unit/test_agent_brain.py -v --tb=short -m "not benchmark"`

#### Why

The three fundamental operations an agent performs. These must be simple, safe, and handle all scope routing internally.

#### Acceptance Criteria

- [x] `remember(fact: str, *, tier: str = "procedural", share: bool = False, share_with: str | list[str] | None = None) -> str`:
  - Generates a key from content hash (caller doesn't manage keys)
  - Default: saves to local agent store only (`agent_scope="private"`)
  - `share=True`: shares with all declared groups (`agent_scope` per group)
  - `share_with="dev-pipeline"`: shares with specific group
  - `share_with="hive"`: publishes to org-wide Hive
  - Expert agents: automatically publishes `architectural`/`pattern` tiers to Hive (per EPIC-056.2)
  - Returns the generated key for reference
  - Runs safety checks (existing `safety.py`) transparently
- [x] `recall(query: str, *, max_results: int = 5, max_tokens: int = 2000, scope: str = "all") -> list[RecallResult]`:
  - `scope="all"` (default): searches local + groups + hive, fused with weights
  - `scope="local"`: searches only agent's private memory
  - `scope="group"`: searches only group memories
  - `scope="hive"`: searches only org-wide expert knowledge
  - Returns `RecallResult` dataclass: `key`, `value`, `confidence`, `source_scope`, `source_agent`, `relevance_score`
  - Token budget enforced via `max_tokens` (existing injection.py logic)
- [x] `forget(key: str) -> bool`:
  - Archives the memory (never hard-deletes, matching existing GC behavior)
  - Returns True if found and archived, False if not found
  - Only operates on agent's local store (cannot delete group/hive memories)

---

### STORY-057.3: Learning methods — success and failure

**Status:** done (2026-04-09)
**Effort:** M
**Depends on:** STORY-057.1
**Context refs:** `src/tapps_brain/store.py` (reinforce), `src/tapps_brain/feedback.py`
**Verification:** `pytest tests/unit/test_agent_brain.py -v --tb=short -m "not benchmark"`

#### Why

AgentForge already has `save_execution()`, `save_failure()`, and `reinforce_memories()` in BrainBridge. These should be first-class on `AgentBrain` with simpler signatures. Learning from outcomes is how agent memory improves over time.

#### Acceptance Criteria

- [x] `learn_from_success(task_description: str, *, task_id: str | None = None, boost: float = 0.1) -> None`:
  - Saves a `procedural` memory with the task description
  - Reinforces any memories that were recalled during the task (via recall tracking)
  - Records positive feedback event
  - Provenance: `task_id`, `session_id` (from context)
  - If agent is expert: auto-publishes to Hive (successful expert knowledge is high value)
- [x] `learn_from_failure(description: str, *, task_id: str | None = None, error: str | None = None) -> None`:
  - Saves a `procedural` memory tagged `failure`
  - Deduplicates against existing failures (similarity check, existing logic)
  - Records negative feedback event
  - Does NOT auto-publish to Hive (failures are agent-specific context)
- [x] `set_task_context(task_id: str, session_id: str | None = None) -> None`:
  - Sets provenance context for subsequent operations
  - Called once per task, not per operation
- [x] Internal recall tracking: `recall()` remembers which keys were returned so `learn_from_success()` can reinforce them

---

### STORY-057.4: Simplified MCP tools

**Status:** done (2026-04-09)
**Effort:** M
**Depends on:** STORY-057.2, STORY-057.3
**Context refs:** `src/tapps_brain/mcp_server.py`
**Verification:** `pytest tests/unit/test_mcp_server.py -v --tb=short -m "not benchmark"`

#### Why

The MCP server currently exposes 80+ tools reflecting internal architecture. LLMs using tapps-brain via MCP don't need `hive_propagate`, `consolidate_similar`, or `gc_stale_entries`. They need the same simple vocabulary as `AgentBrain`: remember, recall, learn.

This story adds **simplified tool aliases** alongside existing tools (not replacing them — power users and internal tooling may need the full surface).

#### Acceptance Criteria

- [x] New MCP tools (agent-friendly aliases):
  - `brain_remember` — maps to `AgentBrain.remember()`
    - Input: `{"fact": str, "tier"?: str, "share"?: bool, "share_with"?: str}`
  - `brain_recall` — maps to `AgentBrain.recall()` (already exists, update to use unified recall)
    - Input: `{"query": str, "max_results"?: int, "scope"?: str}`
  - `brain_forget` — maps to `AgentBrain.forget()`
    - Input: `{"key": str}`
  - `brain_learn_success` — maps to `AgentBrain.learn_from_success()`
    - Input: `{"task_description": str, "task_id"?: str}`
  - `brain_learn_failure` — maps to `AgentBrain.learn_from_failure()`
    - Input: `{"description": str, "task_id"?: str, "error"?: str}`
  - `brain_status` — returns agent identity, group memberships, store stats, hive connectivity
    - Input: `{}`
- [x] Existing 80+ tools remain available (backward compat)
- [x] New tools are listed first in MCP tool enumeration (LLMs see them first)
- [x] Tool descriptions written for LLM consumption (clear, concise, with examples)

---

### STORY-057.5: Simplified CLI commands

**Status:** done (2026-04-09)
**Effort:** S
**Depends on:** STORY-057.2, STORY-057.3
**Context refs:** `src/tapps_brain/cli.py`
**Verification:** `pytest tests/unit/test_cli.py -v --tb=short -m "not benchmark"`

#### Why

The CLI has 43 commands across 10 sub-apps. Humans operating tapps-brain need simple top-level commands for the common case, with sub-apps for advanced use.

#### Acceptance Criteria

- [x] Top-level CLI aliases (no sub-app required):
  - `tapps-brain remember "Use Tailwind for styling"` — save to local store
  - `tapps-brain recall "how to style components"` — unified recall
  - `tapps-brain forget <key>` — archive a memory
  - `tapps-brain status` — agent identity, groups, store stats, hive status
  - `tapps-brain who-am-i` — show current agent_id, groups, expert_domains, project
- [x] All accept `--agent-id` and `--project-dir` (or env vars)
- [x] `--share` flag on `remember` for group/hive publishing
- [x] `--scope` flag on `recall` for filtering (local/group/hive/all)
- [x] Existing sub-app commands (`tapps-brain store save`, `tapps-brain hive search`) remain available
- [x] `tapps-brain --help` shows simplified commands first, sub-apps below

---

### STORY-057.6: LLM-facing documentation and system prompts

**Status:** done (2026-04-09)
**Effort:** M
**Depends on:** STORY-057.4
**Context refs:** `src/tapps_brain/mcp_server.py` (tool descriptions), `docs/guides/`
**Verification:** design-only — review documentation for clarity and completeness

#### Why

LLMs (Claude, GPT, etc.) are primary consumers of tapps-brain via MCP. They need documentation written for LLM consumption: concise, example-driven, and focused on "when to use what." This is different from human developer docs.

#### Acceptance Criteria

- [x] `docs/guides/llm-brain-guide.md` — a system-prompt-sized guide for LLMs:
  - **When to remember:** After learning something useful, when a user states a preference, when completing a task successfully
  - **When to recall:** Before starting a task, when you need context, when the user asks about something you might have seen before
  - **When to share:** When knowledge is useful beyond this agent (share with group), when knowledge is broadly valuable (share with hive)
  - **When NOT to remember:** Ephemeral conversation, one-off clarifications, PII unless explicitly requested
  - **Tier guide:** Which tier to use for what kind of knowledge
  - **Examples:** 5-10 concrete MCP tool call examples with inputs and expected behavior
- [x] MCP tool descriptions updated with LLM-optimized wording
- [x] MCP `prompts` updated:
  - `recall(topic)` — updated to describe unified multi-scope recall
  - `remember(fact)` — new prompt for teaching LLMs to save appropriately
  - `brain_guide()` — new prompt that returns the full LLM guide
- [x] `docs/guides/agent-integration.md` — developer guide for building agents that use tapps-brain:
  - How to set up `AgentBrain` in a new project
  - How to declare groups and expert domains
  - How to configure Hive DSN for shared deployments
  - How to test with local-only mode (no Hive)
  - Reference architecture diagram

---

### STORY-057.7: AgentBrain context manager and lifecycle

**Status:** done (2026-04-09)
**Effort:** S
**Depends on:** STORY-057.1
**Context refs:** `src/tapps_brain/store.py` (close method), `src/tapps_brain/mcp_server.py`
**Verification:** `pytest tests/unit/test_agent_brain.py -v --tb=short -m "not benchmark"`

#### Why

Resources (SQLite connections, Postgres pool, file handles) must be cleaned up. A context manager makes this automatic and Pythonic. Also enables clean shutdown in MCP server and CLI.

#### Acceptance Criteria

- [x] `AgentBrain` supports context manager protocol:
  ```python
  with AgentBrain(agent_id="frontend-dev", project_dir="/app") as brain:
      brain.remember("something")
  # connections closed automatically
  ```
- [x] `AgentBrain.__enter__` returns self
- [x] `AgentBrain.__exit__` calls `close()` which closes `MemoryStore` and `HiveBackend`
- [x] MCP server uses `AgentBrain` lifecycle (init on start, close on shutdown)
- [x] CLI creates and closes `AgentBrain` per command invocation
- [x] Double-close is safe (idempotent)

## Priority Order

| Order | Story | Rationale |
|-------|-------|-----------|
| 1 | STORY-057.1 | Facade class is the foundation |
| 2 | STORY-057.7 | Lifecycle must work before usage |
| 3 | STORY-057.2 | Core methods — the primary API |
| 4 | STORY-057.3 | Learning methods — completes the agent API |
| 5 | STORY-057.4 | MCP tools — LLMs can use the simplified API |
| 6 | STORY-057.5 | CLI — humans can use the simplified API |
| 7 | STORY-057.6 | Documentation — teaches LLMs and developers how to use it |
