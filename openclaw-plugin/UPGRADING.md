# Upgrading the tapps-brain OpenClaw Plugin

## v1.3.0 — Align with tapps-brain 1.3.0

- Bumps plugin package, `openclaw.plugin.json`, and `ContextEngineInfo.version` to
  **1.3.0** alongside the Python release (EPIC-031: diagnostics + flywheel MCP tools,
  `memory://report`, schema v11).
- MCP client `clientInfo.version` now matches the plugin release.

## v1.2.0 — Plugin rename: `tapps-brain` → `tapps-brain-memory`

The plugin package was renamed from `tapps-brain` to `tapps-brain-memory` in
v1.2.0 to align with the OpenClaw plugin naming convention for ContextEngine
plugins.

If you are upgrading from a previous version, follow these steps to avoid
orphaned config and duplicate plugin entries.

### 1. Back up your current config

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak
```

### 2. Remove the old plugin entry

Open `~/.openclaw/openclaw.json` and delete (or comment out) the old
`tapps-brain` entry under `plugins`:

```jsonc
// REMOVE this entry:
{
  "name": "tapps-brain",
  // ... old settings ...
}
```

### 3. Install the updated plugin

```bash
cd openclaw-plugin
npm install && npm run build
openclaw plugin install .
```

This registers the new `tapps-brain-memory` entry automatically.

### 4. Migrate your custom settings

If you had custom settings on the old `tapps-brain` plugin entry (e.g.
`agentId`, `hiveEnabled`, `tokenBudget`, `toolGroups`), copy them into the
new `tapps-brain-memory` entry in `openclaw.json`:

```jsonc
{
  "plugins": {
    "tapps-brain-memory": {
      "agentId": "your-agent-id",
      "hiveEnabled": true,
      "tokenBudget": 3000
      // ... any other custom settings ...
    }
  }
}
```

See `openclaw.plugin.json` for the full list of available config keys and
their defaults.

### 5. Clean up the orphaned extension directory

```bash
rm -rf ~/.openclaw/extensions/tapps-brain/
```

### 6. Restart the gateway

```bash
openclaw gateway restart
```

### Automated migration

You can also run the migration script to automate steps 2–5:

```bash
node openclaw-plugin/scripts/migrate-plugin-rename.mjs
```

The script will:
- Detect and copy settings from the old `tapps-brain` entry
- Remove the old entry from `openclaw.json`
- Delete the orphaned `~/.openclaw/extensions/tapps-brain/` directory
- Print a summary of what was changed

### OpenClaw version compatibility

| OpenClaw version | Compatibility mode | Notes |
|------------------|--------------------|-------|
| ≥ 2026.3.7       | ContextEngine      | Full lifecycle (ingest/assemble/compact) |
| 2026.3.1–3.6     | Hook-only          | Memory injected at session start only |
| < 2026.3.1       | Tools-only         | No automatic memory injection |

The plugin detects your OpenClaw version at startup and selects the best
available mode automatically. If `definePluginEntry` is not available in your
OpenClaw version, the plugin uses an identity shim and logs a warning.

Canonical install/upgrade runbook:
`docs/guides/openclaw-runbook.md`
