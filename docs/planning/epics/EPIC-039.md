---
id: EPIC-039
title: "Replace custom MCP client with official @modelcontextprotocol/sdk"
status: done
completed: 2026-03-24
priority: critical
created: 2026-03-24
tags: [openclaw-plugin, mcp, sdk, transport, reliability]
---

# EPIC-039: Replace Custom MCP Client with Official @modelcontextprotocol/sdk

## Context

The OpenClaw plugin's `mcp_client.ts` is a hand-rolled JSON-RPC 2.0 client (~466 lines) that implements Content-Length framing, manual stdio parsing, request/response ID matching, exponential-backoff reconnection, and periodic health checks. This custom implementation is the root cause of timeout failures when the plugin runs inside OpenClaw's gateway runtime.

OpenClaw itself uses `@modelcontextprotocol/sdk` (v1.27.1) — the same battle-tested SDK used by Claude Desktop, Cursor, and every other MCP host — for all MCP server communication. The SDK's `StdioClientTransport` + `Client` handle:

- Proper stdio spawning with `stderr: "pipe"`
- Content-Length framing (correct UTF-8 byte counting)
- JSON-RPC 2.0 protocol and request/response matching
- Connection lifecycle (`connect` / `close`)
- Dead process detection via `transport.pid`

The custom client likely has a subtle stdio buffering or framing bug (the code itself notes that non-ASCII Content-Length handling is incorrect) that manifests only when spawned as a child process inside OpenClaw's Node.js runtime.

**Validated against OpenClaw source** (`github.com/openclaw/openclaw.git`):

| Pattern | OpenClaw file | Our equivalent |
|---------|--------------|----------------|
| `StdioClientTransport` construction | `src/agents/pi-bundle-mcp-tools.ts:154-159` | `mcp_client.ts:109` (`spawn()`) |
| `Client` construction + connect | `pi-bundle-mcp-tools.ts:161-167` | `mcp_client.ts:139-143` (`sendRpc("initialize")`) |
| `client.callTool()` | `pi-bundle-mcp-tools.ts:199-203` | `mcp_client.ts:301` (`sendRpc("tools/call")`) |
| Session invalidation reconnect | `chrome-mcp.ts:325-328` | `mcp_client.ts:286-316` (retry loop) |
| Two-phase close | `pi-bundle-mcp-tools.ts:116-120` | `mcp_client.ts:160-170` (`process.kill`) |
| Stderr logging | `pi-bundle-mcp-tools.ts:89-114` | *(not implemented)* |
| Dead process detection | `chrome-mcp.ts:272` (`transport.pid === null`) | `mcp_client.ts:190-192` (`this.process !== null`) |

**Key SDK facts:**
- OpenClaw does **not** use `client.readResource()` or `client.getPrompt()` from external MCP servers, but the SDK `Client` class supports both methods. Our plugin calls `readResource()` (for `memory://stats`, `memory://diagnostics`, etc.) and `callPrompt()` (for recall/store/remember prompts), so we must verify these work.
- The SDK handles the MCP initialization handshake (`initialize` + `notifications/initialized`) automatically inside `client.connect()` — no manual `sendRpc("initialize")` needed.
- OpenClaw uses `@modelcontextprotocol/sdk@1.27.1`. We should pin to `^1.27.0` for compatibility.

**Scope:** This epic replaces only `mcp_client.ts` internals. The `McpClient` class public API (`start`, `stop`, `callTool`, `readResource`, `callPrompt`, `isRunning`, `reconnect`) stays the same so `index.ts` requires zero changes. The helper functions `hasMemoryMd` and `isFirstRun` are pure filesystem — unchanged.

## Success Criteria

- [ ] `@modelcontextprotocol/sdk` added as a dependency (`^1.27.0`)
- [ ] `mcp_client.ts` rewritten to use `StdioClientTransport` + `Client` from the official SDK
- [ ] All hand-rolled protocol code removed: Content-Length framing, buffer parsing, JSON-RPC request/response matching, manual initialization handshake
- [ ] `callTool()`, `readResource()`, and `callPrompt()` delegate to SDK `Client` methods
- [ ] Reconnection uses OpenClaw's session-invalidation pattern (not retry loops)
- [ ] Stderr from MCP process is piped and logged (matching OpenClaw's pattern)
- [ ] `index.ts` requires **zero changes** — same `McpClient` import and API
- [ ] `cd openclaw-plugin && npm run build` compiles cleanly
- [ ] `cd openclaw-plugin && npm test` passes
- [ ] Helper functions `hasMemoryMd` and `isFirstRun` unchanged

## Stories

### STORY-039.1: Add @modelcontextprotocol/sdk dependency

**Status:** planned
**Effort:** S
**Depends on:** none
**Context refs:** `openclaw-plugin/package.json`
**Verification:** `cd openclaw-plugin && npm install && node -e "require('@modelcontextprotocol/sdk/client/index.js')"`

#### Why

The SDK must be installed before any code can import it. Verifying the three import paths (`client/index.js`, `client/stdio.js`, `types.js`) upfront prevents surprises during the rewrite. This is a zero-risk change — adding a dependency doesn't affect existing code.

#### Acceptance Criteria

- [ ] `@modelcontextprotocol/sdk` added to `dependencies` (not devDependencies) in `openclaw-plugin/package.json` at `^1.27.0`
- [ ] `npm install` succeeds in the `openclaw-plugin` directory
- [ ] These imports resolve without error:
  - `import { Client } from "@modelcontextprotocol/sdk/client/index.js"`
  - `import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js"`
  - `import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js"`
- [ ] Existing `npm run build` and `npm test` still pass (no regressions)

---

### STORY-039.2: Rewrite McpClient internals with SDK transport

**Status:** planned
**Effort:** L
**Depends on:** STORY-039.1
**Context refs:** `openclaw-plugin/src/mcp_client.ts`, OpenClaw `src/agents/pi-bundle-mcp-tools.ts` (transport + client patterns), OpenClaw `src/browser/chrome-mcp.ts` (session invalidation pattern)
**Verification:** `cd openclaw-plugin && npm run build`

#### Why

This is the core fix. The hand-rolled JSON-RPC client with Content-Length framing is the source of the timeout bug. Replacing it with the SDK's `StdioClientTransport` + `Client` uses the same proven transport layer that OpenClaw itself runs. The public API must stay identical so `index.ts` needs zero changes.

#### Acceptance Criteria

- [ ] `McpClient` class retains exact same public API: `constructor(projectDir)`, `start(command?, extraArgs?)`, `stop()`, `reconnect()`, `callTool(name, args)`, `readResource(uri)`, `callPrompt(name, args)`, `isRunning` getter, `projectDir` getter
- [ ] Internal state replaced: `process: ChildProcess` → `client: Client | null` + `transport: StdioClientTransport | null`
- [ ] `start()` creates `StdioClientTransport` with `{ command, args: ["--project-dir", projectDir, ...extraArgs], stderr: "pipe" }`, creates `Client({ name: "tapps-brain-openclaw", version: "1.4.0" }, {})`, calls `await client.connect(transport)`
- [ ] `stop()` calls `await client.close().catch(() => {})` then `await transport.close().catch(() => {})` (two-phase close matching OpenClaw's `disposeSession()` pattern)
- [ ] `callTool()` delegates to `await this.client.callTool({ name, arguments: args })` — returns `CallToolResult`
- [ ] `readResource()` delegates to `await this.client.readResource({ uri })` — verify SDK supports this method
- [ ] `callPrompt()` delegates to `await this.client.getPrompt({ name, arguments: args })` — verify SDK supports this method
- [ ] `isRunning` checks `this.transport !== null && this.transport.pid !== null` (matching OpenClaw's dead-process detection in `chrome-mcp.ts:272`)
- [ ] `reconnect()` invalidates current session (close client + transport), then creates fresh transport + client
- [ ] All removed code: `buffer` field, `processBuffer()`, `sendRpc()`, `sendNotification()`, `handleResponse()`, `onData()`, `pending` map, `PendingRequest`/`JsonRpcRequest`/`JsonRpcResponse` interfaces, `MCP_PROTOCOL_VERSION` constant, `REQUEST_TIMEOUT_MS` constant
- [ ] Health check timer removed (SDK transport + `transport.pid` detect dead processes; health check was a workaround for the custom client's lack of process death detection)
- [ ] Stderr from the transport is piped and logged via a data listener (matching `pi-bundle-mcp-tools.ts:89-114`), using `console.error` or accepting an optional logger parameter
- [ ] `hasMemoryMd()` and `isFirstRun()` helper functions unchanged
- [ ] File compiles with `npm run build`

---

### STORY-039.3: Adopt session-invalidation reconnection pattern

**Status:** planned
**Effort:** M
**Depends on:** STORY-039.2
**Context refs:** `openclaw-plugin/src/mcp_client.ts` (current retry loop in `callTool`/`readResource`/`callPrompt`), OpenClaw `src/browser/chrome-mcp.ts:325-328` (session invalidation), OpenClaw `src/agents/pi-bundle-mcp-tools.ts:211-216` (graceful degradation)
**Verification:** `cd openclaw-plugin && npm run build`

#### Why

The current client uses an active retry loop with exponential backoff (3 retries at 100/200/400ms). OpenClaw uses a different, simpler pattern: on error, invalidate the session (close + null), and the next call lazily re-creates it. This is simpler, matches OpenClaw's proven approach, and avoids holding up the caller with retry delays. The ContextEngine hooks in `index.ts` already have graceful fallback behavior (return empty results on error), so a single failed call is safe.

#### Acceptance Criteria

- [ ] `callTool()` wraps `client.callTool()` in try/catch; on transport/connection error: close client+transport, set to null, re-throw
- [ ] `readResource()` same pattern: try, catch → invalidate session, re-throw
- [ ] `callPrompt()` same pattern: try, catch → invalidate session, re-throw
- [ ] Before each RPC call, check `isRunning`; if not running, call `reconnect()` first (lazy re-creation)
- [ ] `RECONNECT_DELAYS_MS` constant removed
- [ ] No `for` loop retries in any RPC method
- [ ] Callers (`index.ts` hooks) handle errors via their existing graceful fallback paths — no changes needed in `index.ts`
- [ ] File compiles with `npm run build`

---

### STORY-039.4: Update tests for SDK-based client

**Status:** planned
**Effort:** M
**Depends on:** STORY-039.2, STORY-039.3
**Context refs:** `openclaw-plugin/tests/mcp_client.test.ts`
**Verification:** `cd openclaw-plugin && npm test`

#### Why

The existing tests validate internal protocol details (Content-Length framing, buffer parsing, request ID matching, manual handshake) that no longer exist after the rewrite. Tests must be rewritten to verify the SDK-based client's behavior: transport creation, client connection, method delegation, session invalidation, and process lifecycle. Tests should mock the SDK classes, not child processes.

#### Acceptance Criteria

- [ ] All Content-Length framing tests removed (SDK handles this)
- [ ] All JSON-RPC request/response matching tests removed (SDK handles this)
- [ ] All manual initialization handshake tests removed (SDK handles this)
- [ ] New tests for process lifecycle:
  - `start()` creates `StdioClientTransport` with correct command/args/stderr options
  - `start()` creates `Client` with correct name/version
  - `start()` calls `client.connect(transport)`
  - Double `start()` is a no-op
  - `stop()` calls `client.close()` then `transport.close()`
  - `stop()` swallows close errors (`.catch(() => {})`)
- [ ] New tests for RPC delegation:
  - `callTool(name, args)` delegates to `client.callTool({ name, arguments: args })`
  - `readResource(uri)` delegates to `client.readResource({ uri })`
  - `callPrompt(name, args)` delegates to `client.getPrompt({ name, arguments: args })`
- [ ] New tests for session invalidation:
  - On `callTool` error: client and transport are closed, set to null
  - Next `callTool` call after invalidation: reconnects (creates new transport + client)
  - `isRunning` returns false after invalidation
- [ ] New tests for `isRunning`:
  - Returns true when transport exists and `transport.pid !== null`
  - Returns false when transport is null
  - Returns false when `transport.pid === null` (process died)
- [ ] Helper function tests unchanged: `hasMemoryMd`, `isFirstRun`
- [ ] All tests pass with `npm test`

---

### STORY-039.5: End-to-end build and integration verification

**Status:** planned
**Effort:** S
**Depends on:** STORY-039.4
**Context refs:** `openclaw-plugin/src/index.ts`, `openclaw-plugin/tests/index.test.ts`
**Verification:** `cd openclaw-plugin && npm run build && npm test`

#### Why

The rewrite must be invisible to `index.ts` — same imports, same API, same behavior. This final story verifies end-to-end that the plugin builds, all tests (both `mcp_client.test.ts` and `index.test.ts`) pass, and no regressions exist. It also validates that `readResource()` and `callPrompt()` actually work via the SDK (since OpenClaw itself doesn't exercise these code paths, they're less battle-tested).

#### Acceptance Criteria

- [ ] `index.ts` has zero diff — no changes required
- [ ] `import { McpClient, hasMemoryMd, isFirstRun } from "./mcp_client.js"` still works
- [ ] `cd openclaw-plugin && npm run build` succeeds with no type errors
- [ ] `cd openclaw-plugin && npm test` — all tests pass (both `mcp_client.test.ts` and `index.test.ts`)
- [ ] `npm run lint` (tsc --noEmit) passes
- [ ] Verify SDK `Client` class has `readResource()` and `getPrompt()` methods (grep the installed `node_modules/@modelcontextprotocol/sdk` type declarations)
- [ ] Package size has not grown unreasonably (SDK is a reasonable dependency for an MCP plugin)

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-039.1 — Add SDK dependency | S | Foundation; zero-risk, unblocks all other stories |
| 2 | STORY-039.2 — Rewrite McpClient internals | L | The core fix; replaces custom transport with SDK |
| 3 | STORY-039.3 — Session-invalidation reconnection | M | Simplifies error handling to match OpenClaw patterns |
| 4 | STORY-039.4 — Update tests | M | Validates the rewrite against correct behavior |
| 5 | STORY-039.5 — End-to-end verification | S | Final gate; confirms zero regression in index.ts |

## Dependency Graph

```
039.1 (SDK dep) ──→ 039.2 (rewrite) ──→ 039.3 (reconnection) ──→ 039.4 (tests) ──→ 039.5 (e2e)
```

Linear chain — each story builds on the previous. 039.2 and 039.3 could be merged (reconnection is part of the rewrite), but separating them keeps reviews focused and the reconnection pattern is independently testable.

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| SDK `readResource()` or `getPrompt()` behave differently than hand-rolled impl | Medium | Medium | STORY-039.5 verifies these methods; fallback: wrap with adapter |
| SDK version drift (OpenClaw upgrades, our pin falls behind) | Low | Low | Pin `^1.27.0` allows minor bumps; OpenClaw peer dep already gates compatibility |
| `stop()` becoming async (SDK close is async, current `stop()` is sync) | High | Low | Make `stop()` async; `index.ts` `dispose()` already uses `await` pattern |
| Stderr piping API differs between SDK versions | Low | Low | Match OpenClaw's exact pattern from `pi-bundle-mcp-tools.ts:89-114` |
