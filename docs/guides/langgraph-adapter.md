# LangGraph Store Adapter (`@tapps-brain/langgraph`)

**Package:** `@tapps-brain/langgraph`  
**npm:** `npm install @tapps-brain/langgraph`  
**Status:** v1.0.0 (TAP-561, STORY-SC05)

The LangGraph adapter exposes tapps-brain as a `BaseStore`-compatible store
for LangGraph agents. It enables long-term, cross-session memory for any
LangGraph graph or tool, backed by tapps-brain's Postgres-persistent store.

## Requirements

- Node.js ≥ 18.0.0
- `@langchain/langgraph` ≥ 0.2.0 (peer dependency)
- `@tapps-brain/sdk` ≥ 1.0.0
- A running tapps-brain HTTP adapter

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

// Put an item
await store.put(["memories", "alice"], "preferences", {
  content: "Alice prefers concise, technical answers",
  language: "Python",
});

// Retrieve it
const item = await store.get(["memories", "alice"], "preferences");
console.log(item?.value.content);

// Search within a namespace prefix
const results = await store.search(["memories"], {
  query: "alice preferences",
  limit: 5,
});

// Use with a LangGraph StateGraph
const StateAnnotation = Annotation.Root({ messages: Annotation<string[]> });

const builder = new StateGraph(StateAnnotation);
builder.addNode("agent", agentNode, { store });

const graph = builder.compile();
await graph.invoke({ messages: ["Help me write Python code"] });

store.close();
```

## Constructor options

| Option | Type | Default | Description |
|---|---|---|---|
| `url` | `string` | `http://localhost:8080` | Brain HTTP URL |
| `projectId` | `string` | `"default"` | Project scope |
| `agentId` | `string` | `"unknown"` | Agent identity |
| `authToken` | `string` | — | Bearer token |
| `namespaceSeparator` | `string` | `"/"` | Namespace join character in tapps-brain keys |
| `timeoutMs` | `number` | `30000` | Per-request timeout (ms) |
| `maxRetries` | `number` | `2` | Retry attempts on `503`/`429` |

## Namespace mapping

LangGraph namespaces map to tapps-brain keys by joining namespace components
with the separator and appending the item key:

| LangGraph namespace | LangGraph key | tapps-brain key |
|---|---|---|
| `["memories", "alice"]` | `"prefs"` | `"memories/alice/prefs"` |
| `["checkpoints"]` | `"run-001"` | `"checkpoints/run-001"` |
| `["tools", "web-search"]` | `"cache-xyz"` | `"tools/web-search/cache-xyz"` |

All items share the `projectId` and `agentId` configured on the store. Use
distinct `projectId` values to shard data between different LangGraph
applications sharing the same brain instance.

## API reference

The store implements the full `BaseStore` interface:

### `put(namespace, key, value)` → `Promise<void>`

Save an item. `value` must be a JSON-serialisable object. Passing `null`
deletes the item (equivalent to `delete()`).

```typescript
await store.put(["memories", "alice"], "task-context", {
  summary: "Working on TypeScript migration",
  files: ["src/index.ts", "src/types.ts"],
});

// Delete
await store.put(["memories", "alice"], "task-context", null);
```

### `get(namespace, key)` → `Promise<Item | null>`

Retrieve an item. Returns `null` if not found.

```typescript
const item = await store.get(["memories", "alice"], "task-context");
if (item) {
  console.log(item.value);     // { summary: "...", files: [...] }
  console.log(item.createdAt); // Date
  console.log(item.updatedAt); // Date
}
```

### `delete(namespace, key)` → `Promise<void>`

Delete an item.

```typescript
await store.delete(["memories", "alice"], "stale-context");
```

### `search(namespacePrefix, options?)` → `Promise<SearchItem[]>`

Search within a namespace prefix. Supports free-text query via BM25 + vector
hybrid retrieval.

```typescript
const results = await store.search(["memories"], {
  query: "TypeScript preferences",
  limit: 10,
  offset: 0,
});

for (const item of results) {
  console.log(`[score=${item.score?.toFixed(2)}] ${item.namespace.join("/")}/${item.key}`);
  console.log(item.value);
}
```

### `listNamespaces(options?)` → `Promise<string[][]>`

List distinct namespace paths that exist in the store.

```typescript
const namespaces = await store.listNamespaces({
  prefix: ["memories"],  // only namespaces starting with "memories"
  maxDepth: 2,           // truncate at depth 2
  limit: 50,
});
// → [["memories", "alice"], ["memories", "bob"], ...]
```

### `batch(operations)` → `Promise<OperationResults>`

Execute multiple operations in parallel. Each result maps to its input
operation type.

```typescript
const [item, searchResults, , namespaces] = await store.batch([
  { namespace: ["memories", "alice"], key: "prefs" },        // Get
  { namespacePrefix: ["memories"], query: "task" },          // Search
  { namespace: ["memories", "alice"], key: "notes",          // Put
    value: { text: "new note" } },
  {},                                                         // ListNamespaces
] as const);
```

## Using in a LangGraph node

```typescript
import { TappsBrainStore } from "@tapps-brain/langgraph";
import { RunnableConfig } from "@langchain/core/runnables";
import { LangGraphRunnableConfig } from "@langchain/langgraph";

const store = new TappsBrainStore({
  url: process.env.TAPPS_BRAIN_URL!,
  projectId: "my-agent",
  authToken: process.env.TAPPS_BRAIN_AUTH_TOKEN,
});

async function agentNode(
  state: { messages: string[] },
  config: LangGraphRunnableConfig,
) {
  // Access the store from the config
  const nodeStore = config.store;

  // Recall user preferences
  const prefs = await nodeStore?.get(["user", "alice"], "preferences");

  // ... process messages with context from prefs ...

  // Save new information
  await nodeStore?.put(["user", "alice"], "last-topic", {
    topic: "TypeScript SDK",
    timestamp: new Date().toISOString(),
  });

  return { messages: [...state.messages, "Response here"] };
}

const builder = new StateGraph(StateAnnotation);
builder.addNode("agent", agentNode, { store });
const graph = builder.compile();
```

## Item schema

Each stored item has:

```typescript
interface Item {
  namespace: string[];          // e.g. ["memories", "alice"]
  key: string;                  // e.g. "preferences"
  value: Record<string, unknown>; // your JSON object
  createdAt: Date;
  updatedAt: Date;
}

interface SearchItem extends Item {
  score?: number | null;        // relevance score from search
}
```

## Limitations

- **Write ordering:** `batch()` dispatches all operations in parallel. There is
  no transactional guarantee across operations.
- **listNamespaces accuracy:** Namespace listing is approximate — it scans
  stored keys and extracts prefixes. Namespaces with no surviving entries may
  not appear.
- **filter:** The `filter` field in `SearchOperation` is accepted but not
  currently applied server-side (requires tapps-brain to expose filter-aware
  endpoints). Items are filtered on the client by namespace prefix.
- **Delete propagation:** Deleting an item via `put(..., null)` or `delete()`
  archives it in tapps-brain (not a hard delete). Archived entries are excluded
  from search results but remain in the audit trail.

## Development

```bash
cd packages/langgraph
npm install
npm run build   # compile TypeScript
npm test        # unit tests (no live server required)
```

## See also

- [TypeScript SDK](typescript-sdk.md) — base SDK powering this adapter
- [LangGraph documentation](https://langchain-ai.github.io/langgraphjs/) — LangGraph JS/TS docs
- [tapps-brain fleet topology](agentforge-integration.md) — deployment guide
- [Hive multi-agent sharing](hive.md) — share memories across agents
