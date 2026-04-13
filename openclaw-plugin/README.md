# tapps-brain OpenClaw Plugin

**Plugin version 2.0.3** (tracks the [tapps-brain](https://github.com/wtthornton/tapps-brain) Python release).

ContextEngine plugin that integrates tapps-brain persistent memory into [OpenClaw](https://openclaw.dev).

## Features

- **Auto-recall** — `assemble()` injects relevant memories as `systemPromptAddition`
- **Auto-capture** — `ingest()` extracts new facts from conversation (rate limited)
- **Pre-compaction flush** — `compact()` persists context before OpenClaw compresses it
- **Profile support** — respects `.tapps-brain/profile.yaml` for memory profiles
- **Hive integration** — shares memories across agents via multi-agent Hive

## Prerequisites

- Node.js >= 18
- OpenClaw >= v2026.3.7
- Python package `tapps-brain` installed (`pip install tapps-brain[mcp]`)

## Install

```bash
# Install the Python backend
pip install tapps-brain[mcp]

# Build the plugin
cd openclaw-plugin
npm install
npm run build

# Install into OpenClaw
openclaw plugin install .
```

Canonical operator runbook (install + upgrade, PyPI + Git-only):
`docs/guides/openclaw-runbook.md`

If `openclaw logs` shows repeated
`tapps-brain-memory: loaded without install/load-path provenance` warnings,
see the runbook's *Troubleshooting → Repeated provenance warning* section
(reinstall via `openclaw plugin install .` or pin via `plugins.allow`).

## Activate

Add to your OpenClaw config (`openclaw.yaml` or equivalent):

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
        agentId: my-agent        # optional: for Hive sharing
        hiveEnabled: true        # optional: enable multi-agent Hive
```

## Configuration

Settings are defined in `openclaw.plugin.json` via `configSchema`:

| Setting            | Default                        | Description                        |
| ------------------ | ------------------------------ | ---------------------------------- |
| `mcpCommand`       | `tapps-brain-mcp`             | MCP server command to spawn        |
| `profilePath`      | `.tapps-brain/profile.yaml`   | Memory profile config path         |
| `tokenBudget`      | `2000`                         | Max tokens for injected memories   |
| `captureRateLimit` | `3`                            | Capture at most once every N calls |
| `agentId`          | `""`                           | Agent ID for Hive sharing          |
| `hiveEnabled`      | `false`                        | Enable multi-agent Hive            |

## ContextEngine Hooks

| Hook       | When called                 | What it does                                         |
| ---------- | --------------------------- | ---------------------------------------------------- |
| `bootstrap`| Session start (optional)    | Spawns MCP server, imports MEMORY.md, registers agent|
| `ingest`   | New message enters context  | Captures durable facts via `memory_capture`          |
| `assemble` | Before model call           | Recalls memories → `systemPromptAddition` markdown   |
| `compact`  | Before context compression  | Flushes context via `memory_ingest`, indexes session  |
| `dispose`  | Gateway shutdown            | Stops MCP child process                              |

## Architecture

```
OpenClaw
  └── tapps-brain plugin (TypeScript, this package)
        └── @modelcontextprotocol/sdk (StdioClientTransport + Client)
              └── tapps-brain-mcp (Python, spawned as child process)
                    └── MemoryStore (PostgreSQL, pgvector, tsvector)
```

The plugin is a thin wrapper. All memory logic lives in the Python MCP backend.
The plugin uses `@modelcontextprotocol/sdk`'s official `StdioClientTransport` and
`Client` — the same SDK used by OpenClaw itself — for MCP communication.
`ownsCompaction: false` — OpenClaw handles compaction, the plugin just flushes
context to memory before it's discarded.

## Development

```bash
npm install
npm run dev    # watch mode
npm run build  # production build
npm run lint   # type check only
```

## License

MIT
