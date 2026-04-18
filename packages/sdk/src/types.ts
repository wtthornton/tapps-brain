/**
 * TypeScript types for the tapps-brain SDK.
 *
 * All types mirror the Python-side Pydantic models in
 * `src/tapps_brain/models.py` and the `TappsBrainClient` method signatures
 * in `src/tapps_brain/client.py`.
 */

// ---------------------------------------------------------------------------
// Memory tier and source enumerations
// ---------------------------------------------------------------------------

/** Memory tier controls decay half-life and retrieval ranking weight. */
export type MemoryTier = "architectural" | "pattern" | "procedural" | "context";

/** Origin of the memory entry. */
export type MemorySource = "human" | "agent" | "inferred" | "system";

/** Hive propagation scope. */
export type AgentScope = "private" | "domain" | "hive" | `group:${string}`;

// ---------------------------------------------------------------------------
// Core data models
// ---------------------------------------------------------------------------

/**
 * A single persisted memory entry.
 *
 * Maps to `MemoryEntry` in `src/tapps_brain/models.py`.
 */
export interface MemoryEntry {
  key: string;
  value: string;
  tier: MemoryTier;
  confidence: number;
  source: MemorySource;
  tags: string[];
  created_at: string;
  updated_at: string;
  last_accessed: string;
  access_count: number;
  agent_scope: AgentScope;
  memory_group?: string | null;
  valid_from: string;
  valid_until: string;
  embedding?: number[] | null;
  embedding_model_id?: string | null;
  positive_feedback_count: number;
  negative_feedback_count: number;
  stale?: boolean;
  score?: number;
}

/**
 * Result shape returned by recall and search operations.
 *
 * Maps to `RecallResult` in `src/tapps_brain/models.py`.
 */
export interface RecallResult {
  memory_section: string;
  memories: MemoryEntry[];
  token_count: number;
  recall_time_ms: number;
  truncated: boolean;
  memory_count: number;
  hive_memory_count: number;
  quality_warning?: string | null;
  recall_diagnostics?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Client option types
// ---------------------------------------------------------------------------

/** Options for `TappsBrainClient` constructor. */
export interface TappsBrainClientOptions {
  /** Brain HTTP/MCP URL (default: `http://localhost:8080`). */
  url?: string;
  /** Project identifier. Falls back to `TAPPS_BRAIN_PROJECT` env var. */
  projectId?: string;
  /** Agent identifier. Falls back to `TAPPS_BRAIN_AGENT_ID` env var. */
  agentId?: string;
  /** Bearer auth token. Falls back to `TAPPS_BRAIN_AUTH_TOKEN` env var. */
  authToken?: string;
  /** Request timeout in milliseconds (default: 30 000). */
  timeoutMs?: number;
  /** Maximum retry attempts on transient failure (default: 2). */
  maxRetries?: number;
}

/** Options for `remember()`. */
export interface RememberOptions {
  /** Memory tier controlling decay rate (default: `"procedural"`). */
  tier?: MemoryTier;
  /** Share memory to the Hive domain (default: `false`). */
  share?: boolean;
  /** Specific Hive scope or group target (e.g., `"hive"`, `"group:frontend"`). */
  shareWith?: string;
  /** Override the agent ID for this call (defaults to the client's agent ID). */
  agentId?: string;
}

/** Options for `recall()`. */
export interface RecallOptions {
  /** Maximum memories to return (default: 5). */
  maxResults?: number;
  /** Override the agent ID for this call. */
  agentId?: string;
}

/** Options for `forget()`. */
export interface ForgetOptions {
  /** Override the agent ID for this call. */
  agentId?: string;
}

/** Options for `learnSuccess()`. */
export interface LearnSuccessOptions {
  /** Task identifier for correlation. */
  taskId?: string;
  /** Override the agent ID for this call. */
  agentId?: string;
}

/** Options for `learnFailure()`. */
export interface LearnFailureOptions {
  /** Task identifier for correlation. */
  taskId?: string;
  /** Error message or exception text. */
  error?: string;
  /** Override the agent ID for this call. */
  agentId?: string;
}

/** Options for `memorySave()`. */
export interface MemorySaveOptions {
  tier?: MemoryTier;
  source?: MemorySource;
  tags?: string[];
  confidence?: number;
  agentScope?: AgentScope;
  memoryGroup?: string;
  agentId?: string;
}

/** Options for `memorySearch()`. */
export interface MemorySearchOptions {
  tier?: MemoryTier;
  limit?: number;
  agentId?: string;
}

/** Options for `memoryRecall()`. */
export interface MemoryRecallOptions {
  limit?: number;
  agentId?: string;
}

/** Options for `memoryReinforce()`. */
export interface MemoryReinforceOptions {
  confidenceBoost?: number;
  agentId?: string;
}

/** Options for `memorySaveMany()`. */
export interface MemorySaveManyOptions {
  agentId?: string;
}

/** Options for `memoryRecallMany()`. */
export interface MemoryRecallManyOptions {
  agentId?: string;
}

/** Options for `memoryReinforceMany()`. */
export interface MemoryReinforceOptions2 {
  agentId?: string;
}
