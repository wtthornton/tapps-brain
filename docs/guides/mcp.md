# MCP Server: Using tapps-brain with AI Assistants

tapps-brain exposes its full API via the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP), making persistent memory available to Claude Code, Cursor, VS Code Copilot, and any other MCP-compatible client.

**Tool and resource counts** ship in [`docs/generated/mcp-tools-manifest.json`](../generated/mcp-tools-manifest.json) (`tool_count`, `resource_count`, lists). Regenerate after `mcp_server.py` changes: `python scripts/generate_mcp_tool_manifest.py`. Do not cite stale integers in other docs unless labeled historical.

## Project identity (multi-tenant)

A single deployed `tapps-brain` serves many client projects. Every connection **must declare a `project_id`** so the server can load the right memory profile and partition data. See [ADR-010](../planning/adr/ADR-010-multi-tenant-project-registration.md) and [EPIC-069](../planning/epics/EPIC-069.md) for the design.

**Resolution precedence** (first match wins):

1. Per-call MCP `_meta.project_id` (override on a single tool call)
2. HTTP header `X-Tapps-Project: <id>` (streamable HTTP / SSE transport)
3. Env var `TAPPS_BRAIN_PROJECT=<id>` (stdio transport)
4. Literal `"default"` (dev only — strict-mode deployments reject this)

**Before you connect:** register your project once against the deployed brain:

```bash
tapps-brain project register alpaca --profile ./profile.yaml
tapps-brain project approve alpaca   # strict-mode deployments only
```

Or via the admin HTTP surface (requires `TAPPS_BRAIN_ADMIN_TOKEN` on the server):

```bash
# Register (or overwrite) a project profile
curl -X POST http://brain.internal:8088/admin/projects \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d @payload.json       # {"project_id":"alpaca","profile":{…},"approved":true}

# Inspect / list / approve / delete
curl      http://brain.internal:8088/admin/projects            -H "Authorization: Bearer $ADMIN_TOKEN"
curl      http://brain.internal:8088/admin/projects/alpaca     -H "Authorization: Bearer $ADMIN_TOKEN"
curl -X POST http://brain.internal:8088/admin/projects/alpaca/approve -H "Authorization: Bearer $ADMIN_TOKEN"
curl -X DELETE http://brain.internal:8088/admin/projects/alpaca       -H "Authorization: Bearer $ADMIN_TOKEN"
```

The `profile.yaml` format is unchanged from [EPIC-010](../planning/epics/EPIC-010.md) — it is now a seed document consumed at registration time rather than read by the server at runtime.

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

**stdio (local) transport:**

```json
{
  "mcpServers": {
    "tapps-brain": {
      "command": "tapps-brain-mcp",
      "env": {
        "TAPPS_BRAIN_PROJECT": "alpaca",
        "TAPPS_BRAIN_DATABASE_URL": "postgresql://brain:brain@localhost:5433/brain"
      }
    }
  }
}
```

**Deployed-brain (HTTP) transport:**

```json
{
  "mcpServers": {
    "tapps-brain": {
      "url": "http://brain.internal:8088/mcp",
      "headers": { "X-Tapps-Project": "alpaca" }
    }
  }
}
```

The legacy `--project-dir` flag is accepted for local dev but no longer selects a profile — identity comes from `TAPPS_BRAIN_PROJECT` / `X-Tapps-Project`.

### Cursor

In Cursor Settings > MCP, add a new server:

- **Name:** `tapps-brain`
- **Type:** `command`
- **Command:** `tapps-brain-mcp`
- **Env:** `TAPPS_BRAIN_PROJECT=<your-project-id>`

Or add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "tapps-brain": {
      "command": "tapps-brain-mcp",
      "env": { "TAPPS_BRAIN_PROJECT": "your-project-id" }
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
      "env": { "TAPPS_BRAIN_PROJECT": "your-project-id" }
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
        "env": { "TAPPS_BRAIN_PROJECT": "your-project-id" },
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

## Transport

tapps-brain's MCP server supports the **stdio** transport, which is the default and only transport available today.

### stdio (default)

The server communicates over standard input/output using JSON-RPC messages. This is the transport used by Claude Code, Cursor, VS Code Copilot, and most MCP clients:

```bash
tapps-brain-mcp --project-dir /path/to/project
```

All client configuration examples in this guide use stdio. The server process is launched by the client and communicates via stdin/stdout pipes.

### SSE (Server-Sent Events)

SSE transport is **not currently supported** by the tapps-brain MCP server. SSE would allow the server to run as a long-lived HTTP service that clients connect to over the network, which is useful for:

- Remote/shared server deployments
- Multi-client access to a single server instance
- Environments where subprocess spawning is restricted

SSE transport support depends on the upstream MCP SDK. Track the [MCP SDK changelog](https://github.com/modelcontextprotocol/python-sdk/releases) for SSE availability.

### MCP SDK version compatibility

| tapps-brain | MCP SDK | Transport | Notes |
|-------------|---------|-----------|-------|
| 2.0.x | `mcp >=1.2.0,<2` | stdio | Current supported range |

The MCP SDK pin (`>=1.2.0,<2`) in `pyproject.toml` allows compatible minor/patch updates within the 1.x series. If the MCP SDK releases 2.0, a tapps-brain update will be required; check the changelog for migration notes.

---

## Available Tools

### Core Memory Operations

| Tool | Description |
|------|-------------|
| `memory_save` | Save or update a memory entry |
| `memory_get` | Retrieve a single entry by key |
| `memory_delete` | Delete an entry by key |
| `memory_search` | Full-text search with tier/scope/point-in-time filters |
| `memory_list` | List entries with optional filters |
| `memory_list_groups` | List distinct group names used in the store (for `group=` filter) |

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
| `tapps_brain_session_end` | Record an end-of-session episodic memory (summary, tags, optional daily note) |

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
| `memory_profile_onboarding` | Return Markdown onboarding guidance for the active profile (tiers, scoring, limits, Hive hints) |

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
| `hive_propagate` | Manually propagate a local memory to the Hive (optional `force`, `dry_run`) |
| `hive_push` | Batch-promote local memories to the Hive (`push_all`, `tags`, `tier`, `keys`, `dry_run`, `force`) |
| `hive_write_revision` | Return the current Hive write-notification revision (monotonic counter for polling) |
| `hive_wait_write` | Long-poll until the Hive write revision exceeds `since_revision` or timeout |
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

### Knowledge Graph Tools

| Tool | Description |
|------|-------------|
| `memory_relations` | Get relations for a memory entry |
| `memory_find_related` | BFS traversal from an entity |
| `memory_query_relations` | Query relation triples |
| `memory_relations_get_batch` | Return relations for multiple memory keys in one call |

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

### Operator Tools (advanced/maintenance)

Operator tools are **not available in default agent sessions**. They are intended for
operators running dedicated maintenance or monitoring sessions, not for regular
agent workflows. Enable them via the `--enable-operator-tools` CLI flag or by setting
`TAPPS_BRAIN_OPERATOR_TOOLS=1` in the server's environment.

**Do not enable operator tools in shared multi-tenant agent sessions.** They expose
bulk data operations (export, import), trigger destructive mutations (GC, consolidation),
and surface configuration writes that can affect all agents sharing the same store.

To start the server with operator tools:

```bash
# CLI flag
tapps-brain-mcp --project-dir /path/to/project --enable-operator-tools

# Environment variable
TAPPS_BRAIN_OPERATOR_TOOLS=1 tapps-brain-mcp --project-dir /path/to/project
```

In a client config (e.g. `.mcp.json`), pass the flag as an arg:

```json
{
  "mcpServers": {
    "tapps-brain-ops": {
      "command": "tapps-brain-mcp",
      "args": ["--project-dir", "/path/to/project", "--enable-operator-tools"]
    }
  }
}
```

| Tool | Description | Risk |
|------|-------------|------|
| `maintenance_consolidate` | Merge similar memories (deterministic, Jaccard + TF-IDF) | Bulk mutation |
| `maintenance_gc` | Archive stale memories (supports `dry_run`) | Bulk archive |
| `maintenance_stale` | List GC stale candidates with reasons (read-only JSON: `count`, `entries`) | Read-only |
| `tapps_brain_health` | Structured health report (store connectivity, Hive status, integrity) | Read-only |
| `memory_gc_config` | Read current GC thresholds | Read-only |
| `memory_gc_config_set` | Update GC thresholds at runtime | Config write |
| `memory_consolidation_config` | Read current auto-consolidation settings | Read-only |
| `memory_consolidation_config_set` | Update auto-consolidation settings at runtime | Config write |
| `memory_export` | Export entries as JSON (with tier/scope/confidence filters) | Data exposure |
| `memory_import` | Import entries from JSON | Bulk write |
| `tapps_brain_relay_export` | Build cross-node memory relay payload (GitHub #19) | Data exposure |
| `flywheel_evaluate` | Run a BEIR-style eval suite against the store | Compute-heavy |
| `flywheel_hive_feedback` | Aggregate and apply Hive cross-project feedback penalties | Hive mutation |

**CLI-only (not exposed as MCP tools):**

- `tapps-brain maintenance consolidation-threshold-sweep` — read-only consolidation threshold sensitivity (`evaluation.run_consolidation_threshold_sweep`; `--json` supported).
- `tapps-brain maintenance consolidation-merge-undo CONSOLIDATED_KEY` — revert one auto-consolidation merge using the last matching `consolidation_merge` audit row (`MemoryStore.undo_consolidation_merge`; writes `consolidation_merge_undo` event to Postgres `audit_log`; `--json` supported).

## Resources

Resources are read-only views that MCP clients can pull into context:

| URI | Description |
|-----|-------------|
| `memory://stats` | Entry count, tier distribution, schema version, package version, profile name, optional `profile_seed_version` (`profile.seeding.seed_version`) |
| `memory://agent-contract` | Agent integration JSON (versions, profile layers, recall empty-reason codes); see [Agent integration](agent-integration.md) |
| `memory://health` | Full `StoreHealthReport` JSON (includes `profile_seed_version` when set) |
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

## Tool stability and versioning

All MCP tools shipped in the current release are **stable (v1)**. Clients can rely on their parameter shapes and return types remaining backward-compatible within the v1 series.

### Deprecation policy

When a tool is scheduled for removal or replacement:

1. A `deprecated` annotation is added to the tool's metadata in `mcp_server.py` at least **one minor release** before removal.
2. The tool continues to function normally during the deprecation window; callers receive a `deprecated` field in responses where applicable.
3. Migration guidance is included in the release notes and in this guide.
4. After the deprecation window, the tool is removed in the next major version bump.

No tools are currently deprecated.

### Batch save (future)

A dedicated `memory_save_batch` tool is planned for use cases where per-item round-trip latency dominates (e.g., relay import, bulk seed). Until then, callers that need multi-save should use the relay import path (`tapps_brain_relay_export` or `tapps-brain relay import`), which already processes items in a single store transaction.

### Rate limiting in MCP context

The MCP server inherits the store-level sliding-window rate limiter (`SlidingWindowRateLimiter`). Key behaviors:

- **Default limits:** 20 writes per minute, 100 writes per session. Configurable via `RateLimiterConfig`.
- **Warn-only:** Rate limit violations emit structured log warnings but do **not** block the write. This prevents anomalous bursts from silently corrupting workflow without hard-failing legitimate automation.
- **Batch exemptions:** Certain batch contexts are exempt from per-minute counting: `import_markdown`, `memory_relay`, `seed`, `federation_sync`, `consolidate`. MCP tools that perform bulk operations set the appropriate batch context automatically.
- **Per-tool telemetry (future):** Per-tool write counters are a planned addition to the `memory://metrics` resource so operators can identify which MCP tools generate the most write traffic.

## Maintainers: release gate and doc consistency

Before a release, the repo runs an automated gate that includes this MCP surface (counts in `docs/generated/mcp-tools-manifest.json`) end-to-end with Python packaging and the OpenClaw plugin:

- **Full gate:** `bash scripts/release-ready.sh` (see `scripts/publish-checklist.md`)
- **OpenClaw-facing docs only:** `python scripts/check_openclaw_docs_consistency.py`

If you add or rename MCP tools/resources, update `openclaw-skill/SKILL.md` frontmatter and run `python scripts/generate_mcp_tool_manifest.py` so the consistency script reads the updated counts.
