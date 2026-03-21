# tapps-brain for OpenClaw

Persistent cross-session memory for your OpenClaw agents. 21 MCP tools, zero LLM dependency, works offline.

---

## Quick Start (5 minutes)

### 1. Install tapps-brain

**From PyPI:**

```bash
pip install tapps-brain[mcp]
```

**From source (latest):**

```bash
git clone https://github.com/anthropics/tapps-brain.git
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

### 21 MCP Tools

| Category | Tools |
|----------|-------|
| **Core** | `memory_save`, `memory_get`, `memory_delete`, `memory_search`, `memory_list` |
| **Lifecycle** | `memory_recall`, `memory_reinforce`, `memory_ingest`, `memory_supersede`, `memory_history` |
| **Sessions** | `memory_index_session`, `memory_search_sessions`, `memory_capture` |
| **Federation** | `federation_status`, `federation_subscribe`, `federation_unsubscribe`, `federation_publish` |
| **Maintenance** | `maintenance_consolidate`, `maintenance_gc`, `memory_export`, `memory_import` |

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
