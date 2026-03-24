---
id: EPIC-037
title: "OpenClaw plugin SDK realignment — fix API contract to match real SDK"
status: done
completed: 2026-03-23
priority: critical
created: 2026-03-23
tags: [openclaw-plugin, bug, sdk, type-safety]
---

# EPIC-037: OpenClaw Plugin SDK Realignment — Fix API Contract to Match Real SDK

## Context

The OpenClaw plugin ships a hand-written `openclaw-sdk.d.ts` (ambient type declarations) that diverges from the real OpenClaw SDK in several critical ways. EPIC-033 partially addressed this by importing the SDK type and fixing `api.runtime.version`, but the ambient declarations were kept and several API mismatches remain. A source-level audit of the real OpenClaw SDK (`github.com/openclaw/openclaw`, `src/plugin-sdk/` and `src/plugins/`) reveals these concrete discrepancies:

1. **`resolveAgentWorkspaceDir` signature** — Our type declares `(): string | undefined`. Real SDK: `(cfg: OpenClawConfig, agentId: string)`. Calling without args crashes with `Cannot read properties of undefined (reading 'agents')`.

2. **`api.pluginConfig` not used** — The real SDK provides `api.pluginConfig?: Record<string, unknown>` for plugin-specific config. Our code casts `api.config` (the full OpenClaw app config) to `PluginConfig`, which reads wrong keys.

3. **`registerTool` signature wrong** — Our type: `(name: string, definition: PluginToolDefinition)`. Real SDK: `(tool: AnyAgentTool | ToolFactory, opts?)`. All 54 tool registrations use the wrong calling convention.

4. **`definePluginEntry` missing required fields** — Real SDK requires `description: string` and optionally `kind` and `configSchema`. Our call only provides `id`, `name`, `register`.

5. **`registerContextEngine` factory signature** — Real SDK factory is `() => ContextEngine | Promise<ContextEngine>` (parameterless). Our code passes `(_config) => engine` — works by accident but types are wrong.

6. **`openclaw-sdk.d.ts` still exists** — This ambient type file is the root cause of all mismatches. It should be replaced with real SDK imports.

These issues prevent the plugin from loading in a real OpenClaw installation. The `resolveAgentWorkspaceDir` crash (item 1) is the most severe — it kills the plugin on startup before any tools or hooks register.

**Source of truth:** The real OpenClaw SDK types at:
- `openclaw/src/plugins/types.ts` — `OpenClawPluginApi`
- `openclaw/src/plugins/runtime/types-core.ts` — `PluginRuntimeCore`, `PluginAgent`
- `openclaw/src/agents/agent-scope.ts` — `resolveAgentWorkspaceDir(cfg, agentId)`
- `openclaw/src/context-engine/registry.ts` — `ContextEngineFactory`
- `openclaw/src/plugin-sdk/plugin-entry.ts` — `definePluginEntry`

## Success Criteria

- [ ] `openclaw-sdk.d.ts` deleted; all types imported from the real `openclaw` package
- [ ] `resolveAgentWorkspaceDir` called with `(api.config, agentId)` — no startup crash
- [ ] Plugin reads `api.pluginConfig` for plugin-specific settings
- [ ] `registerTool` calls match the real SDK signature (tool object or factory)
- [ ] `definePluginEntry` includes `description` and `kind` fields
- [ ] `registerContextEngine` factory is parameterless
- [ ] `cd openclaw-plugin && npm run build` compiles cleanly against real SDK types
- [ ] `cd openclaw-plugin && npm test` passes

## Stories

### STORY-037.1: Delete ambient types and import from real SDK

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `openclaw-plugin/src/openclaw-sdk.d.ts`, `openclaw-plugin/src/index.ts` (imports at top), `openclaw-plugin/package.json`
**Verification:** `cd openclaw-plugin && npm run build`

#### Why

The hand-written `openclaw-sdk.d.ts` is the root cause of every API mismatch. It masks compile-time errors that would otherwise catch bugs 1-5. Deleting it and importing from the real SDK ensures the TypeScript compiler enforces the correct API contract. This must land first so all subsequent stories get compile-time verification.

#### Acceptance Criteria

- [ ] Delete `openclaw-plugin/src/openclaw-sdk.d.ts`
- [ ] Add `openclaw` as a dev dependency in `openclaw-plugin/package.json` (the package provides types)
- [ ] Replace ambient imports with real SDK imports: `import type { OpenClawPluginApi, PluginRuntime } from "openclaw/plugin-sdk/core";`
- [ ] Import `definePluginEntry` from `"openclaw/plugin-sdk/plugin-entry"` (or `"openclaw/plugin-sdk/core"` — both export it)
- [ ] Fix all resulting TypeScript compilation errors — these reveal the remaining bugs
- [ ] Compilation succeeds with `npm run build`

---

### STORY-037.2: Fix resolveAgentWorkspaceDir and pluginConfig access

**Status:** planned
**Effort:** S
**Depends on:** STORY-037.1
**Context refs:** `openclaw-plugin/src/index.ts` (register function ~line 2249-2263)
**Verification:** `cd openclaw-plugin && npm run build && npm test`

#### Why

`resolveAgentWorkspaceDir()` requires `(cfg, agentId)` but we call it with no arguments — this is the startup crash. Additionally, `api.config` is the full OpenClaw config, not our plugin config; the real SDK provides `api.pluginConfig` for that. Both fixes are in the `register()` function and logically coupled (they initialize the engine).

#### Acceptance Criteria

- [ ] `resolveAgentWorkspaceDir` called as `api.runtime.agent.resolveAgentWorkspaceDir(api.config, agentId)` where `agentId` comes from `api.pluginConfig?.agentId` or `api.id`
- [ ] Defensive fallback retained: if result is `undefined`, log warning and use `process.cwd()`
- [ ] Engine constructed with `api.pluginConfig` (cast to `PluginConfig`) instead of `api.config`
- [ ] Plugin-level config properties (`mcpCommand`, `tokenBudget`, `hiveEnabled`, etc.) still resolve correctly
- [ ] Plugin starts without crashing in test harness

---

### STORY-037.3: Rewrite tool registration to match real SDK registerTool

**Status:** planned
**Effort:** M
**Depends on:** STORY-037.1
**Context refs:** `openclaw-plugin/src/index.ts` (registerMemorySlotTools ~line 803, registerLifecycleTools ~line 932, etc.)
**Verification:** `cd openclaw-plugin && npm run build && npm test`

#### Why

The real `registerTool` accepts a tool object (`AnyAgentTool`) or a factory function — not a `(name, definition)` pair. All 7 tool registration functions (54 tools total) use the wrong calling convention. Without this fix, no tools register even if the plugin loads.

#### Acceptance Criteria

- [ ] Each tool registration uses the real `registerTool(toolObject)` or `registerTool(factory)` pattern
- [ ] Tool objects conform to `AnyAgentTool` shape: `{ name, description, inputSchema, execute }` (verify exact field names from SDK)
- [ ] All 7 registration functions updated: `registerMemorySlotTools`, `registerLifecycleTools`, `registerHiveTools`, `registerKnowledgeGraphTools`, `registerAuditTagsProfileTools`, `registerMaintenanceConfigTools`, `registerFederationTools`
- [ ] `registerResourceAndPromptTools` updated for resource and prompt registration patterns
- [ ] `npm run build` succeeds with no type errors in tool registration code

---

### STORY-037.4: Fix definePluginEntry and registerContextEngine signatures

**Status:** planned
**Effort:** S
**Depends on:** STORY-037.1
**Context refs:** `openclaw-plugin/src/index.ts` (default export ~line 2246, registerContextEngine ~line 2312)
**Verification:** `cd openclaw-plugin && npm run build && npm test`

#### Why

`definePluginEntry` requires a `description` field and optionally `kind` and `configSchema`. Our call omits these. The `registerContextEngine` factory should be parameterless but ours accepts an unused `_config` parameter. Both are type errors that the real SDK import (from 037.1) will surface.

#### Acceptance Criteria

- [ ] `definePluginEntry` call includes `description: "Persistent cross-session memory powered by tapps-brain"` (or similar)
- [ ] `kind: "context-engine"` included (matches `openclaw.plugin.json`)
- [ ] `configSchema` provided (reference the schema from `openclaw.plugin.json` or define inline)
- [ ] `registerContextEngine` factory changed from `(_config) => engine` to `() => engine`
- [ ] Compilation succeeds with no type warnings

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-037.1 — Delete ambient types, import real SDK | M | Foundation; unblocks compile-time checks for all others |
| 2 | STORY-037.2 — Fix resolveAgentWorkspaceDir + pluginConfig | S | Fixes the startup crash (highest severity bug) |
| 3 | STORY-037.3 — Rewrite tool registration | M | Fixes tool registration (second highest severity) |
| 4 | STORY-037.4 — Fix definePluginEntry + registerContextEngine | S | Fixes registration signatures (lower severity) |

## Dependency Graph

```
037.1 (SDK types) ──┬──→ 037.2 (workspace + config)
                    ├──→ 037.3 (tool registration)
                    └──→ 037.4 (entry + engine signatures)
```

037.2, 037.3, and 037.4 can be worked in parallel after 037.1, but sequential is fine for Ralph.
