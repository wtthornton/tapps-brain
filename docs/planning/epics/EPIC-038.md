---
id: EPIC-038
title: "OpenClaw plugin simplification — remove dead compat layers and streamline"
status: done
completed: 2026-03-23
priority: high
created: 2026-03-23
tags: [openclaw-plugin, cleanup, simplification]
---

# EPIC-038: OpenClaw Plugin Simplification — Remove Dead Compat Layers and Streamline

## Context

After EPIC-037 aligns the plugin with the real OpenClaw SDK, a significant amount of dead weight remains in the codebase:

1. **Three compatibility modes** (`context-engine` / `hook-only` / `tools-only`) — The plugin was built to support OpenClaw versions from pre-2026.3.1 through 2026.3.7+. Our `peerDependencies` already require `>=2026.3.7` and we target `2026.3.x`. The `hook-only` and `tools-only` paths are dead code. The version detection infrastructure (`parseOpenClawVersion`, `compareVersionTuples`, `getCompatibilityMode`, `CompatibilityMode` type, version constants) exists solely to route between these paths.

2. **`definePluginEntry` shim** — A try/catch `require()` with identity-function fallback (lines 280-300). Since EPIC-037 adds the real SDK as a dependency and uses a proper import, the shim is unnecessary. If the SDK isn't available, the plugin shouldn't silently degrade — it should fail to load.

3. **Tool group filtering** — The `isGroupEnabled()` function and `toolGroups` config let operators selectively enable/disable tool categories. No real-world user has requested this, and the real OpenClaw memory plugins (memory-core, memory-lancedb) don't have it. It adds ~50 lines of branching in `register()` and complexity to the config schema.

4. **Missing `delegateCompactionToRuntime`** — The real SDK exports `delegateCompactionToRuntime` for context engines that don't own compaction. Our engine sets `ownsCompaction: false` but implements a custom compact handler instead of delegating to the runtime.

Removing this dead weight reduces `index.ts` by ~200 lines, eliminates untestable code paths, and makes the plugin easier to maintain and debug.

**Prerequisite:** EPIC-037 must be complete (real SDK types in place, API calls fixed).

## Success Criteria

- [ ] No version detection code remains (`parseOpenClawVersion`, `compareVersionTuples`, `getCompatibilityMode`, `CompatibilityMode` type, version constants all deleted)
- [ ] No `definePluginEntry` shim/fallback — static import only
- [ ] Only the `registerContextEngine` path exists (no `hook-only` or `tools-only` branches)
- [ ] `compact()` delegates to `delegateCompactionToRuntime` from the SDK
- [ ] Tool group filtering removed — all tools registered unconditionally
- [ ] `index.ts` reduced by ~200 lines
- [ ] `cd openclaw-plugin && npm run build && npm test` passes
- [ ] Plugin loads and functions correctly in the simplified form

## Stories

### STORY-038.1: Remove version detection and compatibility modes

**Status:** planned
**Effort:** M
**Depends on:** EPIC-037 complete
**Context refs:** `openclaw-plugin/src/index.ts` (lines 196-270: version parsing; lines 2250, 2301-2349: mode routing in register())
**Verification:** `cd openclaw-plugin && npm run build && npm test`

#### Why

The three-mode compatibility system (`context-engine` / `hook-only` / `tools-only`) was built speculatively for OpenClaw versions that we don't support. Our `peerDependencies` require `>=2026.3.7` and `registerContextEngine` is always available. The version detection + routing adds ~120 lines of untestable code and makes the registration flow harder to follow. With only one path, the `register()` function becomes straightforward.

#### Acceptance Criteria

- [ ] Delete `parseOpenClawVersion()` function
- [ ] Delete `compareVersionTuples()` function
- [ ] Delete `getCompatibilityMode()` function
- [ ] Delete `CompatibilityMode` type and `OpenClawVersionTuple` type
- [ ] Delete `V_CONTEXT_ENGINE` and `V_HOOK_ONLY` constants
- [ ] Remove the `if (mode === "context-engine") ... else if (mode === "hook-only") ... else` branching in `register()`
- [ ] `register()` calls `api.registerContextEngine()` directly (no mode check)
- [ ] Remove the `hook-only` path (`registerHook("before_agent_start", ...)` block)
- [ ] Remove the `tools-only` fallback comment/path
- [ ] Exported types/functions updated — remove any exports of deleted items
- [ ] Tests updated to remove version-detection test cases

---

### STORY-038.2: Remove definePluginEntry shim and use static import

**Status:** planned
**Effort:** S
**Depends on:** STORY-037.1 (real SDK import in place)
**Context refs:** `openclaw-plugin/src/index.ts` (lines 280-300: shim)
**Verification:** `cd openclaw-plugin && npm run build && npm test`

#### Why

The try/catch `require()` with identity-function fallback was a workaround for environments without the SDK installed. With the real SDK as a dependency (from EPIC-037), this is unnecessary. Silent degradation to an identity shim masks real load failures — if the SDK isn't available, the plugin should fail loudly.

#### Acceptance Criteria

- [ ] Delete the try/catch `require("openclaw/plugin-sdk/core")` block (lines 280-300)
- [ ] `definePluginEntry` imported via static `import` statement at the top of the file
- [ ] No fallback shim — if the import fails, the module fails to load (correct behavior)
- [ ] The `console.warn` about identity shim removed
- [ ] Tests don't mock or stub `definePluginEntry` resolution

---

### STORY-038.3: Delegate compaction to runtime

**Status:** planned
**Effort:** S
**Depends on:** STORY-037.1 (real SDK import in place)
**Context refs:** `openclaw-plugin/src/index.ts` (TappsBrainEngine.compact ~line 589-629)
**Verification:** `cd openclaw-plugin && npm run build && npm test`

#### Why

The engine sets `ownsCompaction: false` but still implements a custom compact handler that flushes messages and indexes sessions via MCP. The real SDK provides `delegateCompactionToRuntime()` for engines that don't own compaction — this is the intended pattern. Our custom compact logic should move to the `dispose()` or `ingest()` lifecycle where it belongs, and `compact()` should delegate to the runtime.

#### Acceptance Criteria

- [ ] Import `delegateCompactionToRuntime` from `"openclaw/plugin-sdk/core"`
- [ ] `compact()` method calls `delegateCompactionToRuntime(params)` and returns its result
- [ ] Session indexing logic (currently in compact) moved to a more appropriate lifecycle hook or kept as a side-effect of ingest
- [ ] Message flush logic preserved (moved to dispose or triggered periodically in ingest)
- [ ] No behavioral regression — memories still captured and sessions still indexed

---

### STORY-038.4: Remove tool group filtering

**Status:** planned
**Effort:** S
**Depends on:** none
**Context refs:** `openclaw-plugin/src/index.ts` (isGroupEnabled ~line 131-135, toolGroups usage in register ~lines 2276-2298), `openclaw-plugin/openclaw.plugin.json` (configSchema)
**Verification:** `cd openclaw-plugin && npm run build && npm test`

#### Why

The `toolGroups` config and `isGroupEnabled()` function add branching complexity to `register()` for a feature no one uses. Real OpenClaw memory plugins register all their tools unconditionally. Removing this simplifies the registration flow and the config schema.

#### Acceptance Criteria

- [ ] Delete `isGroupEnabled()` function
- [ ] Delete `toolGroups` from the `PluginConfig` interface
- [ ] Remove `toolGroups` from `openclaw.plugin.json` `configSchema`
- [ ] All tool registration functions called unconditionally in `register()` (no `isGroupEnabled` checks)
- [ ] Config documentation updated to remove `toolGroups` references

---

### STORY-038.5: Update tests for simplified architecture

**Status:** planned
**Effort:** M
**Depends on:** STORY-038.1, STORY-038.2, STORY-038.3, STORY-038.4
**Context refs:** `openclaw-plugin/tests/index.test.ts`, `openclaw-plugin/tests/mcp_client.test.ts`
**Verification:** `cd openclaw-plugin && npm test`

#### Why

After removing compat modes, the shim, tool group filtering, and rewriting compact, the test suite will have dead test cases and missing coverage for the new simplified paths. Tests need to be updated to match the new architecture — not just passing, but verifying the correct behavior.

#### Acceptance Criteria

- [ ] Remove test cases for version detection (`parseOpenClawVersion`, `compareVersionTuples`, `getCompatibilityMode`)
- [ ] Remove test cases for `definePluginEntry` shim fallback
- [ ] Remove test cases for `hook-only` and `tools-only` registration paths
- [ ] Remove test cases for `isGroupEnabled` / tool group filtering
- [ ] Add/update test for `registerContextEngine` being called directly (no mode check)
- [ ] Add/update test for `delegateCompactionToRuntime` in compact handler
- [ ] Add/update test for unconditional tool registration
- [ ] All tests pass with `npm test`

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-038.1 — Remove version detection + compat modes | M | Biggest deletion, simplifies register() |
| 2 | STORY-038.2 — Remove definePluginEntry shim | S | Quick win, depends on 037.1 |
| 3 | STORY-038.3 — Delegate compaction to runtime | S | Behavioral change, small scope |
| 4 | STORY-038.4 — Remove tool group filtering | S | Independent cleanup |
| 5 | STORY-038.5 — Update tests | M | Must come last, validates everything |

## Dependency Graph

```
EPIC-037 (complete) ──→ 038.1 (compat modes) ──→ 038.5 (tests)
                    ──→ 038.2 (shim)          ──→ 038.5
                    ──→ 038.3 (compaction)     ──→ 038.5
                        038.4 (tool groups)    ──→ 038.5
```

038.1, 038.2, 038.3, and 038.4 are independent of each other (all depend on EPIC-037). 038.5 must come last.
