# tapps-brain OpenClaw Plugin

ContextEngine plugin that integrates [tapps-brain](https://github.com/anthropics/tapps-brain) persistent memory into [OpenClaw](https://openclaw.dev).

## Features

- **Auto-recall** — injects relevant memories into every prompt
- **Auto-capture** — extracts new facts from agent responses (rate limited)
- **Pre-compaction flush** — persists context before OpenClaw compacts it
- **Profile support** — respects `.tapps-brain/profile.yaml` for memory profiles
- **Hive integration** — shares memories across agents via multi-agent Hive

## Prerequisites

- Node.js >= 18
- Python package `tapps-brain` installed (`pip install tapps-brain[mcp]`)
- OpenClaw workspace

## Install

```bash
# From the repo root
cd openclaw-plugin
npm install
npm run build

# Install into OpenClaw
openclaw plugin install ./openclaw-plugin
```

## Configuration

The plugin reads settings from `plugin.json`:

| Setting            | Default                        | Description                        |
| ------------------ | ------------------------------ | ---------------------------------- |
| `mcpCommand`       | `tapps-brain-mcp`             | MCP server command to spawn        |
| `profilePath`      | `.tapps-brain/profile.yaml`   | Memory profile config path         |
| `tokenBudget`      | `2000`                         | Max tokens for injected memories   |
| `captureRateLimit` | `3`                            | Capture at most once every N turns |

## Hooks

| Hook        | Trigger                   | MCP Tools Used                              |
| ----------- | ------------------------- | ------------------------------------------- |
| `bootstrap` | Session start             | `memory_import`, `memory_recall`            |
| `ingest`    | Each user message         | `memory_recall`                             |
| `afterTurn` | After agent response      | `memory_capture`                            |
| `compact`   | Before context compaction | `memory_ingest`, `memory_index_session`     |

## Development

```bash
npm install
npm run dev    # watch mode
npm run build  # production build
npm run lint   # type check only
```

## License

MIT
