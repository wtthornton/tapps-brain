# TypeScript SDK (`@tapps-brain/sdk`)

**Package:** `@tapps-brain/sdk`  
**npm:** `npm install @tapps-brain/sdk`  
**Status:** v1.0.0 (TAP-561, STORY-SC05)

The TypeScript SDK exposes the full tapps-brain agent memory API to Node.js
and TypeScript applications. It mirrors the Python-side `TappsBrainClient` in
`src/tapps_brain/client.py` and communicates with a deployed tapps-brain HTTP
container via MCP Streamable HTTP (`tools/call` JSON-RPC 2.0 to `/mcp`).

## Requirements

- Node.js ≥ 18.0.0 (uses native `fetch` + `crypto.randomUUID`)
- A running tapps-brain HTTP adapter (`docker compose up tapps-brain-http`)

## Quick start

```typescript
import { TappsBrainClient } from "@tapps-brain/sdk";

const brain = new TappsBrainClient({
  url: "http://brain.internal:8080",      // or TAPPS_BRAIN_URL env var
  projectId: "my-project",               // or TAPPS_BRAIN_PROJECT env var
  agentId: "my-agent",                   // or TAPPS_BRAIN_AGENT_ID env var
  authToken: process.env.TAPPS_BRAIN_AUTH_TOKEN,
});

// Save a fact
const key = await brain.remember("Prefer ruff over pylint for linting", {
  tier: "pattern",
});

// Recall relevant memories
const memories = await brain.recall("linting conventions", { maxResults: 5 });
for (const m of memories) {
  console.log(`[${m.tier}] ${m.key}: ${m.value}`);
}

// Record task outcomes for future reinforcement
await brain.learnSuccess("Migrated linting to ruff — CI green");
await brain.learnFailure("Direct SQLite connection failed", {
  error: "ADR-007: Postgres-only",
});

await brain.close();
```

## Constructor options

| Option | Type | Default | Description |
|---|---|---|---|
| `url` | `string` | `http://localhost:8080` | Brain HTTP URL. Also reads `TAPPS_BRAIN_URL`. |
| `projectId` | `string` | `"default"` | Project scope. Also reads `TAPPS_BRAIN_PROJECT`. |
| `agentId` | `string` | `"unknown"` | Agent identity. Also reads `TAPPS_BRAIN_AGENT_ID`. |
| `authToken` | `string` | — | Bearer token. Also reads `TAPPS_BRAIN_AUTH_TOKEN`. |
| `timeoutMs` | `number` | `30000` | Per-request timeout in milliseconds. |
| `maxRetries` | `number` | `2` | Retry attempts on transient `503`/`429`. |

## Environment variables

All constructor options fall back to env vars:

```bash
export TAPPS_BRAIN_URL=http://brain.internal:8080
export TAPPS_BRAIN_PROJECT=my-project
export TAPPS_BRAIN_AGENT_ID=my-agent
export TAPPS_BRAIN_AUTH_TOKEN=<token>
```

## API reference

### `remember(fact, options?)` → `Promise<string>`

Save a memory fact. Returns the generated key.

```typescript
const key = await brain.remember("Use TypeScript strict mode", {
  tier: "pattern",       // "architectural" | "pattern" | "procedural" | "context"
  share: false,          // propagate to Hive domain
  shareWith: "hive",     // "hive" | "domain" | "group:<name>"
});
```

### `recall(query, options?)` → `Promise<MemoryEntry[]>`

Recall memories via BM25 + vector hybrid search.

```typescript
const memories = await brain.recall("TypeScript configuration", {
  maxResults: 10,
});
```

### `forget(key, options?)` → `Promise<boolean>`

Archive a memory entry by key. Returns `true` if found and archived.

```typescript
const forgotten = await brain.forget("old-stale-fact");
```

### `learnSuccess(taskDescription, options?)` → `Promise<string>`

Record a successful outcome — boosts confidence of related memories.

```typescript
await brain.learnSuccess("Deployed v3.9.0 to production", { taskId: "T-123" });
```

### `learnFailure(description, options?)` → `Promise<string>`

Record a failure for future avoidance.

```typescript
await brain.learnFailure("Memory import OOM on 50k entries", {
  taskId: "T-456",
  error: "MemoryError: ...",
});
```

### Low-level `memory_*` API

For raw entry manipulation:

```typescript
// Save a raw key-value entry
await brain.memorySave("db-config", "host=localhost port=5432", {
  tier: "architectural",
  tags: ["infra", "postgres"],
});

// Retrieve by key
const entry = await brain.memoryGet("db-config");

// Full-text search
const results = await brain.memorySearch("postgres configuration");

// Reinforce (boost confidence + reset decay)
await brain.memoryReinforce("db-config", { confidenceBoost: 0.1 });

// Bulk operations
await brain.memorySaveMany([
  { key: "k1", value: "v1", tier: "context" },
  { key: "k2", value: "v2", tier: "context" },
]);
const recallMany = await brain.memoryRecallMany(["query 1", "query 2"]);
```

## Error handling

All errors extend `TappsBrainError`:

```typescript
import {
  TappsBrainError,
  AuthError,
  ProjectNotFoundError,
  RateLimitError,
  BrainDegradedError,
  NotFoundError,
} from "@tapps-brain/sdk";

try {
  await brain.recall("test");
} catch (err) {
  if (err instanceof ProjectNotFoundError) {
    // Register the project first: brain.status()
    console.error("Project not registered:", err.projectId);
  } else if (err instanceof RateLimitError) {
    // err.retryAfter is the suggested delay in seconds
    await sleep((err.retryAfter ?? 2) * 1000);
  } else if (err instanceof BrainDegradedError) {
    // Brain is temporarily degraded — retry with back-off
  } else if (err instanceof AuthError) {
    // Check TAPPS_BRAIN_AUTH_TOKEN
  }
}
```

| Error class | HTTP | Description |
|---|---|---|
| `AuthError` | 401 | Invalid or missing auth token |
| `ProjectNotFoundError` | 403 | Project not registered |
| `NotFoundError` | 404 | Memory key not found |
| `IdempotencyConflictError` | 409 | Idempotency key already used |
| `RateLimitError` | 429 | Rate limit exceeded |
| `InvalidRequestError` | 400 | Bad request parameters |
| `BrainDegradedError` | 503 | Brain temporarily unavailable |
| `InternalError` | 500 | Server-side error |

## Memory tiers

| Tier | Half-life | Use for |
|---|---|---|
| `architectural` | 180 days | System design decisions, tech-stack choices |
| `pattern` | 60 days | Coding conventions, API shapes |
| `procedural` | 30 days | Build/deploy commands, workflows |
| `context` | 14 days | Session-scope facts, transient state |

## Transport

The SDK uses MCP Streamable HTTP — sends `tools/call` JSON-RPC 2.0 requests
to the `/mcp` endpoint. This is identical to the Python client transport and
is compatible with any tapps-brain deployment ≥ v3.9.0.

All write operations (`remember`, `learnSuccess`, `learnFailure`, `memorySave`,
`memoryReinforce`) auto-generate a UUID idempotency key. On transient failures
(`503`, `429`) the same key is reused so retries are safe.

## Development

```bash
cd packages/sdk
npm install
npm run build       # compile TypeScript
npm test            # unit tests (no live server required)
npm run test:contract  # contract tests (requires TAPPS_BRAIN_URL)
```

## See also

- [LangGraph adapter](langgraph-adapter.md) — use tapps-brain as a LangGraph `BaseStore`
- [Python client](../guides/mcp-client-repo-setup.md) — Python-side equivalent
- [Fleet topology](agentforge-integration.md) — deploying tapps-brain in multi-agent fleets
- `src/tapps_brain/client.py` — reference Python implementation
