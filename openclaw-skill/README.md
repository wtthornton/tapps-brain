# tapps-brain-memory

**Persistent cross-session memory for OpenClaw agents.**

Give your OpenClaw agent a long-term memory that survives across sessions.
Memories are ranked by relevance, confidence, recency, and frequency using
BM25 scoring with exponential decay -- fully deterministic, no LLM calls.

## Install

```bash
openclaw skill install tapps-brain-memory
```

This installs `tapps-brain[mcp]` from PyPI and auto-configures the MCP server.

**No PyPI — install or upgrade from GitHub:** follow
[`docs/guides/openclaw-install-from-git.md`](../docs/guides/openclaw-install-from-git.md)
(`pip install` / `pip install --upgrade`, editable `git pull`, rebuild `openclaw-plugin`).

## What It Does

| Hook        | Action                                                        |
|-------------|---------------------------------------------------------------|
| `bootstrap` | Spawns `tapps-brain-mcp`, imports MEMORY.md on first run      |
| `ingest`    | Auto-recalls relevant memories and injects them into context  |
| `afterTurn` | Captures new facts from agent responses (rate-limited)        |
| `compact`   | Flushes important context to memory before compaction         |

## Key Features

- **Auto-recall** -- relevant memories injected before every turn, zero config
- **Auto-capture** -- facts extracted from agent responses automatically
- **Pre-compaction flush** -- saves important context before OpenClaw compresses
  the context window
- **Configurable profiles** -- `default`, `long_term`, `high_confidence`, or
  `fast_context` scoring profiles
- **Hive sharing** -- multiple OpenClaw agents share knowledge via the Hive
- **Federation** -- cross-project memory sharing via a federated hub
- **54 MCP tools** -- full programmatic control when you need it (memory, feedback,
  diagnostics, flywheel, Hive, federation, graph, OpenClaw migration)

## How It Works

tapps-brain runs as an MCP server (`tapps-brain-mcp`) spawned by the bootstrap
hook. All four ContextEngine lifecycle hooks are wired automatically:

1. **Bootstrap** starts the MCP server and runs first-time MEMORY.md import.
2. **Ingest** intercepts each user message, recalls relevant memories, and
   injects them as a system prefix (respecting a configurable token budget).
3. **AfterTurn** extracts facts from agent responses and saves them (rate-limited
   to once every N turns to avoid noise).
4. **Compact** captures important context before OpenClaw truncates the window.

All storage is local SQLite with WAL mode -- no cloud dependencies.

## Configuration

Settings in `openclaw.plugin.json` (auto-installed with the skill):

| Setting            | Default                     | Description                     |
|--------------------|-----------------------------|---------------------------------|
| `mcpCommand`       | `tapps-brain-mcp`           | MCP server command              |
| `profilePath`      | `.tapps-brain/profile.yaml` | Custom scoring profile path     |
| `tokenBudget`      | `2000`                      | Max tokens for memory injection |
| `captureRateLimit` | `3`                         | Capture every N turns           |

## Permissions

| Permission         | Reason                                      |
|--------------------|---------------------------------------------|
| `filesystem:read`  | Read memory database and config files       |
| `filesystem:write` | Write memory entries and session indexes     |
| `network:localhost` | MCP server communication (stdio transport) |
| `process:spawn`    | Spawn `tapps-brain-mcp` subprocess          |

## Requirements

- Python 3.12+
- OpenClaw v2026.3.7+
- `tapps-brain[mcp]` v1.1.0+ (installed automatically)

## Links

- [Documentation](https://github.com/wtthornton/tapps-brain/tree/main/docs)
- [Source](https://github.com/wtthornton/tapps-brain)
- [OpenClaw Integration Guide](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/openclaw.md)
- [PyPI](https://pypi.org/project/tapps-brain/)

## License

MIT
