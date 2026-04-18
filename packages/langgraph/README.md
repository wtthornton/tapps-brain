# @tapps-brain/langgraph

LangGraph `BaseStore` adapter backed by [tapps-brain](https://github.com/wtthornton/tapps-brain) persistent agent memory.

[![npm version](https://img.shields.io/npm/v/@tapps-brain/langgraph)](https://www.npmjs.com/package/@tapps-brain/langgraph)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Install

```bash
npm install @tapps-brain/langgraph @tapps-brain/sdk @langchain/langgraph
```

**Requires:** Node.js ≥ 18.0.0 · `@langchain/langgraph` ≥ 0.2.0 · A running tapps-brain instance

## Quick start

```typescript
import { TappsBrainStore } from "@tapps-brain/langgraph";
import { StateGraph, Annotation } from "@langchain/langgraph";

const store = new TappsBrainStore({
  url: "http://brain.internal:8080",
  projectId: "my-langgraph-app",
  agentId: "graph-runner",
  authToken: process.env.TAPPS_BRAIN_AUTH_TOKEN,
});

// Standard BaseStore operations
await store.put(["memories", "alice"], "preferences", {
  style: "concise",
  language: "TypeScript",
});

const prefs = await store.get(["memories", "alice"], "preferences");
const results = await store.search(["memories"], { query: "alice" });

// Wire to a LangGraph graph
const builder = new StateGraph(StateAnnotation);
builder.addNode("agent", agentNode, { store });
const graph = builder.compile();
```

## Namespace mapping

LangGraph namespaces map to tapps-brain keys:

```
namespace = ["memories", "alice"]  key = "prefs"
  ──────────────────────────────────────────────
  tapps-brain key = "memories/alice/prefs"
```

All items are scoped to the configured `projectId` + `agentId`.

## Documentation

Full guide: [docs/guides/langgraph-adapter.md](../../docs/guides/langgraph-adapter.md)

## Related packages

- [`@tapps-brain/sdk`](../sdk/) — base TypeScript SDK
