# tapps-brain for OpenClaw

Persistent cross-session memory for your OpenClaw agents. 41 MCP tools, zero LLM
dependency, SQLite-backed, works offline.

---

## Quick Start

**Recommended: install via OpenClaw skill registry (one command):**

```bash
openclaw skill install tapps-brain-memory
```

This installs `tapps-brain[mcp]` from PyPI and configures the ContextEngine plugin
automatically. Requires OpenClaw v2026.3.1+.

**Manual install (any version):**

```bash
pip install tapps-brain[mcp]
```

Then choose an integration mode below based on your OpenClaw version and needs.

---

## Integration Modes

Four ways to integrate tapps-brain, from zero-config to fully custom:

| Mode | OpenClaw Version | Setup Effort | Memory Control | Best For |
|------|-----------------|--------------|----------------|----------|
| **ContextEngine plugin** | v2026.3.7+ | Zero-config | Automatic | Production use |
| **Memory slot plugin** | v2026.3.1+ | Low | memory_search / memory_get replaced | Replacing memory-core |
| **MCP sidecar** | Any | Medium | Full manual | Custom workflows |
| **mcp-adapter** | v2026.3.1–3.6 | Low | Hook-based | Older installations |

---

## Mode 1: ContextEngine Plugin (recommended)

The ContextEngine plugin handles everything automatically — bootstrap, ingest, assemble,
compact, and dispose — with no agent prompting required. Memories are injected into every
model call and captured from every message.

### Requirements

- Node 18+, Python 3.12+
- **OpenClaw v2026.3.7+** (uses `definePluginEntry` ContextEngine API)
- `tapps-brain[mcp]` installed

### Install

If you did not use `openclaw skill install`, build and register manually:

```bash
# Install the Python backend
pip install tapps-brain[mcp]

# Clone the repo to get the TypeScript plugin
git clone https://github.com/wtthornton/tapps-brain.git
cd tapps-brain/openclaw-plugin
npm install
npm run build

# Register with OpenClaw
openclaw plugin install ./openclaw-plugin
```

### Configure

Add to your OpenClaw config (`openclaw.yaml`):

```yaml
plugins:
  slots:
    contextEngine: tapps-brain-memory
  entries:
    tapps-brain-memory:
      enabled: true
      config:
        mcpCommand: tapps-brain-mcp
        tokenBudget: 2000
        captureRateLimit: 3
```

### How it works

The plugin uses OpenClaw's ContextEngine lifecycle hooks:

| Hook | Trigger | What happens |
|------|---------|--------------|
| `bootstrap` | Agent startup | Spawns `tapps-brain-mcp`, imports MEMORY.md on first run, registers Hive agent if enabled |
| `ingest` | New message arrives | Extracts facts via `memory_capture` (rate-limited by `captureRateLimit`) |
| `assemble` | Before model call | Recalls memories → injects `systemPromptAddition` into system prompt |
| `compact` | Context compaction | Flushes context to `memory_ingest`, indexes session chunks |
| `dispose` | Gateway shutdown | Stops MCP child process |

Auto-recall loop:

```
User message  →  ingest()  →  memory_capture()  →  new facts saved
Before model  →  assemble()  →  memory_recall()  →  top memories injected
After context  →  compact()  →  memory_ingest()  →  session archived
```

### Test it

After registering the plugin:

```
# Session 1 — save a fact
"remember that we use PostgreSQL 16 for the main database"

# Session 2 — verify recall
"what do you remember about the database?"
```

If the agent recalls the PostgreSQL fact without prompting, recall is working. Verify
directly:

```bash
tapps-brain search "PostgreSQL" --project-dir /path/to/your/project
```

---

## Mode 2: Memory Slot Plugin

When enabled, this mode replaces OpenClaw's built-in `memory-core` — so `memory_search`
and `memory_get` route through tapps-brain's SQLite store instead of the default plugin.

> **Status:** Planned (EPIC-026). This mode is declared in `openclaw.plugin.json` but
> the backing tool implementations are not yet complete. Use Mode 1 or Mode 3 in the
> meantime.

### Configure (once available)

```yaml
plugins:
  slots:
    contextEngine: tapps-brain-memory
    memory: tapps-brain-memory        # claim the memory slot
  entries:
    tapps-brain-memory:
      enabled: true
      config:
        mcpCommand: tapps-brain-mcp
        tokenBudget: 2000
```

When `slots.memory` is set to `"tapps-brain-memory"`, OpenClaw routes all
`memory_search` and `memory_get` calls through tapps-brain. Falls back gracefully if the
memory slot is not claimed.

---

## Mode 3: MCP Sidecar

Use the MCP sidecar for direct access to all 41 tools, or when you want full manual
control over recall and capture workflows. Works with any OpenClaw version.

### Install

```bash
pip install tapps-brain[mcp]
tapps-brain-mcp --help    # verify install
```

### Configure

Edit `~/.openclaw/openclaw.json`:

```json
{
  "mcp": {
    "servers": {
      "tapps-brain": {
        "command": "tapps-brain-mcp",
        "args": [],
        "transport": "stdio"
      }
    }
  }
}
```

To pin a specific project directory:

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

### Per-agent scoping

To scope tapps-brain to a specific agent only:

```json
{
  "agents": {
    "list": [
      {
        "id": "work",
        "name": "work",
        "mcp": {
          "servers": {
            "tapps-brain": {
              "command": "tapps-brain-mcp",
              "args": ["--project-dir", "/path/to/work/project"],
              "transport": "stdio"
            }
          }
        }
      }
    ]
  }
}
```

### With uv or virtualenvs

Use the full binary path:

```json
{ "command": "/path/to/.venv/bin/tapps-brain-mcp", "args": ["--project-dir", "/path/to/project"], "transport": "stdio" }
```

Or use `uv run` to avoid path issues:

```json
{
  "command": "uv",
  "args": ["run", "--extra", "mcp", "tapps-brain-mcp", "--project-dir", "/path/to/project"],
  "transport": "stdio"
}
```

Restart OpenClaw after editing `openclaw.json` — MCP config is read at startup only.

---

## Mode 4: mcp-adapter (OpenClaw v2026.3.1–3.6)

For OpenClaw versions that do not support the ContextEngine API (`definePluginEntry`) but
do support the `before_agent_start` hook, the plugin automatically registers in
hook-only mode. No configuration changes required — the version is detected at bootstrap.

| OpenClaw version | Plugin behaviour |
|-----------------|-----------------|
| v2026.3.7+ | Full ContextEngine hooks (recommended) |
| v2026.3.1–3.6 | `before_agent_start` hook only — memory injected at session start, no per-turn capture |
| < v2026.3.1 | Tools registered only, no hooks — manual recall/capture via MCP tools |

If you are on v2026.3.1–3.6 or older, upgrade OpenClaw when possible to get per-turn
recall and auto-capture.

---

## Configuration Reference

All settings are configured in the plugin `config` block (Modes 1/2) or via MCP command
args (Mode 3).

### Plugin config settings

| Setting | Default | Description |
|---------|---------|-------------|
| `mcpCommand` | `"tapps-brain-mcp"` | Command used to spawn the MCP server process |
| `profilePath` | `".tapps-brain/profile.yaml"` | Path to custom memory profile YAML |
| `tokenBudget` | `2000` | Max tokens for memories injected via `assemble()` |
| `captureRateLimit` | `3` | Capture facts every N `ingest()` calls; `0` = every call |
| `agentId` | `""` | Unique agent ID for Hive multi-agent sharing |
| `hiveEnabled` | `false` | Enable Hive cross-agent memory sharing |
| `citations` | `"auto"` | Citation footers in `assemble()` output: `"auto"` / `"on"` appends `Source: memory/<tier>/<key>.md`, `"off"` disables |

### MCP server CLI args

| Arg | Description |
|-----|-------------|
| `--project-dir PATH` | Directory where `.tapps-brain/memory/` is stored (default: cwd) |
| `--agent-id ID` | Agent ID for Hive identification |
| `--enable-hive` | Enable Hive at server level |
| `--profile PATH` | Path to profile YAML |

### Custom memory profile

Create `.tapps-brain/profile.yaml` in your project to override scoring and decay:

```yaml
name: my-project
tier_weights:
  architectural: 1.0
  pattern: 0.8
  procedural: 0.6
  context: 0.4
scoring:
  relevance_weight: 0.4
  confidence_weight: 0.3
  recency_weight: 0.15
  frequency_weight: 0.15
```

Set `profilePath` in your plugin config to use it. Built-in profiles:
`repo-brain`, `customer-support`, `home-automation`, `personal-assistant`,
`project-management`, `research-knowledge`.

---

## Feature Matrix

| Feature | ContextEngine | Memory Slot | MCP Sidecar | mcp-adapter |
|---------|:---:|:---:|:---:|:---:|
| Auto-recall before model call | ✅ | — | — | ✅ (session start) |
| Auto-capture from messages | ✅ | — | — | — |
| Pre-compaction flush | ✅ | — | — | — |
| Replaces memory-core tools | — | ✅ | — | — |
| Direct tool access (41 tools) | ✅ | ✅ | ✅ | ✅ |
| Hive multi-agent sharing | ✅ | ✅ | ✅ | — |
| Cross-project federation | ✅ | ✅ | ✅ | — |
| Custom profiles | ✅ | ✅ | ✅ | — |
| Citation footers in recall | ✅ | — | — | — |
| Session memory search | ✅ | — | ✅ | — |
| MCP resources (health, stats) | ✅ | ✅ | ✅ | — |
| MCP prompts | — | — | ✅ | — |
| Minimum OpenClaw version | v2026.3.7 | v2026.3.1 | Any | v2026.3.1 |

---

## All 41 MCP Tools

### Core Memory (CRUD)

| Tool | Description |
|------|-------------|
| `memory_save` | Save or update a memory entry |
| `memory_get` | Retrieve a single entry by key |
| `memory_delete` | Delete an entry |
| `memory_search` | Full-text search with BM25 ranking and filters |
| `memory_list` | List entries with optional tier/tag filters |

### Lifecycle

| Tool | Description |
|------|-------------|
| `memory_recall` | Auto-rank and inject memories for a message |
| `memory_reinforce` | Boost confidence and reset decay clock |
| `memory_ingest` | Extract and save facts from context text |
| `memory_supersede` | Create new version, mark old as invalid |
| `memory_history` | Show full version chain for a key |
| `memory_capture` | Extract durable facts from an agent response |

### Sessions

| Tool | Description |
|------|-------------|
| `memory_index_session` | Index session chunks for future search |
| `memory_search_sessions` | Full-text search past session summaries |

### Hive (Multi-Agent Sharing)

| Tool | Description |
|------|-------------|
| `hive_status` | Show namespaces, entry counts, registered agents |
| `hive_search` | Search shared Hive memories from other agents |
| `hive_propagate` | Manually share an existing local memory to the Hive |
| `agent_register` | Register this agent in the Hive registry |
| `agent_list` | List all registered agents and their profiles |
| `agent_create` | Create and register a new agent programmatically |
| `agent_delete` | Remove an agent registration |

### Federation (Cross-Project)

| Tool | Description |
|------|-------------|
| `federation_status` | Hub status, subscribed projects, and subscription list |
| `federation_subscribe` | Subscribe this project to receive memories from another |
| `federation_unsubscribe` | Remove a subscription |
| `federation_publish` | Publish shared-scope memories to the federation hub |

### Tags & Audit

| Tool | Description |
|------|-------------|
| `memory_list_tags` | List all tags in use |
| `memory_update_tags` | Add or remove tags from an entry |
| `memory_entries_by_tag` | List entries matching a tag |
| `memory_audit` | View JSONL audit log entries |

### Maintenance

| Tool | Description |
|------|-------------|
| `maintenance_consolidate` | Merge similar memories (Jaccard + TF-IDF, no LLM) |
| `maintenance_gc` | Archive stale memories (never deleted) |
| `memory_export` | Export entries as JSON |
| `memory_import` | Import from JSON or markdown |

### Config

| Tool | Description |
|------|-------------|
| `memory_gc_config` | Show GC configuration |
| `memory_gc_config_set` | Update GC thresholds |
| `memory_consolidation_config` | Show consolidation configuration |
| `memory_consolidation_config_set` | Update consolidation thresholds |
| `profile_info` | Show active profile name, layers, scoring config |
| `profile_switch` | Switch to a different built-in profile |

### MCP Resources

| URI | Description |
|-----|-------------|
| `memory://stats` | Entry count, tier distribution, schema version |
| `memory://health` | Store health report with diagnostics |
| `memory://entries/{key}` | Full detail view of a single entry |
| `memory://metrics` | Operation counters and latency histograms |

### MCP Prompts

| Prompt | Description |
|--------|-------------|
| `recall(topic)` | "What do you remember about {topic}?" |
| `store_summary()` | Overview of what is in the memory store |
| `remember(fact)` | Guides the agent to save a fact with appropriate tier/tags |

---

## Memory Tiers and Scoring

### Tiers and decay half-lives

| Tier | Half-life | Use for |
|------|-----------|---------|
| `architectural` | 180 days | System decisions, tech stack, infrastructure |
| `pattern` | 90 days | Coding conventions, API patterns, recurring approaches |
| `procedural` | 30 days | Workflows, deployment steps, one-time procedures |
| `context` | 14 days | Session-specific facts, current task details |

Decay is exponential and lazy — computed on read, no background processes.

### Composite scoring

Search results are ranked by four weighted signals:

| Signal | Default weight | Source |
|--------|---------------|--------|
| Relevance | 40% | BM25 full-text match |
| Confidence | 30% | Decayed confidence score |
| Recency | 15% | Time since last update |
| Frequency | 15% | Access count |

Weights are overridable per profile.

---

## Hive: Multi-Agent Memory Sharing

When multiple OpenClaw agents share a workspace, the Hive enables cross-agent knowledge
sharing. Each agent runs its own `tapps-brain-mcp` process; memories flow between agents
based on `agent_scope`.

### Agent scope

| Scope | Visible to | Use when |
|-------|-----------|----------|
| `private` | Saving agent only (default) | Scratch notes, intermediate reasoning |
| `domain` | Agents sharing the same profile | Conventions relevant to a role (e.g., all `repo-brain` agents) |
| `hive` | All registered agents | Cross-cutting facts: tech stack, API contracts, project decisions |

### Example: planner + coder workflow

```yaml
# openclaw.yaml — orchestrator
config:
  agentId: orchestrator
  hiveEnabled: true
```

1. Orchestrator calls `agent_create(agent_id="planner", profile="repo-brain")`.
2. Orchestrator calls `agent_create(agent_id="coder", profile="repo-brain")`.
3. Planner saves architectural decisions with `agent_scope: "hive"`.
4. Coder recalls and automatically sees the planner's memories merged (0.8 local / 0.2 Hive weight).
5. Each agent's `private` memories remain isolated.

### Conflict resolution

When two agents write to the same key in the Hive:

| Policy | Behavior |
|--------|----------|
| `confidence_max` | Highest confidence wins (default) |
| `last_write_wins` | Most recent write wins |
| `source_authority` | Original author's version wins |
| `supersede` | New version always replaces old |

---

## Migration Guide

### From memory-core (built-in OpenClaw memory)

If you have existing memories in OpenClaw's built-in `memory-core`:

```bash
# Import your MEMORY.md into tapps-brain
tapps-brain import --source MEMORY.md --project-dir /path/to/project

# Or use the MCP migration tool (planned, EPIC-026)
# tapps-brain openclaw migrate --workspace /path/to/project --dry-run
```

The import infers tier from heading level:
- `## Heading` → `architectural`
- `### Heading` → `pattern`
- `#### Heading` → `procedural`
- Body text → `context`

### Upgrading from tapps-brain v0.x (28-tool API)

v1.x expanded from 28 to 41 MCP tools. The original 28 tools are unchanged — no
breaking changes. New tools are additive. See CHANGELOG for the full list.

### Migrating from MCP sidecar to ContextEngine plugin

1. Keep your `mcp` config as-is in `openclaw.json` (the plugin spawns its own MCP
   process, so conflicts are possible — remove the sidecar entry after verifying the
   plugin works).
2. Add the plugin config under `plugins:` in `openclaw.yaml`.
3. Restart OpenClaw.
4. Verify recall is working (Quick Start test above).

---

## Version Compatibility

| tapps-brain version | OpenClaw version | Plugin mode |
|--------------------|-----------------|-------------|
| 1.2.x | v2026.3.7+ | Full ContextEngine (recommended) |
| 1.2.x | v2026.3.1–3.6 | `before_agent_start` hook only |
| 1.2.x | < v2026.3.1 | Tools only (no hooks) |
| 1.1.x | Any | MCP sidecar only |

**`minimumVersion` in `openclaw.plugin.json` is set to `"2026.3.1"`** — OpenClaw will
warn on older versions. The plugin still registers tools as a fallback, but automatic
recall and capture will not work without hook support.

To check your OpenClaw version:

```bash
openclaw --version
```

---

## Troubleshooting

### `memory_search returns 0` results

**Cause:** Most common cause is the `_MIN_SCORE` threshold (0.3) combined with source
trust scoring. Short queries like `"v1"` or single words can produce composite scores below
the cutoff.

**Fixes:**
1. Use a longer, more descriptive query — `"PostgreSQL database config"` scores higher than `"db"`.
2. Verify the store is not empty: `tapps-brain list --project-dir /path/to/project`.
3. Check `--project-dir` points to the right directory. The store lives at
   `{project-dir}/.tapps-brain/memory/memory.db`. If omitted, defaults to the MCP
   server's working directory, which may differ from your project root.
4. Try `memory_list` to confirm entries exist, then `memory_search` with a term that
   appears verbatim in a saved entry.

### `MCP process crashes` / plugin stops responding

**Cause:** The MCP child process (`tapps-brain-mcp`) exited unexpectedly.

**Fixes:**
1. The plugin (v1.2+) includes automatic reconnection logic: up to 3 retries with
   exponential backoff (100/200/400ms). If the process recovers, tool calls resume
   automatically.
2. If the process does not recover after retries, check the logs:
   ```bash
   # Show recent MCP server output
   tapps-brain-mcp --project-dir /path/to/project 2>&1 | head -50
   ```
3. Verify disk space — SQLite writes fail silently on full disk.
4. On WSL with Windows drives, check mount options for `/mnt/c/` — WAL mode requires
   `metadata=full` or equivalent for reliable writes.
5. Restart the OpenClaw gateway. The plugin's `dispose` hook stops the process cleanly.

### `mcp package required` error

```bash
pip install tapps-brain[mcp]
```

### OpenClaw does not see tools after config change

Restart the OpenClaw gateway — MCP config is read at startup only.

### No memories returned after saving

Check that `--project-dir` in your MCP sidecar config matches the directory where
memories were saved. Each project has an isolated store at `{project-dir}/.tapps-brain/`.

### Permission errors on Windows / WSL

Ensure `.tapps-brain/memory/` is writable. On WSL with `/mnt/c/` paths, mount with
`metadata` option in `/etc/wsl.conf`:

```ini
[automount]
options = "metadata"
```

### Inspect the server manually

Use the MCP Inspector to verify tool discovery without OpenClaw:

```bash
npx @modelcontextprotocol/inspector tapps-brain-mcp --project-dir /path/to/project
```

### Check store health

Ask your agent: *"read the memory://health resource"* — returns a health report with
entry counts, tier distribution, schema version, and last GC run.

---

## Where data is stored

```
{project-dir}/
└── .tapps-brain/
    └── memory/
        ├── memory.db              # SQLite (WAL mode, FTS5)
        ├── memory_log.jsonl       # Audit log of all mutations
        └── archive.jsonl          # GC'd entries (never deleted)
```

Global federation hub:

```
~/.tapps-brain/
├── memory/
│   └── federated.db              # Cross-project federation hub
└── hive/
    └── hive.db                   # Multi-agent Hive store
```

Max 500 entries per project. Lowest-confidence entries are evicted when the cap is hit.

---

## Links

- [Main README](../../README.md) — full project overview
- [MCP Server Guide](mcp.md) — detailed tool/resource/prompt reference
- [Auto-Recall Guide](auto-recall.md) — recall orchestrator configuration
- [Federation Guide](federation.md) — cross-project memory sharing
- [OpenClaw Plugin README](../../openclaw-plugin/README.md) — plugin development guide
