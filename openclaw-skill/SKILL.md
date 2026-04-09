---
name: tapps-brain-memory
version: "3.2.0"
displayName: "tapps-brain — Persistent Memory"
description: >
  Persistent cross-session memory for OpenClaw agents. BM25 ranking,
  exponential decay, automatic consolidation, configurable profiles,
  cross-project federation, and multi-agent Hive sharing.
author: tapps-brain contributors
license: MIT
kind: context-engine
install: pip install tapps-brain[mcp]
homepage: https://github.com/wtthornton/tapps-brain
repository: https://github.com/wtthornton/tapps-brain
documentation: https://github.com/wtthornton/tapps-brain/tree/main/docs
hooks:
  - bootstrap
  - ingest
  - assemble
  - compact
  - dispose
capabilities:
  - recall
  - capture
  - compaction
  - profiles
  - hive
  - federation
permissions:
  - filesystem:read
  - filesystem:write
  - network:localhost
  - process:spawn
tools:
  # Core Memory (CRUD)
  - name: memory_save
    description: Save or update a memory entry
  - name: memory_get
    description: Retrieve a single memory by key
  - name: memory_delete
    description: Delete a memory entry
  - name: memory_search
    description: Full-text search with filters
  - name: memory_list
    description: List entries with optional filters
  - name: memory_list_groups
    description: List distinct project-local memory group names (GitHub #49)
  # Lifecycle (Recall/Reinforce/Ingest)
  - name: memory_recall
    description: Auto-recall ranked memories for a message
  - name: memory_reinforce
    description: Boost confidence and reset decay
  - name: memory_ingest
    description: Extract facts from context text
  - name: memory_supersede
    description: Create a new version, mark old as invalid
  - name: memory_history
    description: Show full version chain for a key
  # Sessions
  - name: memory_index_session
    description: Index session chunks for future search
  - name: memory_search_sessions
    description: Search past session summaries
  - name: memory_capture
    description: Extract facts from agent response
  # Federation
  - name: federation_status
    description: Hub status, projects, and subscriptions
  - name: federation_subscribe
    description: Subscribe project to receive memories
  - name: federation_unsubscribe
    description: Remove subscription
  - name: federation_publish
    description: Publish shared-scope memories to hub
  # Maintenance
  - name: maintenance_consolidate
    description: Merge similar memories
  - name: maintenance_gc
    description: Archive stale memories
  - name: maintenance_stale
    description: List GC stale candidates with reasons (read-only)
  # GC Config
  - name: memory_gc_config
    description: Return current garbage collection configuration
  - name: memory_gc_config_set
    description: Update garbage collection thresholds
  # Consolidation Config
  - name: memory_consolidation_config
    description: Return current auto-consolidation configuration
  - name: memory_consolidation_config_set
    description: Update auto-consolidation settings
  # Export/Import
  - name: memory_export
    description: Export entries as JSON
  - name: memory_import
    description: Import from JSON or markdown
  - name: tapps_brain_session_end
    description: Save end-of-session episodic summary to the store
  - name: tapps_brain_relay_export
    description: Build memory relay JSON for primary-node import (GitHub #19); items may include optional memory_group or group per memory-relay.md
  # Profiles
  - name: profile_info
    description: Active profile name, layers, scoring config
  - name: profile_switch
    description: Switch to different built-in profile
  - name: memory_profile_onboarding
    description: Markdown onboarding guide for the active profile (tiers, scoring, recall)
  # Hive (Multi-Agent Sharing)
  #
  # Memories are shared via agent_scope on memory_save/memory_capture/memory_ingest:
  #   private  — only this agent (default)
  #   domain   — agents sharing the same profile (e.g. all repo-brain agents)
  #   hive     — ALL agents regardless of profile
  #
  - name: hive_status
    description: Show namespaces, entry counts, and registered agents
  - name: hive_search
    description: Search shared Hive memories from other agents
  - name: hive_propagate
    description: Manually share an existing local memory to the Hive
  - name: hive_push
    description: Batch-promote local memories to the Hive by tag, tier, keys, or all
  - name: hive_write_revision
    description: Monotonic revision for Hive memory writes (poll for new data)
  - name: hive_wait_write
    description: Long-poll until Hive write revision advances or timeout
  - name: agent_register
    description: Register this agent in the Hive registry
  - name: agent_list
    description: List all registered agents and their profiles
  - name: agent_create
    description: Register with profile validation and namespace assignment
  - name: agent_delete
    description: Remove a registered agent from the Hive registry
  # Knowledge Graph
  - name: memory_relations
    description: Return all relations for a memory entry key
  - name: memory_relations_get_batch
    description: Return relations for multiple memory keys in one call
  - name: memory_find_related
    description: BFS traversal — find entries related within N hops
  - name: memory_query_relations
    description: Filter relations by subject, predicate, or object
  # Audit & Tags
  - name: memory_audit
    description: Query the audit trail for memory events with optional filters
  - name: memory_list_tags
    description: List all tags in the store with usage counts
  - name: memory_update_tags
    description: Atomically add and/or remove tags on a memory entry
  - name: memory_entries_by_tag
    description: Return all entries carrying a specific tag
  # Feedback (EPIC-029)
  - name: feedback_rate
    description: Rate a recalled memory (helpful / partial / irrelevant / outdated)
  - name: feedback_gap
    description: Report a knowledge gap query
  - name: feedback_issue
    description: Flag a quality issue on a memory entry
  - name: feedback_record
    description: Record a generic feedback event
  - name: feedback_query
    description: Query feedback events with filters
  # Diagnostics & flywheel (EPIC-030 / EPIC-031)
  - name: diagnostics_report
    description: Quality diagnostics scorecard and circuit breaker state
  - name: diagnostics_history
    description: Recent persisted diagnostics snapshots
  - name: tapps_brain_health
    description: Native health check — store, hive, integrity in one JSON report
  - name: flywheel_process
    description: Apply feedback events to confidence scores
  - name: flywheel_gaps
    description: Cluster and prioritize knowledge gaps
  - name: flywheel_report
    description: Markdown quality report for a time period
  - name: flywheel_evaluate
    description: Offline BEIR-style retrieval evaluation
  - name: flywheel_hive_feedback
    description: Aggregate Hive feedback and apply confidence penalties
  # Agent Brain facade (EPIC-057)
  - name: brain_remember
    description: Save a memory to the agent's brain with tier and sharing options
  - name: brain_recall
    description: Recall memories matching a query from agent, group, and org knowledge
  - name: brain_forget
    description: Archive a memory by key (non-destructive delete)
  - name: brain_learn_success
    description: Record a successful task outcome and reinforce recalled memories
  - name: brain_learn_failure
    description: Record a failed task outcome to avoid repeating mistakes
  - name: brain_status
    description: Show agent identity, group memberships, store stats, and Hive connectivity
resources:
  - uri: memory://stats
    description: Entry count, tier distribution, schema version, package version, profile name, capacity
  - uri: memory://health
    description: Store health report — DB status, WAL mode, decay health, consolidation readiness
  - uri: memory://metrics
    description: Operation counters and latency histograms for all operations
  - uri: memory://feedback
    description: Recent feedback events summary
  - uri: memory://diagnostics
    description: Latest diagnostics report (composite score, dimensions, circuit state)
  - uri: memory://report
    description: Latest rendered flywheel quality report (markdown)
  - uri: "memory://entries/{key}"
    description: Full detail view of a single entry — all fields including decay state and access count
  - uri: memory://agent-contract
    description: Agent integration JSON — versions, profile layers, recall empty-reason codes, primary MCP/CLI paths
prompts:
  - name: recall
    description: "Auto-recall memories about a topic: 'What do you remember about {topic}?'"
  - name: store_summary
    description: Natural-language overview of store contents and stats
  - name: remember
    description: Guided workflow to save a fact with appropriate tier and tags
---

# tapps-brain — Persistent Memory for OpenClaw

A ContextEngine plugin that gives your OpenClaw agent persistent, cross-session
memory. Memories are ranked by relevance, confidence, recency, and frequency
using BM25 scoring with exponential decay.

## Quick Start

```bash
openclaw skill install tapps-brain-memory
```

This installs `tapps-brain[mcp]` from PyPI and configures the MCP server
automatically via `openclaw.plugin.json`.

## Install from GitHub only (no PyPI)

Use the dedicated guide (same commands an OpenClaw operator or agent can follow):

**[openclaw-install-from-git.md](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/openclaw-install-from-git.md)**

Short version:

```bash
pip install "git+https://github.com/wtthornton/tapps-brain.git@main#egg=tapps-brain[mcp]"
cd openclaw-plugin && npm install && npm run build && openclaw plugin install .
```

Then enable `tapps-brain-memory` in your OpenClaw config as in the main
[OpenClaw guide](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/openclaw.md).

Canonical install + upgrade runbook (PyPI + Git-only):
[openclaw-runbook.md](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/openclaw-runbook.md)

**Upgrade (Git):** re-run `pip install --upgrade …` with the same `git+https://…` URL,
or `git pull` + `pip install -e ".[mcp]"` if you use a clone; rebuild `openclaw-plugin`
and restart OpenClaw. Details: [openclaw-install-from-git.md § Upgrade](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/openclaw-install-from-git.md#upgrade-git-only).

## How It Works

| Hook        | What happens                                                  |
|-------------|---------------------------------------------------------------|
| `bootstrap` | Spawns `tapps-brain-mcp`, imports MEMORY.md on first run      |
| `ingest`    | Captures durable facts from messages (rate-limited)           |
| `assemble`  | Recalls memories → `systemPromptAddition` before model call   |
| `compact`   | Flushes context to memory before compaction                   |
| `dispose`   | Stops MCP child process on gateway shutdown                   |

## Features

- **Auto-recall:** Relevant memories injected via `systemPromptAddition`
  before every model call (no explicit tool calls needed)
- **Auto-capture:** Facts extracted from messages via `ingest()` automatically
- **Pre-compaction flush:** Important context saved before OpenClaw compresses
  the context window
- **Configurable profiles:** Switch between built-in profiles or create custom
  ones via `.tapps-brain/profile.yaml`
- **Hive sharing:** Multiple agents share knowledge via the Hive — use
  `agent_scope: "hive"` on `memory_save` for cross-cutting facts or
  `"domain"` for same-profile sharing
- **Federation:** Cross-project memory sharing via a federated hub
- **MCP tools & resources:** Full programmatic control (memory, feedback,
  diagnostics, flywheel, Hive, federation, graph, OpenClaw migration); counts and URIs
  match `docs/generated/mcp-tools-manifest.json` in the tapps-brain repo

## Configuration

Settings are configured in `openclaw.plugin.json` (auto-installed):

| Setting            | Default              | Description                        |
|--------------------|----------------------|------------------------------------|
| `mcpCommand`       | `tapps-brain-mcp`    | Command to spawn the MCP server    |
| `profilePath`      | `.tapps-brain/profile.yaml` | Path to custom profile      |
| `tokenBudget`      | `2000`               | Max tokens for memory injection    |
| `captureRateLimit` | `3`                  | Capture every N ingest() calls     |
| `agentId`          | `""`                 | Agent ID for Hive sharing          |
| `hiveEnabled`      | `false`              | Enable multi-agent Hive            |
| `toolGroups`       | `"all"`              | Tool groups to expose (see below)  |

### Per-Agent Tool Routing

Use `toolGroups` to control which tool groups are registered for a specific agent
role. This lets you give different agents different levels of access without running
multiple MCP servers.

Available groups:

| Group        | Tools included                                                                 |
|--------------|--------------------------------------------------------------------------------|
| `core`       | `memory_search`, `memory_get`                                                  |
| `lifecycle`  | `memory_reinforce`, `memory_supersede`, `memory_history`, `memory_search_sessions` |
| `search`     | `memory_stats`, `memory_health`, `memory_metrics`, `memory_entry_detail`, `memory_recall_prompt`, `memory_store_summary_prompt`, `memory_remember_prompt` |
| `admin`      | `memory_audit`, `memory_list_tags`, `memory_update_tags`, `memory_entries_by_tag`, `profile_info`, `profile_switch`, `maintenance_consolidate`, `maintenance_gc`, `memory_gc_config`, `memory_gc_config_set`, `memory_consolidation_config`, `memory_consolidation_config_set`, `memory_export`, `memory_import` |
| `hive`       | `hive_status`, `hive_search`, `hive_propagate`, `agent_register`, `agent_create`, `agent_list`, `agent_delete` |
| `federation` | `federation_status`, `federation_subscribe`, `federation_unsubscribe`, `federation_publish` |
| `graph`      | `memory_relations`, `memory_relations_get_batch`, `memory_find_related`, `memory_query_relations` |

**Example — coder agent (recall and capture only):**

```yaml
plugins:
  entries:
    tapps-brain-memory:
      config:
        toolGroups: [core, lifecycle, search]
```

**Example — admin agent (full access):**

```yaml
plugins:
  entries:
    tapps-brain-memory:
      config:
        toolGroups: "all"
```

## Permissions

This skill requires:

- **filesystem:read** — Read memory database and configuration files
- **filesystem:write** — Write memory entries and session indexes
- **network:localhost** — MCP server communication (stdio transport)
- **process:spawn** — Spawn `tapps-brain-mcp` subprocess

## Requirements

- Python 3.12+
- OpenClaw v2026.3.7+
- `tapps-brain[mcp]` (installed automatically)
