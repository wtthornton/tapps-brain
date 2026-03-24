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

### OpenClaw

Add to `~/.openclaw/openclaw.json` (top-level `mcp` key):

```json
{
  "mcp": {
    "servers": {
      "tapps-brain": {
        "command": "tapps-brain-mcp",
        "args": ["--project-dir", "/path/to/your/project"],
        "transport": "stdio"
      }
    }
  }
}
```

Restart the gateway after saving. See the [OpenClaw Guide](openclaw.md) for per-agent config, virtual environment tips, and troubleshooting.

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

### Profile Tools

| Tool | Description |
|------|-------------|
| `profile_info` | Return the active profile's name, layers, scoring config, and Hive settings |
| `profile_switch` | Switch to a different built-in profile by name |

**Example — checking the active profile:**

```
profile_info()
→ { "name": "repo-brain", "layers": [...], "scoring": {...} }
```

**Example — switching profiles:**

```
profile_switch(name="personal-assistant")
```

Profiles configure layers, decay models, scoring weights, promotion rules, and limits. See the [Profile Design Guide](profiles.md) and [Profile Catalog](profile-catalog.md).

### Hive Tools (Multi-Agent Shared Brain)

| Tool | Description |
|------|-------------|
| `hive_status` | Return namespaces, entry counts per namespace, and registered agents |
| `hive_search` | Search the Hive with optional namespace filter |
| `hive_propagate` | Manually propagate a local memory to the Hive |
| `agent_register` | Register an agent in the Hive registry (id, profile, skills) |
| `agent_create` | Composite: register + validate profile + namespace assignment |
| `agent_list` | List all registered agents |
| `agent_delete` | Remove an agent registration |

**Example — checking Hive status:**

```
hive_status()
→ { "namespaces": {"universal": 5, "developer": 12}, "agents": [...] }
```

**Example — searching the Hive:**

```
hive_search(query="authentication patterns", namespace="universal")
```

**Example — registering an agent:**

```
agent_register(agent_id="qa-agent", profile="repo-brain", skills="testing,review")
```

The Hive enables cross-agent memory sharing with namespace isolation and conflict resolution. See the [Hive Guide](hive.md).

### Federation Tools

| Tool | Description |
|------|-------------|
| `federation_status` | Show hub status, registered projects, and subscriptions |
| `federation_subscribe` | Subscribe a project to receive memories from other projects |
| `federation_unsubscribe` | Remove a project's federation subscription |
| `federation_publish` | Publish shared-scope memories to the federation hub |

Federation enables cross-project memory sharing. See the [Federation Guide](federation.md) for details.

### Knowledge Graph Tools

| Tool | Description |
|------|-------------|
| `memory_relations` | Get relations for a memory entry |
| `memory_find_related` | BFS traversal from an entity |
| `memory_query_relations` | Query relation triples |

### Tag Management Tools

| Tool | Description |
|------|-------------|
| `memory_list_tags` | List all tags in the store with usage counts |
| `memory_update_tags` | Atomically add and/or remove tags on an entry |
| `memory_entries_by_tag` | List entries that include a given tag |

### Feedback Tools (EPIC-029)

| Tool | Description |
|------|-------------|
| `feedback_rate` | Record explicit recall quality (`helpful` / `partial` / `irrelevant` / `outdated`) |
| `feedback_gap` | Report a knowledge gap (missing coverage for a query) |
| `feedback_issue` | Flag an entry as stale, wrong, duplicate, or harmful |
| `feedback_record` | Record a custom feedback event type (when registered in profile) |
| `feedback_query` | Query stored feedback with filters (`event_type`, time range, `entry_key`, …) |

### Diagnostics Tools (EPIC-030)

| Tool | Description |
|------|-------------|
| `diagnostics_report` | Composite quality scorecard, dimensions, anomalies, circuit breaker state |
| `diagnostics_history` | Rolling history of diagnostics snapshots (pruned per retention) |

### Flywheel Tools (EPIC-031)

| Tool | Description |
|------|-------------|
| `flywheel_process` | Apply Bayesian feedback processing to update entry confidence signals |
| `flywheel_gaps` | List prioritized knowledge gaps (optional semantic clustering) |
| `flywheel_report` | Generate a markdown quality report from diagnostics + feedback + gaps |
| `flywheel_evaluate` | Run a BEIR-style eval suite against the store |
| `flywheel_hive_feedback` | Aggregate Hive-scoped feedback and cross-project signals |

### Audit Tools

| Tool | Description |
|------|-------------|
| `memory_audit` | Query the audit trail with optional filters |

### Maintenance Tools

| Tool | Description |
|------|-------------|
| `maintenance_consolidate` | Merge similar memories (deterministic, Jaccard + TF-IDF) |
| `maintenance_gc` | Archive stale memories (supports `dry_run`) |
| `maintenance_gc_config` | View or set GC thresholds at runtime |
| `maintenance_consolidation_config` | View or set consolidation configuration |
| `maintenance_health` | Store health report |
| `maintenance_migrate` | Run schema migrations |
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
| `memory://feedback` | Recent feedback events (up to 500); use `feedback_query` for filtered queries |
| `memory://diagnostics` | Latest diagnostics report JSON (includes circuit breaker; read-only, does not append history) |
| `memory://report` | Latest flywheel quality report payload (generates default window if none stored yet) |

## Prompts

Prompts are user-invoked workflow templates:

| Prompt | Arguments | Description |
|--------|-----------|-------------|
| `recall` | `topic` | "What do you remember about {topic}?" — runs auto-recall |
| `store_summary` | (none) | Overview of what's in the memory store |
| `remember` | `fact` | "Remember that {fact}" — guides the AI to save with appropriate tier/tags |

## Memory Tiers

The default `repo-brain` profile defines four tiers. Custom profiles can define any number of tiers with custom names — use `profile_info()` to see the active layers.

| Tier (default) | Half-life | Use for |
|------|-----------|---------|
| `architectural` | 180 days | System-level decisions, tech stack choices |
| `pattern` | 60 days | Coding conventions, API patterns |
| `procedural` | 30 days | Workflows, deployment steps |
| `context` | 14 days | Session-specific facts, current task details |

See the [Profile Catalog](profile-catalog.md) for other built-in profiles with different tier definitions.

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

## Maintainers: release gate and doc consistency

Before a release, the repo runs an automated gate that includes this MCP surface (54 tools, 7 resources) end-to-end with Python packaging and the OpenClaw plugin:

- **Full gate:** `bash scripts/release-ready.sh` (see `scripts/publish-checklist.md`)
- **OpenClaw-facing docs only:** `python scripts/check_openclaw_docs_consistency.py`

If you add or rename MCP tools/resources, update `openclaw-skill/SKILL.md` frontmatter and the `EXPECTED_*` constants in `scripts/check_openclaw_docs_consistency.py`.
