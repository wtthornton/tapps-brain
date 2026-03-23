---
id: EPIC-028
title: "OpenClaw Plugin Hardening — stability, tests, and compatibility"
status: done
priority: high
created: 2026-03-23
target_date: 2026-06-15
tags: [openclaw, hardening, tests, typescript, stability]
---

# EPIC-028: OpenClaw Plugin Hardening — Stability, Tests, and Compatibility

## Context

The ContextEngine plugin (EPIC-012) and memory replacement (EPIC-026) provide the
integration surface. But the plugin has known stability issues:

1. **MCP client has no reconnection logic** — if `tapps-brain-mcp` crashes, all
   subsequent tool calls fail silently until gateway restart
2. **No TypeScript tests** — the plugin is ~700 lines of TypeScript with zero
   automated tests
3. **Bootstrap race condition** — `bootstrap()` runs async but `ingest()` may be
   called before it completes, hitting an uninitialized MCP client
4. **No citation support** — OpenClaw's memory system supports `Source: <path#line>`
   footers in recall results; tapps-brain doesn't include these
5. **Session memory not wired** — OpenClaw's experimental session memory search
   (`sources: ["memory", "sessions"]`) can't reach tapps-brain's session index
6. **Breaking changes in OpenClaw v2026.3.x** — sqlite-vec bindings regression
   (issue #31677), memory_search race condition (issue #29588) affect the plugin

This epic hardens the plugin for production use.

Depends on EPIC-012 (done). Complements EPIC-026 and EPIC-027.

## Success Criteria

- [x] MCP client automatically reconnects on crash/timeout
- [x] Full TypeScript test suite with >80% coverage
- [x] Bootstrap completes before any hook is invoked (no race condition)
- [x] Citation support in recall results matches OpenClaw's expected format
- [x] Session memory search integrates with tapps-brain's session index
- [x] Plugin works correctly on OpenClaw v2026.3.1 through v2026.3.7+
- [x] Error handling produces actionable log messages (not silent failures)

## Stories

### STORY-028.1: MCP client reconnection and health checks

**Status:** done
**Effort:** M
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/mcp_client.ts`
**Verification:** TypeScript test — kill child process, verify client reconnects

#### Why

The MCP client spawns `tapps-brain-mcp` as a child process. If the process crashes
(OOM, unhandled exception, signal), the `McpClient` sets `this.process = null` but
never restarts it. All subsequent `callTool()` calls throw "MCP process not running".
The agent loses all memory capabilities until the gateway restarts.

#### Acceptance Criteria

- [x] `McpClient.callTool()` detects dead process and triggers automatic restart
- [x] Configurable retry count (default 3) and backoff (100ms, 200ms, 400ms)
- [x] Health check: periodic `callTool("memory_list", { limit: 0 })` every 60s to verify process is alive
- [x] `onReconnect` callback so the plugin can log the event
- [x] Graceful degradation: after max retries, return `{ error: "mcp_unavailable" }` instead of throwing
- [x] `stop()` is idempotent — calling stop on a dead process doesn't throw
- [x] Request timeout: pending requests fail after 10s (configurable) instead of hanging forever

---

### STORY-028.2: Fix bootstrap race condition

**Status:** done
**Effort:** S
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/index.ts:152-191`
**Verification:** TypeScript test — call ingest() before bootstrap() completes, verify it waits

#### Why

In `register()`, `engine.bootstrap()` is called with `.catch()` — fire-and-forget.
If `ingest()` or `assemble()` is called before bootstrap completes, `this.mcpClient`
is not initialized and the MCP call fails. OpenClaw can call hooks immediately after
plugin registration.

#### Acceptance Criteria

- [x] `bootstrap()` sets a `ready` promise on the engine
- [x] `ingest()`, `assemble()`, `compact()` all `await this.ready` before making MCP calls
- [x] If `bootstrap()` fails, `ready` is rejected and hooks return graceful fallbacks (empty results, not errors)
- [x] Startup time measured: bootstrap should complete in <2s for typical stores
- [x] TypeScript test: simulate concurrent bootstrap + ingest, verify no race

---

### STORY-028.3: TypeScript test suite for plugin

**Status:** done
**Effort:** L
**Depends on:** STORY-028.1, STORY-028.2
**Context refs:** `openclaw-plugin/src/index.ts`, `openclaw-plugin/src/mcp_client.ts`
**Verification:** `cd openclaw-plugin && npm test` — all green, >80% coverage

#### Why

The plugin is ~700 lines of TypeScript controlling a critical integration. Zero tests
means any change is untestable. The MCP client's JSON-RPC framing, the ContextEngine
hooks, and the MEMORY.md parser all need automated validation.

#### Acceptance Criteria

- [x] Test framework: vitest (lightweight, TypeScript-native)
- [x] `openclaw-plugin/package.json` updated with test script and devDependencies
- [x] Tests for McpClient:
  - JSON-RPC message framing (Content-Length header parsing)
  - Request/response matching by ID
  - Error response handling
  - Process spawn and stop lifecycle
  - Reconnection logic (STORY-028.1)
  - Request timeout
- [x] Tests for TappsBrainEngine:
  - bootstrap() — first-run MEMORY.md import
  - bootstrap() — Hive agent registration
  - ingest() — rate limiting (capture every N calls)
  - ingest() — heartbeat skip
  - assemble() — recall injection with token budget
  - assemble() — deduplication (injectedKeys set)
  - compact() — context flush and session indexing
- [x] Tests for parseMemoryMdForImport:
  - Heading level → tier mapping
  - Slugify edge cases
  - Empty content handling
- [x] Coverage: >80% of `index.ts` and `mcp_client.ts`
- [x] CI integration: tests run in GitHub Actions

---

### STORY-028.4: Citation support in recall results

**Status:** done
**Effort:** S
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/index.ts:243-314`, `src/tapps_brain/mcp_server.py:278-298`
**Verification:** manual test — recall results include `Source:` footer

#### Why

OpenClaw's memory system supports citations (`memory.citations: "auto"`) that add
`Source: <path#line>` footers to search results. This helps the agent attribute
knowledge to specific entries. tapps-brain's recall results include keys and tiers but
not in OpenClaw's citation format.

#### Acceptance Criteria

- [x] `assemble()` hook formats recalled memories with citation footers: `Source: tapps-brain/<key>`
- [x] Citation format matches OpenClaw's expected pattern: `Source: <path>#L<line>`
- [x] Synthetic path: `memory/<tier>/<key>.md` (matches what MEMORY.md sync would produce)
- [x] Citations are optional: controlled by plugin config `citations: "auto" | "on" | "off"` (default: "auto")
- [x] When `citations: "off"`, no footer is added
- [x] TypeScript test: verify citation formatting for various entry types

---

### STORY-028.5: Session memory search integration

**Status:** done
**Effort:** M
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/index.ts`, `src/tapps_brain/mcp_server.py:429-472`
**Verification:** manual test — `memory_search` with `sources: ["sessions"]` returns session results

#### Why

OpenClaw's experimental session memory (`experimental.sessionMemory: true`) indexes
conversation transcripts for later recall. tapps-brain has a parallel session index
(`index_session()` / `search_sessions()`). These should be unified: when OpenClaw
requests session-scoped search, tapps-brain's session index should be queried.

#### Acceptance Criteria

- [x] When `memory_search` is called with session scope, also query `memory_search_sessions`
- [x] Results from session search are merged with regular search results
- [x] Session results include `source: "session"` marker so the agent can distinguish them
- [x] `compact()` hook indexes session chunks (already done) — verify it works with session memory enabled
- [x] TypeScript test: verify session results are included in search

---

### STORY-028.6: OpenClaw version compatibility layer

**Status:** done
**Effort:** M
**Depends on:** EPIC-012.2
**Context refs:** `openclaw-plugin/src/index.ts`, `openclaw-plugin/openclaw.plugin.json`
**Verification:** manual test — plugin loads on OpenClaw v2026.3.1 and v2026.3.7

#### Why

OpenClaw v2026.3.1 broke sqlite-vec bindings. v2026.3.7 introduced the ContextEngine
plugin system. The plugin must work across these versions without requiring users to
be on a specific version.

#### Acceptance Criteria

- [x] Plugin detects OpenClaw version at bootstrap
- [x] Version >= 2026.3.7: use full ContextEngine hooks (current behavior)
- [x] Version >= 2026.3.1 < 2026.3.7: register as hook-only plugin with `before_agent_start` hook (legacy path)
- [x] Version < 2026.3.1: log warning, register MCP tools only (no hooks)
- [x] `openclaw.plugin.json` declares minimum version: `"minimumVersion": "2026.3.1"`
- [x] Documentation: version compatibility matrix

---

### STORY-028.7: Error handling and observability

**Status:** done
**Effort:** S
**Depends on:** STORY-028.1
**Context refs:** `openclaw-plugin/src/index.ts`, `openclaw-plugin/src/mcp_client.ts`
**Verification:** manual test — trigger error conditions, verify log output

#### Why

The current plugin catches all exceptions with empty `catch {}` blocks (lines 228,
230, 311, 351). Errors are completely invisible. When something goes wrong, users see
"0 memories" with no explanation. Structured logging makes issues debuggable.

#### Acceptance Criteria

- [x] All `catch {}` blocks replaced with `catch (err) { logger.warn(...) }`
- [x] Log format: `[tapps-brain] <hook>: <error_type> — <message>`
- [x] Structured fields: `{ hook, tool, errorType, message, elapsed_ms }`
- [x] Performance logging: `assemble()` logs recall latency; `ingest()` logs capture latency
- [x] MCP client logs: process spawn, exit (with code), reconnection attempts
- [x] `logger` obtained from `api.logger` (OpenClaw's structured logger)
- [x] TypeScript test: verify error handlers call logger.warn

---

### STORY-028.8: Update documentation for all integration modes

**Status:** done
**Effort:** M
**Depends on:** STORY-028.3, STORY-028.4, STORY-028.5, STORY-028.6
**Context refs:** `docs/guides/openclaw.md`, `openclaw-skill/SKILL.md`
**Verification:** documentation review

#### Why

`docs/guides/openclaw.md` was written during EPIC-012 for the initial ContextEngine
integration. It doesn't cover: memory slot replacement (EPIC-026), full tool surface
(EPIC-027), reconnection behavior, citation config, session memory, version compat,
or the mcp-adapter approach.

#### Acceptance Criteria

- [x] `docs/guides/openclaw.md` restructured into sections:
  1. Quick Start (one-command install via ClawHub)
  2. Integration Modes (ContextEngine, memory slot, MCP sidecar, mcp-adapter)
  3. Configuration Reference (all plugin config options)
  4. Feature Matrix (what works in each mode)
  5. Migration Guide (from memory-core)
  6. Troubleshooting
  7. Version Compatibility
- [x] Configuration examples for each mode with complete `openclaw.json` snippets
- [x] Feature matrix table: tool name × integration mode → supported/not supported
- [x] Troubleshooting: "memory_search returns 0 results" → check which memory plugin is active
- [x] Troubleshooting: "MCP process crashes" → check reconnection logs
- [x] Link to EPIC-026, EPIC-027, EPIC-028 for context

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | 028.2 — Bootstrap race condition | S | Critical bug: first user interaction may fail |
| 2 | 028.7 — Error handling | S | Prerequisite for debugging everything else |
| 3 | 028.1 — MCP reconnection | M | Without this, any crash is permanent |
| 4 | 028.3 — TypeScript tests | L | Foundation for safe changes |
| 5 | 028.4 — Citation support | S | User-visible feature gap |
| 6 | 028.5 — Session memory | M | Completes memory feature parity |
| 7 | 028.6 — Version compat | M | Adoption blocker for older installs |
| 8 | 028.8 — Documentation | M | Final polish |

## Dependency Graph

```
EPIC-012 (done)
    │
    ├──→ 028.2 (race fix) ──┐
    ├──→ 028.7 (error handling) ──→ 028.1 (reconnection) ──→ 028.3 (TS tests) ──┐
    ├──→ 028.4 (citations) ──────────────────────────────────────────────────────┤
    ├──→ 028.5 (session memory) ────────────────────────────────────────────────┼──→ 028.8 (docs)
    └──→ 028.6 (version compat) ───────────────────────────────────────────────┘
```
