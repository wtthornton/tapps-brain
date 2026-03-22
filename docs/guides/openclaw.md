# tapps-brain for OpenClaw

Persistent cross-session memory for your OpenClaw agents. 28 MCP tools, zero LLM dependency, works offline.

There are two ways to integrate tapps-brain with OpenClaw:

| Method | Best for | Setup |
|--------|----------|-------|
| **ContextEngine plugin** | Automatic recall/capture, zero-config | `npm install` + `plugin.json` |
| **MCP sidecar** | Manual control, custom workflows | `pip install` + `openclaw.json` |

The **ContextEngine plugin** (recommended) handles everything automatically — bootstrap,
recall, capture, and pre-compaction flush — with no agent prompting required. The **MCP
sidecar** gives you direct access to all 28 tools for custom integrations.

---

## Option A: ContextEngine Plugin (recommended)

### 1. Install

**Prerequisites:** Node 18+, Python 3.12+, tapps-brain with MCP support.

```bash
# Install the Python backend
pip install tapps-brain[mcp]

# Clone the repo to get the OpenClaw plugin source
git clone https://github.com/wtthornton/tapps-brain.git
cd tapps-brain/openclaw-plugin
npm install
npm run build
```

> **Note:** The ContextEngine plugin is a TypeScript package inside the tapps-brain
> repository at `openclaw-plugin/`. You need to clone the repo (or download that
> directory) to build it. The `pip install` only provides the Python backend and MCP
> server — the OpenClaw plugin wrapper is separate.

### 2. Register the plugin

Copy the built `openclaw-plugin/` directory into your OpenClaw plugins directory (or
symlink it), then add the plugin to your OpenClaw config:

```json
{
  "plugins": {
    "tapps-brain": {
      "path": "./plugins/tapps-brain",
      "slot": "ContextEngine",
      "settings": {
        "mcpCommand": "tapps-brain-mcp",
        "tokenBudget": 2000,
        "captureRateLimit": 3
      }
    }
  }
}
```

### 3. How it works

The plugin registers four hooks that run automatically:

#### Bootstrap

On startup, the plugin spawns `tapps-brain-mcp` as a child process (JSON-RPC over
stdio). On first run it imports your workspace `MEMORY.md` and runs an initial recall to
prime the session context.

#### Auto-recall (ingest hook)

Every user message triggers a `memory_recall` call. Relevant memories are injected into
the agent's context as a system prefix, respecting the configured token budget. Keys are
deduplicated within the session to avoid repeating the same facts.

```
User message → memory_recall() → ranked memories injected into context
```

#### Auto-capture (afterTurn hook)

After the agent responds, the plugin calls `memory_capture` to extract and persist new
facts. To avoid noise, capture is rate-limited: by default it fires once every 3 turns
(configurable via `captureRateLimit`).

```
Agent response → memory_capture() → new facts saved to store
```

#### Pre-compaction flush (compact hook)

When OpenClaw compacts the context window, the plugin flushes the about-to-be-discarded
context into tapps-brain via `memory_ingest` and indexes the session chunks with
`memory_index_session`. This ensures no knowledge is lost during compaction.

### 4. Test it

After registering the plugin and restarting OpenClaw, verify the integration is working:

1. Start a new OpenClaw session. The bootstrap hook should log that `tapps-brain-mcp`
   has spawned.

2. Ask your agent:

```
"remember that we use PostgreSQL 16 for the main database"
```

3. Start a **new session** and ask:

```
"what do you remember about the database?"
```

If the agent recalls the PostgreSQL fact without being prompted, auto-recall is working.
You can also check the store directly:

```bash
tapps-brain search "PostgreSQL" --project-dir /path/to/your/project
```

### 5. Plugin settings

| Setting | Default | Description |
|---------|---------|-------------|
| `mcpCommand` | `"tapps-brain-mcp"` | Command to spawn the MCP server |
| `profilePath` | `".tapps-brain/profile.yaml"` | Path to memory profile config |
| `tokenBudget` | `2000` | Max tokens for injected memories |
| `captureRateLimit` | `3` | Capture every N turns (0 = every turn) |

### 6. Profile switching

tapps-brain supports configurable memory profiles (EPIC-010) that control tier weights,
decay rates, and scoring parameters. To use a custom profile with the plugin:

1. Create a profile YAML at `.tapps-brain/profile.yaml` in your project:

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

2. Set `profilePath` in your plugin settings to point to the file.

The MCP server loads the profile at startup and applies it to all recall and scoring
operations.

### 7. Hive integration (multi-agent sharing)

When multiple OpenClaw agents share a workspace, the Hive (EPIC-011) enables cross-agent
memory sharing. Memories marked with `agent_scope: "hive"` propagate to a shared store
at `~/.tapps-brain/hive/hive.db`.

To enable Hive with the ContextEngine plugin:

1. Register each agent in the Hive agent registry (via the `hive_register_agent` MCP
   tool or programmatically).

2. Set `agent_scope` to `"domain"` or `"hive"` when saving memories that should be
   shared.

3. Recall automatically merges local + Hive results with configurable weight (default
   0.8 local, 0.2 Hive).

No plugin configuration changes are needed — Hive awareness is built into the MCP server.

---

## Option B: MCP Sidecar (manual control)

Use the MCP sidecar when you need direct access to all 28 tools or want to build custom
recall/capture workflows.

### Quick Start (5 minutes)

### 1. Install tapps-brain

**From PyPI:**

```bash
pip install tapps-brain[mcp]
```

**From source (latest):**

```bash
git clone https://github.com/wtthornton/tapps-brain.git
cd tapps-brain
pip install .[mcp]
```

Verify the install:

```bash
tapps-brain-mcp --help
```

### 2. Add to OpenClaw config

Edit `~/.openclaw/openclaw.json` and add a top-level `"mcp"` key (alongside `"agents"`, `"skills"`, etc.):

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

This uses the current working directory for memory storage. To pin a specific project:

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

### 3. Restart the OpenClaw gateway

OpenClaw reads MCP config at startup only — restart after editing `openclaw.json`.

### 4. Test it

Ask your OpenClaw agent:

```
"remember that we use PostgreSQL 16 for the main database"
```

Then in a new session:

```
"what do you remember about the database?"
```

If it recalls the PostgreSQL fact, you're set.

---

## What you get

### 28 MCP Tools

| Category | Tools |
|----------|-------|
| **Core** | `memory_save`, `memory_get`, `memory_delete`, `memory_search`, `memory_list` |
| **Lifecycle** | `memory_recall`, `memory_reinforce`, `memory_ingest`, `memory_supersede`, `memory_history` |
| **Sessions** | `memory_index_session`, `memory_search_sessions`, `memory_capture` |
| **Federation** | `federation_status`, `federation_subscribe`, `federation_unsubscribe`, `federation_publish` |
| **Maintenance** | `maintenance_consolidate`, `maintenance_gc`, `memory_export`, `memory_import` |
| **Profiles** | `profile_info`, `profile_switch` |
| **Hive** | `hive_status`, `hive_search`, `hive_propagate`, `agent_register`, `agent_list` |

### 4 MCP Resources

| URI | Description |
|-----|-------------|
| `memory://stats` | Entry count, tier distribution, schema version |
| `memory://health` | Store health report |
| `memory://entries/{key}` | Full detail view of a single entry |
| `memory://metrics` | Operation counters and latency histograms |

### 3 MCP Prompts

| Prompt | Description |
|--------|-------------|
| `recall(topic)` | "What do you remember about {topic}?" |
| `store_summary()` | Overview of what's in the memory store |
| `remember(fact)` | Guides the agent to save a fact with appropriate tier/tags |

---

## How memory works

### Tiers and decay

Every memory has a tier that controls how fast it fades:

| Tier | Half-life | Use for |
|------|-----------|---------|
| `architectural` | 180 days | System decisions, tech stack, infrastructure |
| `pattern` | 90 days | Coding conventions, API patterns |
| `procedural` | 30 days | Workflows, deployment steps |
| `context` | 14 days | Session-specific facts, current task details |

Decay is exponential and lazy — computed on read, no background processes.

### Auto-recall loop

```
User message  →  memory_recall()  →  Ranked memories injected into context
                                            ↓
Agent response  →  memory_capture()  →  New facts extracted and persisted
```

The recall orchestrator searches, ranks, deduplicates, and formats memories within a configurable token budget (default 2000 tokens).

### Scoring

Search results are ranked by four weighted signals:

| Signal | Weight | Source |
|--------|--------|--------|
| Relevance | 40% | BM25 full-text match |
| Confidence | 30% | Decayed confidence score |
| Recency | 15% | Time since last update |
| Frequency | 15% | Access count |

### Federation

Share memories across projects:

```
Project A  --publish-->  Hub  --subscribe-->  Project B
```

The hub lives at `~/.tapps-brain/memory/federated.db`. Tag filters and confidence thresholds control what flows between projects.

---

## Per-agent configuration

To scope tapps-brain to a specific OpenClaw agent rather than globally, add the MCP config under that agent's entry in `openclaw.json`:

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

---

## Using with uv (virtual environments)

If you installed tapps-brain inside a virtual environment or with `uv`, use the full path to the binary:

**Linux / macOS / WSL:**

```json
{
  "command": "/path/to/your/venv/bin/tapps-brain-mcp",
  "args": ["--project-dir", "/path/to/project"],
  "transport": "stdio"
}
```

**Windows:**

```json
{
  "command": "C:\\path\\to\\your\\venv\\Scripts\\tapps-brain-mcp.exe",
  "args": ["--project-dir", "C:\\path\\to\\project"],
  "transport": "stdio"
}
```

Or use `uv run` to avoid path issues:

```json
{
  "command": "uv",
  "args": ["run", "--extra", "mcp", "tapps-brain-mcp", "--project-dir", "/path/to/project"],
  "transport": "stdio"
}
```

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
└── memory/
    └── federated.db
```

Max 500 entries per project. Lowest-confidence entries are evicted when the cap is hit.

---

## Troubleshooting

**"mcp package required" error**
The MCP extra wasn't installed. Run `pip install tapps-brain[mcp]`.

**OpenClaw doesn't see the tools**
Restart the gateway after editing `openclaw.json`. OpenClaw only reads MCP config at startup.

**No memories returned**
Check that `--project-dir` points to the right directory. The store lives at `{project-dir}/.tapps-brain/memory/`. If omitted, it defaults to the directory where the `tapps-brain-mcp` process starts (which may not be your project).

**Permission errors on Windows / WSL**
Ensure the store directory is writable. On WSL with Windows drives, check mount options for `/mnt/c/`.

**Test the server manually**
Use the MCP Inspector to connect and verify tool discovery:

```bash
npx @modelcontextprotocol/inspector tapps-brain-mcp --project-dir /path/to/project
```

**Check server health from OpenClaw**
Ask your agent: *"read the memory://health resource"* — it returns a health report with diagnostics.

---

## Links

- [Main README](../../README.md) — full project overview
- [MCP Server Guide](mcp.md) — detailed tool/resource/prompt reference
- [Auto-Recall Guide](auto-recall.md) — recall orchestrator configuration
- [Federation Guide](federation.md) — cross-project memory sharing
- [OpenClaw Plugin README](../../openclaw-plugin/README.md) — plugin development guide
