/**
 * LangGraph Store type definitions for the tapps-brain adapter.
 *
 * These interfaces are compatible with `@langchain/langgraph` ≥ 0.2.0.
 * They are inlined here so that the type definitions are available even
 * before `@langchain/langgraph` is resolved (e.g., in type-check-only mode).
 */

// ---------------------------------------------------------------------------
// LangGraph Store protocol — compatible with @langchain/langgraph ≥0.2.0
// ---------------------------------------------------------------------------

/** A stored item in the LangGraph store. */
export interface Item {
  /** Hierarchical namespace path (e.g. `["memories", "alice"]`). */
  namespace: readonly string[];
  /** Unique key within the namespace. */
  key: string;
  /** Arbitrary JSON-serialisable value object. */
  value: Record<string, unknown>;
  /** Creation timestamp. */
  createdAt: Date;
  /** Last-update timestamp. */
  updatedAt: Date;
}

/** An item with an optional relevance score from vector/BM25 search. */
export interface SearchItem extends Item {
  score?: number | null;
}

/** Namespace match condition for `listNamespaces`. */
export interface MatchCondition {
  matchType: "prefix" | "suffix";
  path: string[];
}

// ---------------------------------------------------------------------------
// Operation types
// ---------------------------------------------------------------------------

/** Retrieve a single item by namespace + key. */
export interface GetOperation {
  namespace: readonly string[];
  key: string;
}

/** Full-text or semantic search within a namespace prefix. */
export interface SearchOperation {
  namespacePrefix: readonly string[];
  query?: string;
  filter?: Record<string, unknown>;
  limit?: number;
  offset?: number;
}

/**
 * Save an item. If `value` is `null`, the item is deleted (same as
 * issuing a `DeleteOperation`).
 */
export interface PutOperation {
  namespace: readonly string[];
  key: string;
  value: Record<string, unknown> | null;
  /** Optional index fields (ignored by tapps-brain — all fields are indexed). */
  index?: false | string[];
}

/** List distinct namespace paths that match the given conditions. */
export interface ListNamespacesOperation {
  matchConditions?: MatchCondition[];
  maxDepth?: number;
  limit?: number;
  offset?: number;
}

/** Union of all supported operation types. */
export type Operation =
  | GetOperation
  | SearchOperation
  | PutOperation
  | ListNamespacesOperation;

/** Map from operation type to its result type. */
export type OperationResult<Op extends Operation> =
  Op extends GetOperation
    ? Item | null
    : Op extends SearchOperation
    ? SearchItem[]
    : Op extends PutOperation
    ? void
    : Op extends ListNamespacesOperation
    ? string[][]
    : never;

/** Tuple of results matching the input operation tuple. */
export type OperationResults<Ops extends readonly Operation[]> = {
  [K in keyof Ops]: OperationResult<Ops[K]>;
};

// ---------------------------------------------------------------------------
// Adapter options
// ---------------------------------------------------------------------------

/** Options for {@link TappsBrainStore}. */
export interface TappsBrainStoreOptions {
  /**
   * tapps-brain HTTP/MCP URL (default: `http://localhost:8080`).
   * Falls back to `TAPPS_BRAIN_URL` env var.
   */
  url?: string;
  /**
   * Project identifier scoping all stored items.
   * Falls back to `TAPPS_BRAIN_PROJECT` env var.
   */
  projectId?: string;
  /**
   * Default agent identifier.
   * Falls back to `TAPPS_BRAIN_AGENT_ID` env var.
   */
  agentId?: string;
  /** Bearer auth token. Falls back to `TAPPS_BRAIN_AUTH_TOKEN` env var. */
  authToken?: string;
  /**
   * Separator used to join namespace components into a key prefix.
   * Default: `"/"`
   */
  namespaceSeparator?: string;
  /** Request timeout in milliseconds (default: 30 000). */
  timeoutMs?: number;
  /** Maximum retry attempts on transient failure (default: 2). */
  maxRetries?: number;
}
