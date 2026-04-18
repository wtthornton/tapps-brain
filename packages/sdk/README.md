# @tapps-brain/sdk

TypeScript SDK for [tapps-brain](https://github.com/wtthornton/tapps-brain) — a Postgres-backed persistent memory system for AI agents.

[![npm version](https://img.shields.io/npm/v/@tapps-brain/sdk)](https://www.npmjs.com/package/@tapps-brain/sdk)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Install

```bash
npm install @tapps-brain/sdk
```

**Requires:** Node.js ≥ 18.0.0 · A running tapps-brain instance

## Quick start

```typescript
import { TappsBrainClient } from "@tapps-brain/sdk";

const brain = new TappsBrainClient({
  url: "http://brain.internal:8080",
  projectId: "my-project",
  agentId: "my-agent",
  authToken: process.env.TAPPS_BRAIN_AUTH_TOKEN,
});

// Save a fact
const key = await brain.remember("Prefer ruff over pylint for linting", {
  tier: "pattern",
});

// Recall relevant memories (BM25 + vector hybrid)
const memories = await brain.recall("linting conventions");
for (const m of memories) {
  console.log(`[${m.tier}] ${m.key}: ${m.value}`);
}

// Record outcomes for reinforcement learning
await brain.learnSuccess("Migrated linting to ruff — CI green");

await brain.close();
```

## Features

- **Full API parity** with the Python `TappsBrainClient` (`remember`, `recall`, `forget`, `learnSuccess`, `learnFailure`, `memorySave`, `memoryGet`, `memorySearch`, `memoryReinforce`, `memorySaveMany`, `memoryRecallMany`)
- **Idempotent writes** — auto-generated UUID keys prevent duplicate writes on retry
- **Typed errors** — `AuthError`, `ProjectNotFoundError`, `RateLimitError`, `BrainDegradedError`, etc.
- **Auto-retry** — configurable back-off on `429` / `503` responses
- **Zero runtime deps** — uses Node.js 18+ native `fetch` and `crypto.randomUUID`
- **MCP transport** — communicates via MCP Streamable HTTP (`tools/call` JSON-RPC 2.0 to `/mcp`)

## Environment variables

| Variable | Description |
|---|---|
| `TAPPS_BRAIN_URL` | Brain HTTP URL (default: `http://localhost:8080`) |
| `TAPPS_BRAIN_PROJECT` | Project identifier |
| `TAPPS_BRAIN_AGENT_ID` | Agent identifier |
| `TAPPS_BRAIN_AUTH_TOKEN` | Bearer auth token |

## Documentation

Full guide: [docs/guides/typescript-sdk.md](../../docs/guides/typescript-sdk.md)

## Related packages

- [`@tapps-brain/langgraph`](../langgraph/) — LangGraph `BaseStore` adapter
