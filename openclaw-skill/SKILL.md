---
name: tapps-brain-memory
version: "1.2.0"
displayName: "tapps-brain — Persistent Memory"
description: >
  Persistent cross-session memory for OpenClaw agents. BM25 ranking,
  exponential decay, automatic consolidation, configurable profiles,
  cross-project federation, and multi-agent Hive sharing.
author: tapps-brain contributors
license: MIT
slot: ContextEngine
install: pip install tapps-brain[mcp]
homepage: https://github.com/wtthornton/tapps-brain
repository: https://github.com/wtthornton/tapps-brain
documentation: https://github.com/wtthornton/tapps-brain/tree/main/docs
triggers:
  - bootstrap
  - ingest
  - afterTurn
  - compact
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
  # Export/Import
  - name: memory_export
    description: Export entries as JSON
  - name: memory_import
    description: Import from JSON or markdown
  # Profiles
  - name: profile_info
    description: Active profile name, layers, scoring config
  - name: profile_switch
    description: Switch to different built-in profile
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
  - name: agent_register
    description: Register this agent in the Hive registry
  - name: agent_list
    description: List all registered agents and their profiles
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

## How It Works

| Hook        | What happens                                                  |
|-------------|---------------------------------------------------------------|
| `bootstrap` | Spawns `tapps-brain-mcp`, imports MEMORY.md on first run      |
| `ingest`    | Auto-recalls relevant memories and injects them into context  |
| `afterTurn` | Captures new facts from agent responses (rate-limited)        |
| `compact`   | Flushes important context to memory before compaction         |

## Features

- **Auto-recall:** Relevant memories injected before every turn (no explicit
  tool calls needed)
- **Auto-capture:** Facts extracted from agent responses automatically
- **Pre-compaction flush:** Important context saved before OpenClaw compresses
  the context window
- **Configurable profiles:** Switch between `default`, `long_term`,
  `high_confidence`, or `fast_context` scoring profiles
- **Hive sharing:** Multiple agents share knowledge via the Hive — use
  `agent_scope: "hive"` on `memory_save` for cross-cutting facts or
  `"domain"` for same-profile sharing
- **Federation:** Cross-project memory sharing via a federated hub
- **28 MCP tools:** Full programmatic control when you need it

## Configuration

Settings are configured in `openclaw.plugin.json` (auto-installed):

| Setting            | Default              | Description                        |
|--------------------|----------------------|------------------------------------|
| `mcpCommand`       | `tapps-brain-mcp`    | Command to spawn the MCP server    |
| `profilePath`      | `.tapps-brain/profile.yaml` | Path to custom profile      |
| `tokenBudget`      | `2000`               | Max tokens for memory injection    |
| `captureRateLimit` | `3`                  | Capture every N turns              |

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
