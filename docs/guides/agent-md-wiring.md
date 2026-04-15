# Agent.md Wiring Guide

This guide explains how to grant tapps-brain MCP access in an `AGENT.md` file
safely — without inadvertently exposing operator-level (destructive) tools to
your coding agents.

## Background: Two Servers, Two Trust Levels

As of STORY-070.9, tapps-brain ships **two separate MCP entry points**:

| Entry point | Command | Operator tools | Intended for |
|---|---|---|---|
| `tapps-brain-mcp` | standard server | **Never** (even if `TAPPS_BRAIN_OPERATOR_TOOLS=1`) | Agent sessions, AGENT.md |
| `tapps-brain-operator-mcp` | operator server | **Always** | Human operators, CI pipelines |

**Operator tools** are: `maintenance_gc`, `maintenance_consolidate`,
`maintenance_stale`, `tapps_brain_health`, `memory_gc_config`,
`memory_gc_config_set`, `memory_consolidation_config`,
`memory_consolidation_config_set`, `memory_export`, `memory_import`,
`tapps_brain_relay_export`, `flywheel_evaluate`, `flywheel_hive_feedback`.

Granting the standard server is safe: no agent can accidentally run GC or wipe
memories via a rogue prompt injection.

## Safe AGENT.md Grant (Recommended)

Add the following block to your `AGENT.md` (or `.claude/mcp.json`) to give an
agent read/write memory access **without** operator tools:

```json
{
  "mcpServers": {
    "tapps-brain": {
      "type": "stdio",
      "command": "tapps-brain-mcp",
      "args": ["--project-dir", "${workspaceFolder}"],
      "env": {
        "TAPPS_BRAIN_DATABASE_URL": "${env:TAPPS_BRAIN_DATABASE_URL}",
        "TAPPS_BRAIN_AGENT_ID": "${env:TAPPS_BRAIN_AGENT_ID}",
        "TAPPS_BRAIN_HIVE_DSN": "${env:TAPPS_BRAIN_HIVE_DSN}"
      }
    }
  }
}
```

> **Note:** `TAPPS_BRAIN_OPERATOR_TOOLS=1` has **no effect** on the standard
> server. Even if that variable is set in the environment, the standard server
> will not expose operator tools.

## Operator Grant (Human / CI Only)

Use `tapps-brain-operator-mcp` only when the session needs maintenance access:

```json
{
  "mcpServers": {
    "tapps-brain-operator": {
      "type": "stdio",
      "command": "tapps-brain-operator-mcp",
      "args": ["--project-dir", "${workspaceFolder}"],
      "env": {
        "TAPPS_BRAIN_DATABASE_URL": "${env:TAPPS_BRAIN_DATABASE_URL}",
        "TAPPS_BRAIN_AGENT_ID": "operator"
      }
    }
  }
}
```

Typical operator workflows:
- `maintenance_gc` — archive stale memories
- `maintenance_consolidate` — merge near-duplicate entries
- `memory_export` / `memory_import` — backup and restore
- `memory_gc_config_set` — adjust GC thresholds

## Stdio vs HTTP Transport

Both servers support the same stdio protocol shown above. When running as a
shared network service (HTTP adapter), the standard and operator split is
enforced at the HTTP layer via `TAPPS_BRAIN_ADMIN_TOKEN` — see
[HTTP Adapter guide](http-adapter.md) and
[Deployment guide](deployment.md) for details.

## AgentForge AGENT.md Example

For AgentForge workers that use `tapps-brain` as their memory backend:

```yaml
# .agentforge/AGENT.md  (AgentForge project root)
mcp:
  - name: tapps-brain
    command: tapps-brain-mcp
    args:
      - --project-dir
      - /workspace
    env:
      TAPPS_BRAIN_DATABASE_URL: $TAPPS_BRAIN_DATABASE_URL
      TAPPS_BRAIN_HIVE_DSN: $TAPPS_BRAIN_HIVE_DSN
      TAPPS_BRAIN_AGENT_ID: $AGENTFORGE_WORKER_ID
```

This wires in memory tools (`memory_save`, `memory_recall`, `hive_search`,
etc.) while keeping GC and consolidation out of the agent's tool belt.

## Summary

- Grant **`tapps-brain-mcp`** in `AGENT.md` — always safe, no operator tools.
- Grant **`tapps-brain-operator-mcp`** only for human operator or CI contexts.
- `TAPPS_BRAIN_OPERATOR_TOOLS=1` is ignored by the standard server.
- Both servers share the same service layer, Postgres backend, and error taxonomy.
