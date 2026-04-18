/**
 * TappsBrainStore — LangGraph BaseStore adapter backed by tapps-brain.
 *
 * Implements the LangGraph `BaseStore` interface (≥0.2.0) using the
 * `@tapps-brain/sdk` TypeScript client.  All data is persisted in the
 * tapps-brain Postgres-backed memory store keyed by namespace + key.
 *
 * ## Namespace mapping
 *
 * LangGraph namespaces are joined with the configured separator (default `/`)
 * and prepended to the item key to form a tapps-brain memory key:
 *
 *   namespace = ["memories", "alice"]
 *   key       = "task-notes"
 *   ──────────────────────────────────────────────
 *   tapps-brain key = "memories/alice/task-notes"
 *
 * The `projectId` and `agentId` configured on the adapter scope all items
 * to a single tapps-brain agent.  Use distinct `projectId` values to shard
 * data between LangGraph applications.
 *
 * ## Usage
 *
 * ```typescript
 * import { TappsBrainStore } from "@tapps-brain/langgraph";
 *
 * const store = new TappsBrainStore({
 *   url: "http://brain.internal:8080",
 *   projectId: "my-langgraph-app",
 *   agentId: "graph-runner",
 *   authToken: process.env.TAPPS_BRAIN_AUTH_TOKEN,
 * });
 *
 * // Use as any LangGraph Store
 * const builder = StateGraph(stateAnnotation);
 * builder.addNode("agent", callModel, { store });
 * ```
 */

import { TappsBrainClient } from "@tapps-brain/sdk";
import type {
  Item,
  SearchItem,
  GetOperation,
  SearchOperation,
  PutOperation,
  ListNamespacesOperation,
  Operation,
  OperationResults,
  TappsBrainStoreOptions,
} from "./types.js";

// ---------------------------------------------------------------------------
// TappsBrainStore
// ---------------------------------------------------------------------------

/**
 * LangGraph-compatible store backed by tapps-brain persistent memory.
 *
 * Extends `BaseStore` from `@langchain/langgraph` when that package is
 * available; otherwise provides the full compatible API surface so the adapter
 * works with any LangGraph version ≥ 0.2.0 via structural typing.
 */
export class TappsBrainStore {
  private readonly _client: TappsBrainClient;
  private readonly _sep: string;

  constructor(options: TappsBrainStoreOptions = {}) {
    this._client = new TappsBrainClient({
      url: options.url,
      projectId: options.projectId,
      agentId: options.agentId,
      authToken: options.authToken,
      timeoutMs: options.timeoutMs,
      maxRetries: options.maxRetries,
    });
    this._sep = options.namespaceSeparator ?? "/";
  }

  // ---------------------------------------------------------------------------
  // Key helpers
  // ---------------------------------------------------------------------------

  /** Join namespace components and key into a tapps-brain memory key. */
  private _makeKey(namespace: readonly string[], key: string): string {
    const parts = [...namespace.map(String), key];
    return parts.join(this._sep);
  }

  /**
   * Derive the namespace and key from a tapps-brain memory key.
   *
   * This is the inverse of `_makeKey`. Since the key separator may appear
   * within namespace segments, we use the last separator-delimited segment
   * as the item key and everything before as the namespace.
   */
  private _parseKey(tappsKey: string): { namespace: string[]; key: string } {
    const parts = tappsKey.split(this._sep);
    const key = parts.pop() ?? tappsKey;
    return { namespace: parts, key };
  }

  // ---------------------------------------------------------------------------
  // batch — the core operation dispatcher (implements BaseStore)
  // ---------------------------------------------------------------------------

  /**
   * Execute a batch of store operations atomically from the caller's
   * perspective (each is dispatched to tapps-brain individually; no
   * server-side batch transaction is guaranteed).
   */
  async batch<Ops extends readonly Operation[]>(
    operations: Ops,
  ): Promise<OperationResults<Ops>> {
    const results = await Promise.all(
      operations.map((op) => this._dispatch(op)),
    );
    return results as OperationResults<Ops>;
  }

  private async _dispatch(op: Operation): Promise<unknown> {
    if (isGetOperation(op)) return this._get(op);
    if (isSearchOperation(op)) return this._search(op);
    if (isPutOperation(op)) return this._put(op);
    if (isListNamespacesOperation(op)) return this._listNamespaces(op);
    throw new Error(`Unknown operation type: ${JSON.stringify(op)}`);
  }

  // ---------------------------------------------------------------------------
  // Individual operation handlers
  // ---------------------------------------------------------------------------

  private async _get(op: GetOperation): Promise<Item | null> {
    const tappsKey = this._makeKey(op.namespace, op.key);
    try {
      const raw = await this._client.memoryGet(tappsKey);
      if (!raw || raw["error"]) return null;
      return this._toItem(op.namespace, op.key, raw);
    } catch {
      return null;
    }
  }

  private async _search(op: SearchOperation): Promise<SearchItem[]> {
    const prefixStr = op.namespacePrefix.join(this._sep);
    const query = op.query ?? prefixStr;
    const limit = op.limit ?? 10;
    const offset = op.offset ?? 0;

    const entries = await this._client.memorySearch(query, { limit: limit + offset });
    const relevant = entries.filter((e) =>
      e.key.startsWith(prefixStr),
    );
    const paged = relevant.slice(offset);

    return paged.map((e) => {
      const { namespace, key } = this._parseKey(e.key);
      return {
        ...this._toItem(namespace, key, e as unknown as Record<string, unknown>),
        score: typeof e.score === "number" ? e.score : null,
      };
    });
  }

  private async _put(op: PutOperation): Promise<void> {
    const tappsKey = this._makeKey(op.namespace, op.key);
    if (op.value === null) {
      // Treat null value as a delete
      await this._client.forget(tappsKey).catch(() => undefined);
      return;
    }
    // Serialise the entire value object as JSON
    const serialised = JSON.stringify(op.value);
    await this._client.memorySave(tappsKey, serialised, { tier: "context" });
  }

  private async _listNamespaces(
    op: ListNamespacesOperation,
  ): Promise<string[][]> {
    // tapps-brain doesn't have a native namespace-list endpoint.
    // We approximate by searching with an empty query and collecting prefixes.
    const limit = op.limit ?? 100;
    const offset = op.offset ?? 0;
    const maxDepth = op.maxDepth;

    const entries = await this._client.memorySearch("", { limit: limit + offset });
    const seen = new Set<string>();
    const namespaces: string[][] = [];

    for (const e of entries) {
      const parts = e.key.split(this._sep);
      // Drop the trailing key segment to get the namespace
      parts.pop();
      if (parts.length === 0) continue;

      const ns = maxDepth !== undefined ? parts.slice(0, maxDepth) : parts;

      // Apply matchConditions
      if (op.matchConditions?.length) {
        const matches = op.matchConditions.every((cond) => {
          if (cond.matchType === "prefix") {
            return cond.path.every((seg, i) => ns[i] === seg);
          }
          // suffix
          const offset2 = ns.length - cond.path.length;
          return cond.path.every((seg, i) => ns[offset2 + i] === seg);
        });
        if (!matches) continue;
      }

      const nsKey = ns.join(this._sep);
      if (!seen.has(nsKey)) {
        seen.add(nsKey);
        namespaces.push(ns);
      }
    }

    return namespaces.slice(offset, offset + limit);
  }

  // ---------------------------------------------------------------------------
  // Compatibility helpers (BaseStore surface)
  // ---------------------------------------------------------------------------

  /**
   * Retrieve a single item.
   *
   * Equivalent to `batch([{ namespace, key }])[0]`.
   */
  async get(namespace: string[], key: string): Promise<Item | null> {
    return this._get({ namespace, key });
  }

  /**
   * Save an item. Pass `null` as the value to delete.
   */
  async put(
    namespace: string[],
    key: string,
    value: Record<string, unknown> | null,
  ): Promise<void> {
    return this._put({ namespace, key, value });
  }

  /**
   * Delete an item (convenience wrapper — equivalent to `put(..., null)`).
   */
  async delete(namespace: string[], key: string): Promise<void> {
    return this._put({ namespace, key, value: null });
  }

  /**
   * Search within a namespace prefix.
   */
  async search(
    namespacePrefix: string[],
    options: {
      query?: string;
      filter?: Record<string, unknown>;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<SearchItem[]> {
    return this._search({
      namespacePrefix,
      query: options.query,
      filter: options.filter,
      limit: options.limit,
      offset: options.offset,
    });
  }

  /**
   * List distinct namespace paths.
   */
  async listNamespaces(
    options: {
      prefix?: string[];
      suffix?: string[];
      maxDepth?: number;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<string[][]> {
    const matchConditions: Array<{ matchType: "prefix" | "suffix"; path: string[] }> = [];
    if (options.prefix?.length) {
      matchConditions.push({ matchType: "prefix", path: options.prefix });
    }
    if (options.suffix?.length) {
      matchConditions.push({ matchType: "suffix", path: options.suffix });
    }
    return this._listNamespaces({
      matchConditions: matchConditions.length ? matchConditions : undefined,
      maxDepth: options.maxDepth,
      limit: options.limit,
      offset: options.offset,
    });
  }

  // ---------------------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------------------

  /** Release client resources. */
  close(): void {
    this._client.close();
  }

  async [Symbol.asyncDispose](): Promise<void> {
    this.close();
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  private _toItem(
    namespace: readonly string[] | string[],
    key: string,
    raw: Record<string, unknown>,
  ): Item {
    let value: Record<string, unknown>;
    const rawValue = raw["value"];
    if (typeof rawValue === "string") {
      try {
        value = JSON.parse(rawValue) as Record<string, unknown>;
      } catch {
        value = { text: rawValue };
      }
    } else if (rawValue !== null && typeof rawValue === "object") {
      value = rawValue as Record<string, unknown>;
    } else {
      // Fall back: expose the entire raw entry minus internal fields
      const { key: _k, tier: _t, confidence: _c, embedding: _e, ...rest } = raw;
      void _k;
      void _t;
      void _c;
      void _e;
      value = rest;
    }

    const createdAt =
      typeof raw["created_at"] === "string"
        ? new Date(raw["created_at"])
        : new Date();
    const updatedAt =
      typeof raw["updated_at"] === "string"
        ? new Date(raw["updated_at"])
        : new Date();

    return {
      namespace: Array.from(namespace),
      key,
      value,
      createdAt,
      updatedAt,
    };
  }
}

// ---------------------------------------------------------------------------
// Operation type guards
// ---------------------------------------------------------------------------

function isGetOperation(op: Operation): op is GetOperation {
  return "key" in op && "namespace" in op && !("value" in op) && !("namespacePrefix" in op) && !("matchConditions" in op);
}

function isSearchOperation(op: Operation): op is SearchOperation {
  return "namespacePrefix" in op;
}

function isPutOperation(op: Operation): op is PutOperation {
  return "key" in op && "value" in op;
}

function isListNamespacesOperation(op: Operation): op is ListNamespacesOperation {
  return !("key" in op) && !("namespacePrefix" in op);
}
