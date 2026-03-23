---
id: EPIC-033
title: "OpenClaw plugin SDK alignment — fix API type drift and runtime bugs"
status: done
completed: 2026-03-23
priority: critical
created: 2026-03-23
tags: [openclaw-plugin, bug, sdk, type-safety]
---

# EPIC-033: OpenClaw Plugin SDK Alignment — Fix API Type Drift and Runtime Bugs

## Context

The OpenClaw plugin (`openclaw-plugin/src/index.ts`) defines a custom `OpenClawPluginApi` interface (lines 184-202) instead of importing the official type from `openclaw/plugin-sdk/core`. This hand-rolled interface has drifted from the actual SDK shape, causing three distinct runtime bugs:

1. **Version detection wrong** (GitHub #4): `api.version` is the *plugin's* version (e.g. `"1.2.0"`), not the OpenClaw runtime version. The runtime version lives at `api.runtime.version`. This causes the compatibility layer to misidentify OpenClaw 2026.3.13 as 1.2.0 and fall back to tools-only mode.

2. **Crash on register** (GitHub #5): `api.runtime.workspaceDir` does not exist on `PluginRuntime`. The workspace must be resolved via `api.runtime.agent.resolveAgentWorkspaceDir()` or `api.config`. Passing `undefined` to `resolve()` crashes the plugin before any hooks or tools can register.

3. **Migration script wrong path** (GitHub #7): The migration script reads `config.plugins[OLD_NAME]` but OpenClaw's actual config structure nests entries under `config.plugins.entries[name]` and installs under `config.plugins.installs[name]`.

Issue #6 (the custom interface) is the root cause of #4 and #5. Fixing the type import first ensures the compiler catches any remaining mismatches.

## Success Criteria

- [x] Plugin imports `OpenClawPluginApi` from `openclaw/plugin-sdk/core` instead of defining its own
- [x] Version detection reads `api.runtime.version` (OpenClaw version), not `api.version` (plugin version)
- [x] Workspace resolution uses `api.runtime.agent.resolveAgentWorkspaceDir()` or equivalent
- [x] Session ID resolved from correct `PluginRuntime` property
- [x] Migration script reads `config.plugins.entries[name]` and migrates `config.plugins.installs[name]`
- [x] TypeScript compiles cleanly with the SDK types
- [x] Existing tests pass

## Stories

### STORY-033.1: Import SDK types and remove custom interface

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `openclaw-plugin/src/index.ts` (lines 184-209), `openclaw-plugin/package.json`
**Verification:** `cd openclaw-plugin && npm run build`

#### Why

The custom `OpenClawPluginApi` interface is the root cause of issues #4 and #5. Replacing it with the official SDK type import ensures the compiler catches any field mismatches immediately and prevents future type drift. This must land first so that subsequent fixes are verified at compile time.

#### Acceptance Criteria

- [x] Add `openclaw` as a dev dependency (or peer dependency) with SDK type import
- [x] Replace the custom `OpenClawPluginApi` interface (lines 184-202) with: `import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";`
- [x] Remove `PluginEntryDef` custom interface if the SDK provides an equivalent
- [x] Update `register()` function signature to use the imported type
- [x] Fix all resulting TypeScript compilation errors (these reveal the bugs in #4 and #5)
- [x] TypeScript compiles cleanly

---

### STORY-033.2: Fix version detection to read api.runtime.version

**Status:** planned
**Effort:** S
**Depends on:** STORY-033.1
**Context refs:** `openclaw-plugin/src/index.ts` (version compatibility layer, ~line 212+)
**Verification:** `cd openclaw-plugin && npm run build && npm test`

#### Why

`api.version` returns the plugin's own version (e.g. `"1.2.0"`), not OpenClaw's runtime version. This causes the compatibility layer to select tools-only mode even when the full ContextEngine API is available. Fixes GitHub #4.

#### Acceptance Criteria

- [x] `getCompatibilityMode()` receives `api.runtime.version` instead of `api.version`
- [x] Version parsing handles the `"2026.3.13"` format correctly
- [x] Log message correctly shows the OpenClaw runtime version
- [x] Full ContextEngine mode activates on OpenClaw >= 2026.3.7
- [x] Hook-only mode activates on OpenClaw 2026.3.1-2026.3.6
- [x] Tools-only mode activates only on genuinely old versions

---

### STORY-033.3: Fix workspace and session resolution

**Status:** planned
**Effort:** S
**Depends on:** STORY-033.1
**Context refs:** `openclaw-plugin/src/index.ts` (register function, ~line 2270+), `openclaw-plugin/src/mcp_client.ts`
**Verification:** `cd openclaw-plugin && npm run build && npm test`

#### Why

`api.runtime.workspaceDir` and `api.runtime.sessionId` do not exist on `PluginRuntime`. The workspace must come from `api.runtime.agent.resolveAgentWorkspaceDir()` and session ID from the correct runtime property. Passing `undefined` crashes the MCP client constructor. Fixes GitHub #5.

#### Acceptance Criteria

- [x] Workspace directory resolved via `api.runtime.agent.resolveAgentWorkspaceDir()` (or `api.config.workspace.dir` with fallback)
- [x] Session ID resolved from the correct `PluginRuntime` property
- [x] `TappsBrainEngine` and `McpClient` receive valid string values (never `undefined`)
- [x] Defensive fallback if workspace resolution returns `undefined` (log warning, use `process.cwd()`)
- [x] Plugin registers successfully without crashing

---

### STORY-033.4: Fix migration script config path

**Status:** planned
**Effort:** S
**Depends on:** none
**Context refs:** `openclaw-plugin/scripts/migrate-plugin-rename.mjs`
**Verification:** `node openclaw-plugin/scripts/migrate-plugin-rename.mjs --dry-run`

#### Why

The migration script reads `config.plugins[OLD_NAME]` but OpenClaw stores plugin entries under `config.plugins.entries[name]` and install metadata under `config.plugins.installs[name]`. The script always reports "nothing to migrate" even when the old entry exists. Fixes GitHub #7.

#### Acceptance Criteria

- [x] Script reads from `config.plugins.entries[OLD_NAME]` instead of `config.plugins[OLD_NAME]`
- [x] Script also migrates `config.plugins.installs[OLD_NAME]` to `config.plugins.installs[NEW_NAME]`
- [x] Merged entry written to `config.plugins.entries[NEW_NAME]`
- [x] Old entry removed from both `entries` and `installs`
- [x] Backward-compatible: if `config.plugins.entries` doesn't exist, fall back to checking `config.plugins[OLD_NAME]` (for older OpenClaw config formats)
- [x] `--dry-run` output shows correct paths being read/written

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-033.1 — Import SDK types | M | Root cause; unblocks compile-time checks for 033.2 and 033.3 |
| 2 | STORY-033.2 — Fix version detection | S | Depends on 033.1; quick fix once types are correct |
| 3 | STORY-033.3 — Fix workspace/session resolution | S | Depends on 033.1; fixes crash |
| 4 | STORY-033.4 — Fix migration script | S | Independent; no type dependency |

## Dependency Graph

```
033.1 (SDK types) ──┬──→ 033.2 (version detection)
                    │
                    └──→ 033.3 (workspace/session)

033.4 (migration script) — independent
```

033.2 and 033.3 can be worked in parallel after 033.1. 033.4 is fully independent.

## GitHub Issues

- #4: Version detection reads api.version instead of api.runtime.version → STORY-033.2
- #5: Plugin crashes on register: api.runtime.workspaceDir is undefined → STORY-033.3
- #6: Plugin defines custom OpenClawPluginApi interface instead of importing from SDK → STORY-033.1
- #7: Migration script reads config.plugins[name] instead of config.plugins.entries[name] → STORY-033.4
