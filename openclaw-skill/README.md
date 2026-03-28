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

**Canonical PyPI + Git paths, verify, and restart:** [`docs/guides/openclaw-runbook.md`](../docs/guides/openclaw-runbook.md).

## What It Does

| Hook        | Action |
|-------------|--------|
| `bootstrap` | Spawns `tapps-brain-mcp`, imports MEMORY.md on first run |
| `ingest`    | Captures durable facts from new messages (rate-limited) |
| `assemble`  | Recalls memories and injects `systemPromptAddition` before the model call |
| `compact`   | Flushes context to memory before OpenClaw compacts the window |
| `dispose`   | Stops the MCP child on gateway shutdown |

## Key Features

- **Auto-recall** — relevant memories injected via `assemble()`, zero extra prompting
- **Auto-capture** — facts extracted in `ingest()` (rate-limited)
- **Pre-compaction flush** — important context saved in `compact()`
- **Configurable profiles** — built-in profiles (e.g. `repo-brain`) or `.tapps-brain/profile.yaml`; see [Profile catalog](../docs/guides/profile-catalog.md)
- **Hive sharing** -- multiple OpenClaw agents share knowledge via the Hive
- **Federation** -- cross-project memory sharing via a federated hub
- **63 MCP tools** -- full programmatic control when you need it (memory, feedback,
  diagnostics, flywheel, Hive, federation, graph, OpenClaw migration)

## How It Works

tapps-brain runs as an MCP server (`tapps-brain-mcp`) spawned by the bootstrap
hook. ContextEngine lifecycle hooks (see `SKILL.md` frontmatter) are wired by the
plugin:

1. **Bootstrap** — MCP process, MEMORY.md import, optional Hive registration.
2. **Ingest** — extract and save facts from incoming messages (`memory_capture`).
3. **Assemble** — recall and inject memories into the system prompt (token budget).
4. **Compact** — flush session context via `memory_ingest` / session indexing.
5. **Dispose** — stop the MCP child cleanly.

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
