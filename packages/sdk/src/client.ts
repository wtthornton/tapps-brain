/**
 * TappsBrainClient — TypeScript SDK for tapps-brain persistent agent memory.
 *
 * Communicates with the deployed tapps-brain HTTP adapter via MCP Streamable
 * HTTP (`tools/call` JSON-RPC 2.0 to `/mcp`), mirroring the Python-side
 * `TappsBrainClient` in `src/tapps_brain/client.py`.
 *
 * All write operations auto-generate an idempotency key that is reused on
 * retry, preventing duplicate writes on transient failures.
 *
 * Usage:
 * ```typescript
 * import { TappsBrainClient } from "@tapps-brain/sdk";
 *
 * const brain = new TappsBrainClient({
 *   url: "http://brain.internal:8080",
 *   projectId: "my-project",
 *   agentId: "my-agent",
 *   authToken: process.env.TAPPS_BRAIN_AUTH_TOKEN,
 * });
 *
 * const key = await brain.remember("Use ruff for linting");
 * const memories = await brain.recall("linting conventions");
 * await brain.close();
 * ```
 */

import { randomUUID } from "node:crypto";
import {
  type TappsBrainClientOptions,
  type MemoryEntry,
  type RememberOptions,
  type RecallOptions,
  type ForgetOptions,
  type LearnSuccessOptions,
  type LearnFailureOptions,
  type MemorySaveOptions,
  type MemorySearchOptions,
  type MemoryRecallOptions,
  type MemoryReinforceOptions,
  type MemorySaveManyOptions,
} from "./types.js";
import {
  parseErrorResponse,
  BrainDegradedError,
  RateLimitError,
  TappsBrainError,
} from "./errors.js";

// ---------------------------------------------------------------------------
// Write tools — these auto-generate an idempotency key
// ---------------------------------------------------------------------------

const WRITE_TOOLS = new Set([
  "brain_remember",
  "brain_learn_success",
  "brain_learn_failure",
  "memory_save",
  "memory_reinforce",
  "memory_save_many",
  "memory_reinforce_many",
  "memory_supersede",
]);

// ---------------------------------------------------------------------------
// MCP JSON-RPC helpers
// ---------------------------------------------------------------------------

function buildHeaders(
  projectId: string,
  agentId: string,
  authToken: string | undefined,
  idempotencyKey?: string,
): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Project-Id": projectId,
    "X-Tapps-Agent": agentId,
  };
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }
  if (idempotencyKey) {
    headers["X-Idempotency-Key"] = idempotencyKey;
  }
  return headers;
}

function buildMcpEnvelope(
  toolName: string,
  args: Record<string, unknown>,
  projectId: string,
  agentId: string,
  idempotencyKey?: string,
): string {
  const meta: Record<string, unknown> = { project_id: projectId, agent_id: agentId };
  if (idempotencyKey) {
    meta.idempotency_key = idempotencyKey;
  }
  return JSON.stringify({
    jsonrpc: "2.0",
    method: "tools/call",
    params: {
      name: toolName,
      arguments: args,
      _meta: meta,
    },
    id: 1,
  });
}

function unwrapMcpResult(data: unknown): unknown {
  if (data !== null && typeof data === "object" && "result" in data) {
    const result = (data as Record<string, unknown>).result;
    if (result !== null && typeof result === "object" && "content" in result) {
      const content = (result as Record<string, unknown>).content;
      if (Array.isArray(content) && content.length > 0) {
        const first = content[0] as Record<string, unknown>;
        const raw = typeof first.text === "string" ? first.text : "{}";
        try {
          return JSON.parse(raw);
        } catch {
          return raw;
        }
      }
    }
  }
  return data;
}

// ---------------------------------------------------------------------------
// Retry helpers
// ---------------------------------------------------------------------------

function isRetryable(err: TappsBrainError): boolean {
  return err instanceof BrainDegradedError || err instanceof RateLimitError;
}

function retryDelayMs(err: TappsBrainError, attempt: number): number {
  if (err instanceof RateLimitError && err.retryAfter !== undefined) {
    return err.retryAfter * 1000;
  }
  return Math.pow(2, attempt) * 1000;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---------------------------------------------------------------------------
// TappsBrainClient
// ---------------------------------------------------------------------------

/**
 * Synchronous-style async client for tapps-brain.
 *
 * All operations are `async` — use `await` or `.then()` chains.
 *
 * Call {@link close} (or use `await using`) to release resources when done.
 */
export class TappsBrainClient {
  private readonly _url: string;
  private readonly _projectId: string;
  private readonly _agentId: string;
  private readonly _authToken: string | undefined;
  private readonly _timeoutMs: number;
  private readonly _maxRetries: number;
  private _closed = false;

  /**
   * Create a new TappsBrainClient.
   *
   * Configuration falls back to environment variables:
   * - `TAPPS_BRAIN_PROJECT` — project identifier
   * - `TAPPS_BRAIN_AGENT_ID` — agent identifier
   * - `TAPPS_BRAIN_AUTH_TOKEN` — bearer auth token
   * - `TAPPS_BRAIN_URL` — base URL
   */
  constructor(options: TappsBrainClientOptions = {}) {
    this._url = (options.url ?? process.env["TAPPS_BRAIN_URL"] ?? "http://localhost:8080").replace(
      /\/+$/,
      "",
    );
    this._projectId =
      options.projectId ?? process.env["TAPPS_BRAIN_PROJECT"] ?? "default";
    this._agentId =
      options.agentId ?? process.env["TAPPS_BRAIN_AGENT_ID"] ?? "unknown";
    this._authToken =
      options.authToken ?? process.env["TAPPS_BRAIN_AUTH_TOKEN"];
    this._timeoutMs = options.timeoutMs ?? 30_000;
    this._maxRetries = options.maxRetries ?? 2;
  }

  // --- Internal transport ---

  private async _postTool(
    toolName: string,
    args: Record<string, unknown>,
    idempotencyKey?: string,
  ): Promise<unknown> {
    const headers = buildHeaders(
      this._projectId,
      this._agentId,
      this._authToken,
      idempotencyKey,
    );
    const body = buildMcpEnvelope(
      toolName,
      args,
      this._projectId,
      this._agentId,
      idempotencyKey,
    );

    let lastErr: TappsBrainError | undefined;

    for (let attempt = 0; attempt <= this._maxRetries; attempt++) {
      const controller = new AbortController();
      const timerId = setTimeout(
        () => controller.abort(),
        this._timeoutMs,
      );
      try {
        const resp = await fetch(`${this._url}/mcp`, {
          method: "POST",
          headers,
          body,
          signal: controller.signal,
        });

        clearTimeout(timerId);

        if (resp.ok) {
          return unwrapMcpResult(await resp.json() as unknown);
        }

        let errorBody: Record<string, unknown>;
        try {
          errorBody = await resp.json() as Record<string, unknown>;
        } catch {
          errorBody = {};
        }

        const err = parseErrorResponse(resp.status, errorBody);

        if (isRetryable(err) && attempt < this._maxRetries) {
          await sleep(retryDelayMs(err, attempt));
          lastErr = err;
          continue;
        }

        throw err;
      } catch (err) {
        clearTimeout(timerId);
        if (err instanceof TappsBrainError) {
          throw err;
        }
        // Network-level errors (fetch failure, abort)
        throw new TappsBrainError(
          err instanceof Error ? err.message : String(err),
          { cause: err },
        );
      }
    }

    throw lastErr ?? new TappsBrainError("Unexpected retry exhaustion");
  }

  private async _tool(
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<unknown> {
    const idempotencyKey = WRITE_TOOLS.has(toolName)
      ? randomUUID()
      : undefined;
    return this._postTool(toolName, args, idempotencyKey);
  }

  // --- AgentBrain-compatible API ---

  /**
   * Save a memory fact.
   *
   * @returns The generated memory key.
   */
  async remember(
    fact: string,
    options: RememberOptions = {},
  ): Promise<string> {
    const result = await this._tool("brain_remember", {
      fact,
      tier: options.tier ?? "procedural",
      share: options.share ?? false,
      share_with: options.shareWith ?? "",
      agent_id: options.agentId ?? "",
    });
    return isRecord(result) && typeof result["key"] === "string"
      ? result["key"]
      : String(result);
  }

  /**
   * Recall memories matching the given query.
   */
  async recall(
    query: string,
    options: RecallOptions = {},
  ): Promise<MemoryEntry[]> {
    const result = await this._tool("brain_recall", {
      query,
      max_results: options.maxResults ?? 5,
      agent_id: options.agentId ?? "",
    });
    return Array.isArray(result) ? (result as MemoryEntry[]) : [];
  }

  /**
   * Archive a memory by key.
   *
   * @returns `true` if the memory was found and archived.
   */
  async forget(key: string, options: ForgetOptions = {}): Promise<boolean> {
    const result = await this._tool("brain_forget", {
      key,
      agent_id: options.agentId ?? "",
    });
    return isRecord(result) ? Boolean(result["forgotten"]) : false;
  }

  /**
   * Record a successful task outcome for future reinforcement.
   *
   * @returns The generated memory key.
   */
  async learnSuccess(
    taskDescription: string,
    options: LearnSuccessOptions = {},
  ): Promise<string> {
    const result = await this._tool("brain_learn_success", {
      task_description: taskDescription,
      task_id: options.taskId ?? "",
      agent_id: options.agentId ?? "",
    });
    return isRecord(result) && typeof result["key"] === "string"
      ? result["key"]
      : String(result);
  }

  /**
   * Record a failed task outcome for future avoidance.
   *
   * @returns The generated memory key.
   */
  async learnFailure(
    description: string,
    options: LearnFailureOptions = {},
  ): Promise<string> {
    const result = await this._tool("brain_learn_failure", {
      description,
      task_id: options.taskId ?? "",
      error: options.error ?? "",
      agent_id: options.agentId ?? "",
    });
    return isRecord(result) && typeof result["key"] === "string"
      ? result["key"]
      : String(result);
  }

  // --- Low-level memory_* API ---

  /** Save a raw memory entry by key. */
  async memorySave(
    key: string,
    value: string,
    options: MemorySaveOptions = {},
  ): Promise<Record<string, unknown>> {
    return (await this._tool("memory_save", {
      key,
      value,
      ...(options.tier ? { tier: options.tier } : {}),
      ...(options.source ? { source: options.source } : {}),
      ...(options.tags ? { tags: options.tags } : {}),
      ...(options.confidence !== undefined ? { confidence: options.confidence } : {}),
      ...(options.agentScope ? { agent_scope: options.agentScope } : {}),
      ...(options.memoryGroup ? { memory_group: options.memoryGroup } : {}),
      ...(options.agentId ? { agent_id: options.agentId } : {}),
    })) as Record<string, unknown>;
  }

  /** Retrieve a memory entry by key. */
  async memoryGet(key: string): Promise<Record<string, unknown>> {
    return (await this._tool("memory_get", { key })) as Record<string, unknown>;
  }

  /** Full-text search over the memory store. */
  async memorySearch(
    query: string,
    options: MemorySearchOptions = {},
  ): Promise<MemoryEntry[]> {
    const result = await this._tool("memory_search", {
      query,
      ...(options.tier ? { tier: options.tier } : {}),
      ...(options.limit !== undefined ? { limit: options.limit } : {}),
      ...(options.agentId ? { agent_id: options.agentId } : {}),
    });
    return Array.isArray(result) ? (result as MemoryEntry[]) : [];
  }

  /** Auto-recall relevant memories for a message. */
  async memoryRecall(
    message: string,
    options: MemoryRecallOptions = {},
  ): Promise<Record<string, unknown>> {
    return (await this._tool("memory_recall", {
      message,
      ...(options.limit !== undefined ? { limit: options.limit } : {}),
      ...(options.agentId ? { agent_id: options.agentId } : {}),
    })) as Record<string, unknown>;
  }

  /** Reinforce a memory entry, boosting confidence and resetting decay. */
  async memoryReinforce(
    key: string,
    options: MemoryReinforceOptions = {},
  ): Promise<Record<string, unknown>> {
    return (await this._tool("memory_reinforce", {
      key,
      confidence_boost: options.confidenceBoost ?? 0.0,
      ...(options.agentId ? { agent_id: options.agentId } : {}),
    })) as Record<string, unknown>;
  }

  /** Bulk save multiple memory entries. */
  async memorySaveMany(
    entries: Array<{ key: string; value: string } & Record<string, unknown>>,
    options: MemorySaveManyOptions = {},
  ): Promise<Record<string, unknown>> {
    return (await this._tool("memory_save_many", {
      entries,
      ...(options.agentId ? { agent_id: options.agentId } : {}),
    })) as Record<string, unknown>;
  }

  /** Bulk recall across multiple queries. */
  async memoryRecallMany(
    queries: string[],
    agentId = "",
  ): Promise<Record<string, unknown>> {
    return (await this._tool("memory_recall_many", {
      queries,
      agent_id: agentId,
    })) as Record<string, unknown>;
  }

  /** Return the current agent/brain status. */
  async status(agentId = ""): Promise<Record<string, unknown>> {
    return (await this._tool("brain_status", {
      agent_id: agentId,
    })) as Record<string, unknown>;
  }

  /** Return the brain health report. */
  async health(): Promise<Record<string, unknown>> {
    return (await this._tool("tapps_brain_health", {})) as Record<string, unknown>;
  }

  // --- Lifecycle ---

  /**
   * No-op — kept for API symmetry with the Python client.
   * Network connections in Node.js are per-request; there is no persistent
   * connection pool to close for the fetch-based transport.
   */
  close(): void {
    this._closed = true;
  }

  /** Async disposer for `await using` syntax (TC39 stage 3). */
  async [Symbol.asyncDispose](): Promise<void> {
    this.close();
  }
}

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function isRecord(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}
