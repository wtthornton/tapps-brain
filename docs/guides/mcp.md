# MCP Server: Using tapps-brain with AI Assistants

tapps-brain exposes its full API via the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP), making persistent memory available to Claude Code, Cursor, VS Code Copilot, and any other MCP-compatible client.

## Installation

```bash
pip install tapps-brain[mcp]
```

Or with uv:

```bash
uv pip install tapps-brain[mcp]
```

This installs the `tapps-brain-mcp` command, which runs a stdio-based MCP server.

## Client Configuration

### Claude Code

Add to your project's `.mcp.json` (or `~/.claude/mcp.json` for global):

```json
{
  "mcpServers": {
    "tapps-brain": {
      "command": "tapps-brain-mcp",
      "args": ["--project-dir", "/path/to/your/project"]
    }
  }
}
```

Omit `--project-dir` to use the current working directory.

### Cursor

In Cursor Settings > MCP, add a new server:

- **Name:** `tapps-brain`
- **Type:** `command`
- **Command:** `tapps-brain-mcp --project-dir /path/to/your/project`

Or add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "tapps-brain": {
      "command": "tapps-brain-mcp",
      "args": ["--project-dir", "/path/to/your/project"]
    }
  }
}
```

### VS Code Copilot

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "tapps-brain": {
      "type": "stdio",
      "command": "tapps-brain-mcp",
      "args": ["--project-dir", "${workspaceFolder}"]
    }
  }
}
```

### Generic MCP Client

The server uses stdio transport. Start it with:

```bash
tapps-brain-mcp --project-dir /path/to/project
```

Or run as a Python module:

```bash
python -m tapps_brain.mcp_server --project-dir /path/to/project
```

## Available Tools

### Core Memory Operations

| Tool | Description |
|------|-------------|
| `memory_save` | Save or update a memory entry |
| `memory_get` | Retrieve a single entry by key |
| `memory_delete` | Delete an entry by key |
| `memory_search` | Full-text search with tier/scope/point-in-time filters |
| `memory_list` | List entries with optional filters |

**Example — saving a memory:**

```
memory_save(
    key="auth-pattern",
    value="This project uses JWT tokens with refresh rotation",
    tier="architectural",
    source="human",
    tags=["auth", "security"]
)
```

**Example — searching:**

```
memory_search(query="authentication", tier="architectural")
```

### Lifecycle Tools

| Tool | Description |
|------|-------------|
| `memory_recall` | Run auto-recall for a message, returning ranked relevant memories |
| `memory_reinforce` | Boost a memory's confidence after it proved useful |
| `memory_ingest` | Extract and store durable facts from conversation text |
| `memory_supersede` | Create a new version of a memory (bi-temporal versioning) |
| `memory_history` | Show the full version chain for a key |

**Example — recall:**

```
memory_recall(message="How does authentication work in this project?")
```

Returns a `memory_section` string with ranked memories, token count, and timing.

**Example — versioning:**

```
memory_supersede(
    old_key="auth-pattern",
    new_value="Migrated to OAuth2 with PKCE flow",
    tier="architectural"
)
```

### Session & Capture Tools

| Tool | Description |
|------|-------------|
| `memory_index_session` | Index session chunks (summaries/key facts) for future search |
| `memory_search_sessions` | Search past session summaries by relevance |
| `memory_capture` | Extract and persist new facts from an agent response |

**Example — indexing a session:**

```
memory_index_session(
    session_id="session-abc",
    chunks=["Refactored auth middleware", "Added rate limiting to API"]
)
```

**Example — searching sessions:**

```
memory_search_sessions(query="rate limiting", limit=5)
```

**Example — capturing facts from a response:**

```
memory_capture(
    response="We decided to use Redis for caching and set TTL to 15 minutes.",
    source="agent"
)
```

### Federation Tools

| Tool | Description |
|------|-------------|
| `federation_status` | Show hub status, registered projects, and subscriptions |
| `federation_subscribe` | Subscribe a project to receive memories from other projects |
| `federation_unsubscribe` | Remove a project's federation subscription |
| `federation_publish` | Publish shared-scope memories to the federation hub |

Federation enables cross-project memory sharing. See the [Federation Guide](federation.md) for details.

### Maintenance Tools

| Tool | Description |
|------|-------------|
| `maintenance_consolidate` | Merge similar memories (deterministic, Jaccard + TF-IDF) |
| `maintenance_gc` | Archive stale memories (supports `dry_run`) |
| `memory_export` | Export entries as JSON (with tier/scope/confidence filters) |
| `memory_import` | Import entries from JSON |

## Resources

Resources are read-only views that MCP clients can pull into context:

| URI | Description |
|-----|-------------|
| `memory://stats` | Entry count, tier distribution, schema version |
| `memory://health` | Store health report |
| `memory://entries/{key}` | Full detail view of a single entry |
| `memory://metrics` | Operation counters and latency histograms |

## Prompts

Prompts are user-invoked workflow templates:

| Prompt | Arguments | Description |
|--------|-----------|-------------|
| `recall` | `topic` | "What do you remember about {topic}?" — runs auto-recall |
| `store_summary` | (none) | Overview of what's in the memory store |
| `remember` | `fact` | "Remember that {fact}" — guides the AI to save with appropriate tier/tags |

## Memory Tiers

When saving memories, choose the tier that matches the information's durability:

| Tier | Half-life | Use for |
|------|-----------|---------|
| `architectural` | 180 days | System-level decisions, tech stack choices |
| `pattern` | 90 days | Coding conventions, API patterns |
| `procedural` | 30 days | Workflows, deployment steps |
| `context` | 14 days | Session-specific facts, current task details |

## Scopes

| Scope | Visibility |
|-------|------------|
| `project` | Available across all sessions in this project |
| `branch` | Scoped to the current git branch |
| `session` | Ephemeral, current session only |

## Troubleshooting

**Server won't start — "mcp package required"**
Install the MCP extra: `pip install tapps-brain[mcp]`

**No memories returned**
Check that `--project-dir` points to the correct project root. The store lives in `{project-dir}/.tapps-brain/memory/`.

**Permission errors on WSL**
Ensure the store directory is writable. On WSL with Windows drives, check that the mount allows writes (`/mnt/c/...`).

**Testing the server manually**
Use the [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) to connect and verify:

```bash
npx @modelcontextprotocol/inspector tapps-brain-mcp --project-dir /path/to/project
```
