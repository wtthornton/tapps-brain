# Upgrading the tapps-brain OpenClaw Plugin

## v2.0.1 — Align with tapps-brain 2.0.1

- Bumps plugin `package.json`, `openclaw.plugin.json`, and manifests to **2.0.1** with the Python patch release.
- **Fix:** MCP `CallToolResult` / structured content is unwrapped when calling recall tools so `assemble()` receives memory text (GitHub #46).
- **Fix:** Injected summaries include recall `value` fields from the Python side.
- **Operators:** Prefer `tapps_memory_search` / `tapps_memory_get` when both tapps-brain and built-in memory tools are present; see `docs/guides/openclaw.md` (GitHub #47 mitigated).

## v1.4.0 — Official MCP SDK transport (EPIC-039)

- **Breaking internal change:** replaced the hand-rolled JSON-RPC 2.0 client
  (`mcp_client.ts`, 466 lines) with the official `@modelcontextprotocol/sdk`
  (`StdioClientTransport` + `Client`). This is the same SDK used by OpenClaw
  itself, Claude Desktop, and Cursor.
- **Reconnection model changed:** exponential-backoff retry loops (3 retries at
  100/200/400ms) replaced with OpenClaw's session-invalidation pattern — on error,
  the session is torn down and lazily re-created on the next call.
- **Stderr logging added:** MCP server diagnostic output is now piped and logged
  via `console.error` (prefixed `[tapps-brain-mcp]`).
- **Health check timer removed:** dead process detection is now handled natively
  by the SDK's `transport.pid` property.
- **No public API change:** `McpClient` class retains the same interface
  (`start`, `stop`, `callTool`, `readResource`, `callPrompt`, `isRunning`,
  `reconnect`). `index.ts` required zero changes.
- **New dependency:** `@modelcontextprotocol/sdk@^1.27.0` added to `dependencies`.
- **Test speedup:** mcp_client tests run in ~16ms (was ~5s with fake timers).
- Also includes EPIC-037 (SDK type realignment) and EPIC-038 (dead compat layer removal).

## v1.3.1 — Align with tapps-brain 1.3.1

- Bumps plugin package, manifests, and `ContextEngineInfo.version` to **1.3.1**
  alongside the Python release (release gate script, OpenClaw docs consistency
  checker, CI integration, operator runbook hardening — no MCP surface change).

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
| ≥ 2026.3.7       | ContextEngine      | Full lifecycle (ingest/assemble/compact) — required |

The plugin requires OpenClaw v2026.3.7+ (`minimumVersion` in `openclaw.plugin.json`).
For older OpenClaw versions, use the MCP sidecar integration (Mode 3 in the
[OpenClaw guide](../../docs/guides/openclaw.md)) instead of the plugin.

Canonical install/upgrade runbook:
`docs/guides/openclaw-runbook.md`
