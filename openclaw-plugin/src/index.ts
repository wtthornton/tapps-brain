/**
 * tapps-brain OpenClaw ContextEngine Plugin
 *
 * Integrates tapps-brain persistent memory as the ContextEngine for OpenClaw.
 * Uses the official ContextEngine API (v2026.3.7): ingest / assemble / compact.
 *
 * @module tapps-brain-memory
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ---------------------------------------------------------------------------
// OpenClaw SDK — inlined / lazy-resolved to avoid top-level require crash.
//
// The gateway loads plugins via require(). If "openclaw" isn't in the
// plugin's node_modules (it's a peerDep), a top-level require crashes
// before register() is ever called. We inline the trivial helpers and
// lazy-resolve the rest so the module always loads cleanly.
// ---------------------------------------------------------------------------
import type {
  OpenClawPluginApi,
  PluginLogger,
  AnyAgentTool,
  AgentToolResult,
  ContextEngine,
  ContextEngineInfo,
  AgentMessage,
  AssembleResult,
  BootstrapResult,
  CompactResult,
  IngestResult,
} from "openclaw/plugin-sdk/core";

/**
 * Inlined from openclaw/plugin-sdk/plugin-entry — pure function, no deps.
 * Original: returns { id, name, description, kind?, configSchema, register }.
 */
interface PluginEntryOptions {
  id: string;
  name: string;
  description: string;
  kind?: "memory" | "context-engine";
  configSchema?: Record<string, unknown>;
  register: (api: OpenClawPluginApi) => void;
}

function definePluginEntry(opts: PluginEntryOptions) {
  return {
    id: opts.id,
    name: opts.name,
    description: opts.description,
    ...(opts.kind ? { kind: opts.kind } : {}),
    configSchema: opts.configSchema ?? {},
    register: opts.register,
  };
}

/**
 * Lazy-resolve delegateCompactionToRuntime from openclaw at call time.
 * Falls back gracefully if openclaw isn't on the module path.
 */
async function delegateCompactionToRuntime(
  params: Parameters<ContextEngine["compact"]>[0],
): Promise<CompactResult> {
  try {
    const core = await import("openclaw/plugin-sdk/core");
    return core.delegateCompactionToRuntime(params);
  } catch {
    // openclaw not resolvable — tell the runtime we didn't compact
    return { ok: true, compacted: false, reason: "openclaw_sdk_unavailable" };
  }
}

import { McpClient, hasMemoryMd, isFirstRun } from "./mcp_client.js";

// ---------------------------------------------------------------------------
// Internal types — local aliases used by the engine and tools
// ---------------------------------------------------------------------------

/** Message type alias for internal use. */
type Message = AgentMessage;

/** Plugin configuration from openclaw.plugin.json configSchema. */
export interface PluginConfig {
  mcpCommand?: string;
  profilePath?: string;
  tokenBudget?: number;
  captureRateLimit?: number;
  agentId?: string;
  hiveEnabled?: boolean;
  /** Controls citation footers in assemble() output. Defaults to "auto". */
  citations?: "auto" | "on" | "off";
}

/**
 * A single result returned from searchWithSessionMemory().
 *
 * `source` distinguishes long-term memory entries (`"memory"`) from
 * session-index chunks (`"session"`).
 */
export interface SearchResult {
  key: string;
  value: string;
  tier?: string;
  confidence?: number;
  source: "memory" | "session";
}

/** Parameters accepted by searchWithSessionMemory(). */
export interface SearchParams {
  query: string;
  /**
   * Controls which stores are queried:
   * - `"memory"` (default): long-term memory only (`memory_recall`)
   * - `"session"`: session index only (`memory_search_sessions`)
   * - `"all"`: both stores merged
   */
  scope?: "memory" | "session" | "all";
  /** Maximum results to return per store. Defaults to 10. */
  limit?: number;
}

/** Re-export PluginLogger for test compatibility. */
export type { PluginLogger };

// Version compatibility layers and tool group filtering were removed in
// EPIC-037/038. We target OpenClaw >= 2026.3.7 exclusively and use the
// ContextEngine API directly. definePluginEntry is inlined (see above)
// to avoid the top-level require crash on peer-dep resolution failure.

// ---------------------------------------------------------------------------
// TappsBrainEngine — the ContextEngine implementation
// ---------------------------------------------------------------------------

export class TappsBrainEngine {
  private mcpClient: McpClient;
  private injectedKeys = new Set<string>();
  private ingestCount = 0;
  private recentMessages: string[] = [];

  /**
   * Resolves when bootstrap() completes successfully.
   * Rejects if bootstrap() fails.
   * All hooks await this before calling MCP to prevent race conditions.
   */
  private ready: Promise<void>;
  private readyResolve!: () => void;
  private readyReject!: (err: Error) => void;

  private readonly config: PluginConfig;
  private readonly workspaceDir: string;
  private readonly captureRateLimit: number;
  private readonly tokenBudget: number;
  private readonly hiveEnabled: boolean;
  private readonly agentId: string;
  private readonly citations: "auto" | "on" | "off";
  private readonly logger: PluginLogger;

  readonly info: ContextEngineInfo = {
    id: "tapps-brain-memory",
    name: "tapps-brain — Persistent Memory",
    version: "1.4.2",
    ownsCompaction: false,
  };

  constructor(config: PluginConfig, workspaceDir: string, logger?: PluginLogger) {
    this.config = config;
    this.workspaceDir = workspaceDir;
    this.captureRateLimit = config.captureRateLimit ?? 3;
    this.tokenBudget = (config.tokenBudget ?? 3000) * 4; // tokens → chars
    this.hiveEnabled = config.hiveEnabled ?? false;
    this.agentId = config.agentId ?? "";
    this.citations = config.citations ?? "auto";
    this.mcpClient = new McpClient(workspaceDir);
    // Default to no-op logger if none provided (e.g. in tests)
    this.logger = logger ?? { info: () => {}, warn: () => {} };

    // Deferred promise: resolved by bootstrap(), rejected on bootstrap failure.
    // All hooks await this.ready so they never call MCP before the client is ready.
    this.ready = new Promise<void>((resolve, reject) => {
      this.readyResolve = resolve;
      this.readyReject = reject;
    });
  }

  /**
   * bootstrap — called once at session start (optional hook).
   *
   * Spawns tapps-brain-mcp, imports MEMORY.md on first run,
   * and registers the agent in the Hive if enabled.
   *
   * Resolves `this.ready` on success so that concurrent hook calls
   * (ingest/assemble/compact) can proceed. Rejects `this.ready` on
   * failure so hooks return graceful fallbacks instead of hanging.
   */
  async bootstrap(
    _params?: Parameters<NonNullable<ContextEngine["bootstrap"]>>[0],
  ): Promise<{ bootstrapped: boolean }> {
    try {
      const extraArgs: string[] = [];
      if (this.agentId) {
        extraArgs.push("--agent-id", this.agentId);
      }
      if (this.hiveEnabled) {
        extraArgs.push("--enable-hive");
      }

      await this.mcpClient.start(
        this.config.mcpCommand ?? "tapps-brain-mcp",
        extraArgs,
      );

      // First-run: import MEMORY.md if it exists and store is fresh
      if (isFirstRun(this.workspaceDir) && hasMemoryMd(this.workspaceDir)) {
        const memoryMdPath = resolve(this.workspaceDir, "MEMORY.md");
        const content = readFileSync(memoryMdPath, "utf-8");
        const memories = parseMemoryMdForImport(content);
        if (memories.length > 0) {
          await this.mcpClient.callTool("memory_import", {
            memories_json: JSON.stringify({ memories }),
            overwrite: false,
          });
        }
      }

      // Register agent in the Hive
      if (this.hiveEnabled && this.agentId) {
        try {
          await this.mcpClient.callTool("agent_register", {
            agent_id: this.agentId,
            profile: this.config.profilePath ?? "default",
          });
        } catch {
          // Non-fatal — agent may already be registered
        }
      }

      // Signal ready — all queued hooks can now proceed
      this.readyResolve();
      return { bootstrapped: true };
    } catch (err) {
      // Signal failure — queued hooks will take graceful fallback paths
      const error = err instanceof Error ? err : new Error(String(err));
      this.readyReject(error);
      throw err;
    }
  }

  /**
   * ingest — called when a new message enters the context window.
   *
   * Captures durable facts from the message text. Rate-limited:
   * captures once every `captureRateLimit` calls.
   *
   * Awaits `this.ready` to ensure the MCP client is initialised before
   * any tool calls. Returns gracefully if bootstrap failed.
   */
  async ingest({
    message,
    isHeartbeat,
  }: Parameters<ContextEngine["ingest"]>[0]): Promise<IngestResult> {
    // Wait for bootstrap — graceful fallback if it failed
    try {
      await this.ready;
    } catch {
      return { ingested: true };
    }

    const contentText = normalizeContent(message.content);
    if (isHeartbeat || !contentText.trim()) {
      return { ingested: true };
    }

    // Track recent messages for compact() flush
    this.recentMessages.push(contentText);
    if (this.recentMessages.length > 20) {
      this.recentMessages.shift();
    }

    this.ingestCount++;

    // Rate limit: only capture every N calls
    if (
      this.captureRateLimit > 0 &&
      this.ingestCount % this.captureRateLimit !== 0
    ) {
      return { ingested: true };
    }

    const t0 = Date.now();
    try {
      await this.mcpClient.callTool("memory_capture", {
        response: contentText,
        source: message.role === "user" ? "human" : "agent",
        agent_scope: this.hiveEnabled ? "hive" : "private",
      });
      this.logger.info(`[tapps-brain] ingest: ${Date.now() - t0}ms`);
    } catch (err) {
      // Fail gracefully — never block the conversation
      this.logger.warn("[tapps-brain] ingest:", err);
    }

    return { ingested: true };
  }

  /**
   * assemble — called before the agent responds.
   *
   * Recalls relevant memories and returns them as a
   * systemPromptAddition. Messages are passed through unchanged
   * since we don't own compaction.
   *
   * Awaits `this.ready` to ensure the MCP client is initialised before
   * any tool calls. Returns empty result if bootstrap failed.
   */
  async assemble({
    messages,
    tokenBudget,
  }: Parameters<ContextEngine["assemble"]>[0]): Promise<AssembleResult> {
    // Wait for bootstrap — graceful fallback if it failed
    try {
      await this.ready;
    } catch {
      return { messages, estimatedTokens: 0 };
    }

    const budget = Math.min(this.tokenBudget, ((tokenBudget ?? 3000) as number) * 4);

    const t0 = Date.now();
    try {
      // Build a query from recent user messages
      const recentUserMessages = messages
        .filter((m) => m.role === "user")
        .slice(-3)
        .map((m) => normalizeContent(m.content))
        .join(" ");

      const query = recentUserMessages || "session context";

      const recallResult = await this.mcpClient.callTool("memory_recall", {
        message: query,
      });

      const recall = JSON.parse(
        typeof recallResult === "string"
          ? recallResult
          : JSON.stringify(recallResult),
      ) as {
        memories?: Array<{
          key: string;
          value: string;
          tier?: string;
          confidence?: number;
        }>;
      };

      const memories = recall.memories ?? [];
      if (memories.length === 0) {
        this.logger.info(`[tapps-brain] assemble: 0 memories, ${Date.now() - t0}ms`);
        return { messages, estimatedTokens: 0 };
      }

      // Filter out keys already injected in this session
      const newMemories = memories.filter((m) => !this.injectedKeys.has(m.key));
      if (newMemories.length === 0) {
        this.logger.info(`[tapps-brain] assemble: 0 memories, ${Date.now() - t0}ms`);
        return { messages, estimatedTokens: 0 };
      }

      // Build markdown section within token budget
      const lines: string[] = [];
      let charCount = 0;

      const header = "## Relevant Memories\n";
      charCount += header.length;
      lines.push(header);

      const citationsEnabled = this.citations !== "off";

      for (const mem of newMemories) {
        const citation = citationsEnabled
          ? `  *Source: memory/${mem.tier ?? "procedural"}/${mem.key}.md*`
          : "";
        const entry = citation
          ? `- **${mem.key}**: ${mem.value}\n${citation}\n`
          : `- **${mem.key}**: ${mem.value}\n`;
        if (charCount + entry.length > budget) {
          break;
        }
        lines.push(entry);
        charCount += entry.length;
        this.injectedKeys.add(mem.key);
      }

      const systemPromptAddition = lines.length > 1 ? lines.join("") : undefined;
      const estimatedTokens = systemPromptAddition
        ? Math.ceil(charCount / 4)
        : 0;

      this.logger.info(`[tapps-brain] assemble: ${newMemories.length} memories, ${Date.now() - t0}ms`);
      return { messages, estimatedTokens, systemPromptAddition };
    } catch (err) {
      // Fail gracefully — return messages unchanged
      this.logger.warn("[tapps-brain] assemble:", err);
      return { messages, estimatedTokens: 0 };
    }
  }

  /**
   * compact — called when OpenClaw compresses the context window.
   *
   * Since ownsCompaction is false, we flush recent conversation context
   * into tapps-brain (so it's not lost), then delegate actual compaction
   * to the OpenClaw runtime via delegateCompactionToRuntime().
   *
   * Awaits `this.ready` to ensure the MCP client is initialised before
   * any tool calls.
   */
  async compact(
    params: Parameters<ContextEngine["compact"]>[0],
  ): Promise<CompactResult> {
    // Wait for bootstrap — graceful fallback if it failed
    try {
      await this.ready;
    } catch {
      return { ok: true, compacted: false, reason: "bootstrap_failed" };
    }

    // Flush recent messages to tapps-brain before compaction discards them
    if (this.recentMessages.length > 0) {
      try {
        const context = this.recentMessages.join("\n\n");

        // Extract and persist durable facts
        await this.mcpClient.callTool("memory_ingest", {
          context,
          source: "compaction",
          agent_scope: this.hiveEnabled ? "hive" : "private",
        });

        // Index the session chunks
        if (params.sessionId) {
          await this.mcpClient.callTool("memory_index_session", {
            session_id: params.sessionId,
            chunks: this.recentMessages,
          });
        }

        // Clear — these messages are now persisted
        this.recentMessages = [];
      } catch (err) {
        // Fail gracefully — never block compaction
        this.logger.warn(`[tapps-brain] compact flush: ${String(err)}`);
      }
    }

    // Delegate actual compaction to the OpenClaw runtime
    return delegateCompactionToRuntime(params);
  }

  /**
   * searchWithSessionMemory — hybrid search across memory and session index.
   *
   * Used by the EPIC-026 `memory_search` tool replacement so that queries
   * with session scope also surface indexed conversation chunks alongside
   * long-term memory entries.
   *
   * - scope `"memory"` (default): queries `memory_recall` only.
   * - scope `"session"`: queries `memory_search_sessions` only.
   * - scope `"all"`: queries both and merges results. Session results are
   *   appended after memory results and are identifiable via `source: "session"`.
   *
   * Awaits `this.ready` to ensure the MCP client is initialised. Returns an
   * empty array if bootstrap failed.
   */
  async searchWithSessionMemory(params: SearchParams): Promise<SearchResult[]> {
    const { query, scope = "memory", limit = 10 } = params;

    // Wait for bootstrap — graceful fallback if it failed
    try {
      await this.ready;
    } catch {
      return [];
    }

    const results: SearchResult[] = [];

    // -----------------------------------------------------------------------
    // Long-term memory results (scope "memory" or "all")
    // -----------------------------------------------------------------------
    if (scope === "memory" || scope === "all") {
      try {
        const raw = await this.mcpClient.callTool("memory_recall", {
          message: query,
          limit,
        });
        const parsed = JSON.parse(
          typeof raw === "string" ? raw : JSON.stringify(raw),
        ) as {
          memories?: Array<{
            key: string;
            value: string;
            tier?: string;
            confidence?: number;
          }>;
        };
        for (const m of parsed.memories ?? []) {
          results.push({ ...m, source: "memory" });
        }
      } catch (err) {
        this.logger.warn("[tapps-brain] searchWithSessionMemory (memory):", err);
      }
    }

    // -----------------------------------------------------------------------
    // Session index results (scope "session" or "all")
    // -----------------------------------------------------------------------
    if (scope === "session" || scope === "all") {
      try {
        const raw = await this.mcpClient.callTool("memory_search_sessions", {
          query,
          limit,
        });
        const parsed = JSON.parse(
          typeof raw === "string" ? raw : JSON.stringify(raw),
        ) as {
          sessions?: Array<{
            session_id?: string;
            chunk?: string;
            score?: number;
          }>;
        };
        for (const s of parsed.sessions ?? []) {
          results.push({
            key: s.session_id ?? "session",
            value: s.chunk ?? "",
            confidence: s.score,
            source: "session",
          });
        }
      } catch (err) {
        this.logger.warn("[tapps-brain] searchWithSessionMemory (session):", err);
      }
    }

    return results;
  }

  /**
   * callMcpTool — proxy an arbitrary MCP tool call through the ready-gate.
   *
   * Awaits `this.ready` so callers never reach the MCP process before
   * bootstrap completes. Returns `null` if bootstrap failed.
   *
   * Exported for use by `registerMemorySlotTools` and future tool handlers
   * that need direct MCP access without going through higher-level engine
   * methods.
   */
  async callMcpTool(
    name: string,
    args: Record<string, unknown>,
  ): Promise<unknown> {
    try {
      await this.ready;
    } catch {
      return null;
    }
    return this.mcpClient.callTool(name, args);
  }

  /**
   * callMcpResource — read an MCP resource URI through the ready-gate.
   *
   * Uses the `resources/read` MCP JSON-RPC method. Awaits bootstrap before
   * sending. Returns `null` if bootstrap failed.
   *
   * @param uri - The resource URI (e.g. "memory://stats", "memory://entries/key").
   */
  async callMcpResource(uri: string): Promise<unknown> {
    try {
      await this.ready;
    } catch {
      return null;
    }
    return this.mcpClient.readResource(uri);
  }

  /**
   * callMcpPrompt — invoke an MCP prompt through the ready-gate.
   *
   * Uses the `prompts/get` MCP JSON-RPC method. Awaits bootstrap before
   * sending. Returns `null` if bootstrap failed.
   *
   * @param name - The prompt name (e.g. "recall", "store_summary", "remember").
   * @param args - Named string arguments for the prompt template.
   */
  async callMcpPrompt(
    name: string,
    args: Record<string, string>,
  ): Promise<unknown> {
    try {
      await this.ready;
    } catch {
      return null;
    }
    return this.mcpClient.callPrompt(name, args);
  }

  /**
   * dispose — called on gateway shutdown.
   * Stops the MCP child process.
   */
  async dispose(): Promise<void> {
    this.mcpClient.stop();
  }
}

// ---------------------------------------------------------------------------
// Tool helpers
// ---------------------------------------------------------------------------

/** Format a tool result as AgentToolResult content. */
function toolResult(data: unknown): AgentToolResult {
  const text = typeof data === "string" ? data : JSON.stringify(data);
  return { content: [{ type: "text", text }] };
}

/**
 * Create an AnyAgentTool that proxies an MCP tool call through the engine.
 * This DRY helper eliminates the repeated proxy pattern across all 54 tools.
 */
function createMcpProxyTool(
  engine: TappsBrainEngine,
  name: string,
  description: string,
  parameters: Record<string, unknown>,
  /** Optional arg mapper — extracts/transforms args before passing to MCP. */
  mapArgs?: (params: Record<string, unknown>) => Record<string, unknown>,
): AnyAgentTool {
  return {
    name,
    description,
    parameters,
    async execute(_toolCallId: string, params: Record<string, unknown>) {
      const args = mapArgs ? mapArgs(params) : params;
      const raw = await engine.callMcpTool(name, args);
      if (raw === null) {
        return toolResult({ error: "unavailable", message: "tapps-brain MCP not ready" });
      }
      try {
        const parsed = JSON.parse(typeof raw === "string" ? raw : JSON.stringify(raw));
        return toolResult(parsed);
      } catch {
        return toolResult({ error: "parse_error" });
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Memory slot tools — memory_search and memory_get backed by tapps-brain MCP
// ---------------------------------------------------------------------------

function registerMemorySlotTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  // memory_search — full-text search over tapps-brain persistent store
  api.registerTool({
    name: "memory_search",
    description:
      "Search tapps-brain persistent memory using full-text search (BM25 ranking). " +
      "Returns matching entries in OpenClaw snippet format.",
    parameters: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query text" },
        tier: {
          type: "string",
          enum: ["architectural", "pattern", "procedural", "context"],
          description: "Optional tier filter",
        },
        limit: {
          type: "number",
          description: "Maximum number of results to return (default: 10)",
        },
      },
      required: ["query"],
    },
    async execute(_toolCallId: string, params: Record<string, unknown>) {
      const query = params.query as string;
      const tier = params.tier as string | undefined;
      const limit = (params.limit as number | undefined) ?? 10;

      const raw = await engine.callMcpTool("memory_search", {
        query,
        ...(tier !== undefined ? { tier } : {}),
      });

      if (raw === null) {
        return toolResult({ snippets: [] });
      }

      try {
        const entries = JSON.parse(
          typeof raw === "string" ? raw : JSON.stringify(raw),
        ) as Array<{ key: string; value: string; tier?: string; confidence?: number }>;

        const snippets = entries.slice(0, limit).map((e) => ({
          text: e.value,
          path: `memory/${e.tier ?? "context"}/${e.key}.md`,
          score: e.confidence ?? 0,
        }));

        return toolResult({ snippets });
      } catch {
        return toolResult({ snippets: [] });
      }
    },
  }, { names: ["memory_search"] });

  // memory_get — retrieve a single entry by key or path
  api.registerTool({
    name: "memory_get",
    description:
      "Retrieve a single tapps-brain memory entry by key or path. " +
      "Returns the entry value as Markdown text.",
    parameters: {
      type: "object",
      properties: {
        key: {
          type: "string",
          description: "Memory key or path (e.g. 'my-key' or 'memory/tier/my-key.md')",
        },
      },
      required: ["key"],
    },
    async execute(_toolCallId: string, params: Record<string, unknown>) {
      let key = (params.key as string) ?? "";
      if (key.includes("/")) {
        key = key.replace(/\.md$/, "").split("/").pop() ?? key;
      }

      const raw = await engine.callMcpTool("memory_get", { key });
      if (raw === null) {
        return toolResult("");
      }

      try {
        const entry = JSON.parse(
          typeof raw === "string" ? raw : JSON.stringify(raw),
        ) as { value?: string; error?: string };
        return toolResult(entry.error ? "" : entry.value ?? "");
      } catch {
        return toolResult("");
      }
    },
  }, { names: ["memory_get"] });
}

// ---------------------------------------------------------------------------
// Lifecycle tools — memory_reinforce, memory_supersede, memory_history,
//                   memory_search_sessions
// ---------------------------------------------------------------------------

function registerLifecycleTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  api.registerTool(createMcpProxyTool(engine, "memory_reinforce",
    "Reinforce a tapps-brain memory entry, boosting its confidence and resetting decay.",
    { type: "object", properties: {
      key: { type: "string", description: "The memory entry key to reinforce" },
      confidence_boost: { type: "number", description: "Confidence increase [0.0, 0.2]. Defaults to 0.0." },
    }, required: ["key"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_supersede",
    "Create a new version of a tapps-brain memory, superseding the old one.",
    { type: "object", properties: {
      old_key: { type: "string", description: "Key of the existing entry to supersede" },
      new_value: { type: "string", description: "Value for the replacement entry" },
      key: { type: "string", description: "Optional explicit key for the new entry" },
      tier: { type: "string", enum: ["architectural", "pattern", "procedural", "context"], description: "Optional tier override" },
      tags: { type: "array", items: { type: "string" }, description: "Optional tags override" },
    }, required: ["old_key", "new_value"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_history",
    "Show the full version chain for a tapps-brain memory key.",
    { type: "object", properties: {
      key: { type: "string", description: "Any key in the version chain" },
    }, required: ["key"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_search_sessions",
    "Search past session summaries indexed by tapps-brain.",
    { type: "object", properties: {
      query: { type: "string", description: "Search query text" },
      limit: { type: "number", description: "Maximum results (default: 10)" },
    }, required: ["query"] },
  ));
}

// ---------------------------------------------------------------------------
// Hive tools — hive_status, hive_search, hive_propagate, agent_*
// ---------------------------------------------------------------------------

function registerHiveTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  api.registerTool(createMcpProxyTool(engine, "hive_status",
    "Return Hive status: namespaces, entry counts, and registered agents.",
    { type: "object", properties: {} },
  ));

  api.registerTool(createMcpProxyTool(engine, "hive_search",
    "Search the shared Hive for memories contributed by other agents.",
    { type: "object", properties: {
      query: { type: "string", description: "Full-text search query" },
      namespace: { type: "string", description: "Optional namespace filter" },
    }, required: ["query"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "hive_propagate",
    "Manually propagate a local memory entry to the Hive shared store.",
    { type: "object", properties: {
      key: { type: "string", description: "Key of the local memory entry to share" },
      agent_scope: { type: "string", enum: ["domain", "hive"], description: "Propagation scope" },
    }, required: ["key"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "agent_register",
    "Register an agent in the Hive registry.",
    { type: "object", properties: {
      agent_id: { type: "string", description: "Unique agent identifier" },
      profile: { type: "string", description: "Memory profile name" },
      skills: { type: "string", description: "Comma-separated list of skills" },
    }, required: ["agent_id"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "agent_create",
    "Create an agent with profile validation and namespace assignment.",
    { type: "object", properties: {
      agent_id: { type: "string", description: "Unique agent identifier (slug)" },
      profile: { type: "string", description: "Memory profile name" },
      skills: { type: "string", description: "Comma-separated list of skills" },
    }, required: ["agent_id"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "agent_list",
    "List all agents registered in the Hive registry.",
    { type: "object", properties: {} },
  ));

  api.registerTool(createMcpProxyTool(engine, "agent_delete",
    "Delete a registered agent from the Hive registry.",
    { type: "object", properties: {
      agent_id: { type: "string", description: "Unique agent identifier to remove" },
    }, required: ["agent_id"] },
  ));
}

// ---------------------------------------------------------------------------
// Knowledge graph tools
// ---------------------------------------------------------------------------

function registerKnowledgeGraphTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  api.registerTool(createMcpProxyTool(engine, "memory_relations",
    "Return all relations for a tapps-brain memory entry (subject, predicate, object triples).",
    { type: "object", properties: {
      key: { type: "string", description: "Memory entry key" },
    }, required: ["key"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_find_related",
    "Find related memory entries via BFS traversal of the knowledge graph.",
    { type: "object", properties: {
      key: { type: "string", description: "Starting entry key" },
      max_hops: { type: "number", description: "Max traversal depth (default 2)" },
    }, required: ["key"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_query_relations",
    "Filter knowledge graph relations by subject, predicate, and/or object.",
    { type: "object", properties: {
      subject: { type: "string", description: "Filter by subject entity" },
      predicate: { type: "string", description: "Filter by predicate type" },
      object_entity: { type: "string", description: "Filter by object entity" },
    } },
  ));
}


// ---------------------------------------------------------------------------
// Audit, tags, and profile tools
// ---------------------------------------------------------------------------

function registerAuditTagsProfileTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  api.registerTool(createMcpProxyTool(engine, "memory_audit",
    "Query the tapps-brain audit trail for memory events.",
    { type: "object", properties: {
      key: { type: "string", description: "Filter by memory entry key" },
      event_type: { type: "string", description: "Filter by event type" },
      since: { type: "string", description: "ISO-8601 lower bound" },
      until: { type: "string", description: "ISO-8601 upper bound" },
      limit: { type: "number", description: "Maximum events (default 50)" },
    } },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_list_tags",
    "List all tags in the memory store with usage counts.",
    { type: "object", properties: {} },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_update_tags",
    "Atomically add/remove tags on a memory entry.",
    { type: "object", properties: {
      key: { type: "string", description: "Memory entry key" },
      add: { type: "array", items: { type: "string" }, description: "Tags to add" },
      remove: { type: "array", items: { type: "string" }, description: "Tags to remove" },
    }, required: ["key"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_entries_by_tag",
    "Return all memory entries carrying a specific tag.",
    { type: "object", properties: {
      tag: { type: "string", description: "Tag to filter by" },
      tier: { type: "string", enum: ["architectural", "pattern", "procedural", "context"], description: "Optional tier filter" },
    }, required: ["tag"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "profile_info",
    "Return the active memory profile configuration.",
    { type: "object", properties: {} },
  ));

  api.registerTool(createMcpProxyTool(engine, "profile_switch",
    "Switch the active memory profile for this session.",
    { type: "object", properties: {
      name: { type: "string", description: "Profile name (e.g. 'personal-assistant')" },
    }, required: ["name"] },
  ));
}

// ---------------------------------------------------------------------------
// Maintenance and config tools
// ---------------------------------------------------------------------------

function registerMaintenanceConfigTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  api.registerTool(createMcpProxyTool(engine, "maintenance_consolidate",
    "Trigger memory consolidation to merge similar entries (deterministic, no LLM).",
    { type: "object", properties: {
      threshold: { type: "number", description: "Similarity threshold (default 0.7)" },
      min_group_size: { type: "number", description: "Min group size (default 3)" },
      force: { type: "boolean", description: "Force consolidation (default true)" },
    } },
  ));

  api.registerTool(createMcpProxyTool(engine, "maintenance_gc",
    "Run garbage collection to archive stale memories.",
    { type: "object", properties: {
      dry_run: { type: "boolean", description: "Preview without archiving (default false)" },
    } },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_gc_config",
    "Return the current garbage collection configuration.",
    { type: "object", properties: {} },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_gc_config_set",
    "Update garbage collection configuration thresholds.",
    { type: "object", properties: {
      floor_retention_days: { type: "number", description: "Min retention days" },
      session_expiry_days: { type: "number", description: "Session index expiry days" },
      contradicted_threshold: { type: "number", description: "Confidence threshold for contradicted entries" },
    } },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_consolidation_config",
    "Return the current auto-consolidation configuration.",
    { type: "object", properties: {} },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_consolidation_config_set",
    "Update auto-consolidation configuration.",
    { type: "object", properties: {
      enabled: { type: "boolean", description: "Enable/disable auto-consolidation" },
      threshold: { type: "number", description: "Similarity threshold (0-1)" },
      min_entries: { type: "number", description: "Min entry count to trigger" },
    } },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_export",
    "Export memory entries as JSON. Optionally filter by tier, scope, or confidence.",
    { type: "object", properties: {
      tier: { type: "string", description: "Filter by tier" },
      scope: { type: "string", description: "Filter by scope: project or global" },
      min_confidence: { type: "number", description: "Min confidence threshold" },
    } },
  ));

  api.registerTool(createMcpProxyTool(engine, "memory_import",
    "Import memory entries from a JSON string.",
    { type: "object", properties: {
      memories_json: { type: "string", description: "JSON string of memory entries" },
      overwrite: { type: "boolean", description: "Overwrite existing keys (default false)" },
    }, required: ["memories_json"] },
  ));
}

// ---------------------------------------------------------------------------
// Federation tools
// ---------------------------------------------------------------------------

function registerFederationTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  api.registerTool(createMcpProxyTool(engine, "federation_status",
    "Show federation hub status: projects and subscriptions.",
    { type: "object", properties: {} },
  ));

  api.registerTool(createMcpProxyTool(engine, "federation_subscribe",
    "Subscribe a project to receive memories from other federated projects.",
    { type: "object", properties: {
      project_id: { type: "string", description: "Project ID to subscribe" },
      sources: { type: "array", items: { type: "string" }, description: "Source project IDs" },
      tag_filter: { type: "array", items: { type: "string" }, description: "Tag filter" },
      min_confidence: { type: "number", description: "Min confidence (default 0.5)" },
    }, required: ["project_id"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "federation_unsubscribe",
    "Remove a project's federation subscription.",
    { type: "object", properties: {
      project_id: { type: "string", description: "Project ID to unsubscribe" },
    }, required: ["project_id"] },
  ));

  api.registerTool(createMcpProxyTool(engine, "federation_publish",
    "Publish shared-scope memories to the federation hub.",
    { type: "object", properties: {
      project_id: { type: "string", description: "This project's federation ID" },
      keys: { type: "array", items: { type: "string" }, description: "Specific keys to publish" },
    }, required: ["project_id"] },
  ));
}

// ---------------------------------------------------------------------------
// Resource and prompt tools
// ---------------------------------------------------------------------------

function registerResourceAndPromptTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  // Resource-backed tools (wrap MCP resources/read)
  for (const [name, desc, uri] of [
    ["memory_stats", "Return store statistics: entry count, tier distribution, capacity.", "memory://stats"],
    ["memory_health", "Return store health: DB status, WAL mode, decay health.", "memory://health"],
    ["memory_metrics", "Return operation metrics: counters and latency histograms.", "memory://metrics"],
  ] as const) {
    api.registerTool({
      name,
      description: desc,
      parameters: { type: "object", properties: {} },
      async execute(_toolCallId: string, _params: Record<string, unknown>) {
        const raw = await engine.callMcpResource(uri);
        if (raw === null) {
          return toolResult({ error: "unavailable", message: "tapps-brain MCP not ready" });
        }
        try {
          const result = raw as { contents?: Array<{ text?: string }> };
          const text = result.contents?.[0]?.text ?? JSON.stringify(raw);
          return toolResult(JSON.parse(text));
        } catch {
          return toolResult(raw);
        }
      },
    });
  }

  // memory_entry_detail — parameterized resource
  api.registerTool({
    name: "memory_entry_detail",
    description: "Return full detail view of a single memory entry by key.",
    parameters: {
      type: "object",
      properties: {
        key: { type: "string", description: "Memory entry key" },
      },
      required: ["key"],
    },
    async execute(_toolCallId: string, params: Record<string, unknown>) {
      const key = (params.key as string) ?? "";
      const raw = await engine.callMcpResource(`memory://entries/${key}`);
      if (raw === null) {
        return toolResult({ error: "unavailable", message: "tapps-brain MCP not ready" });
      }
      try {
        const result = raw as { contents?: Array<{ text?: string }> };
        const text = result.contents?.[0]?.text ?? JSON.stringify(raw);
        return toolResult(JSON.parse(text));
      } catch {
        return toolResult(raw);
      }
    },
  });

  // Prompt-backed tools (wrap MCP prompts/get)
  for (const [name, desc, promptName, paramKey] of [
    ["memory_recall_prompt", "Recall memories about a topic.", "recall", "topic"],
    ["memory_store_summary_prompt", "Generate a store summary.", "store_summary", ""],
    ["memory_remember_prompt", "Guided workflow to save a fact.", "remember", "fact"],
  ] as const) {
    api.registerTool({
      name,
      description: desc,
      parameters: paramKey
        ? { type: "object", properties: { [paramKey]: { type: "string", description: `The ${paramKey}` } }, required: [paramKey] }
        : { type: "object", properties: {} },
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        const args: Record<string, string> = {};
        if (paramKey && params[paramKey]) {
          args[paramKey] = params[paramKey] as string;
        }
        const raw = await engine.callMcpPrompt(promptName, args);
        if (raw === null) {
          return toolResult({ error: "unavailable", message: "tapps-brain MCP not ready" });
        }
        return toolResult(raw);
      },
    });
  }
}

// ---------------------------------------------------------------------------
// Plugin entry — the default export OpenClaw loads
// ---------------------------------------------------------------------------

export default definePluginEntry({
  id: "tapps-brain-memory",
  name: "tapps-brain — Persistent Memory",
  description:
    "Persistent cross-session memory powered by tapps-brain. " +
    "SQLite-backed knowledge store with BM25 ranking, exponential decay, " +
    "automatic consolidation, cross-project federation, and Hive multi-agent sharing.",
  kind: "context-engine",

  register(api) {
    // Resolve workspace directory — requires full config + agent ID
    const agentId =
      (api.pluginConfig?.agentId as string | undefined) ?? api.id ?? "";
    const resolvedDir =
      api.runtime.agent.resolveAgentWorkspaceDir(api.config, agentId) ?? null;
    if (!resolvedDir) {
      api.logger.warn(
        "[tapps-brain] Could not resolve workspace directory from runtime. " +
          "Falling back to process.cwd(). Memory may be stored in the wrong location.",
      );
    }
    const workspaceDir = resolvedDir ?? process.cwd();

    // Plugin-specific config (from plugins.entries.tapps-brain-memory.config)
    const config = (api.pluginConfig ?? {}) as PluginConfig;

    // Create a shared engine instance. Bootstrap runs asynchronously;
    // hooks and tool handlers await readiness via the internal `ready`
    // promise — they never block or fail hard.
    const engine = new TappsBrainEngine(config, workspaceDir, api.logger);
    engine.bootstrap().catch((err: unknown) => {
      api.logger.warn("[tapps-brain] bootstrap failed:", err);
    });

    // Register all tools unconditionally
    registerMemorySlotTools(api, engine);
    registerLifecycleTools(api, engine);
    registerHiveTools(api, engine);
    registerKnowledgeGraphTools(api, engine);
    registerAuditTagsProfileTools(api, engine);
    registerMaintenanceConfigTools(api, engine);
    registerFederationTools(api, engine);
    registerResourceAndPromptTools(api, engine);

    // Register the ContextEngine — OpenClaw >= 2026.3.7 (our peerDep minimum)
    api.registerContextEngine("tapps-brain-memory", () => engine);
  },
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Normalize message.content to a plain string.
 *
 * OpenClaw can pass AgentMessage objects where `content` is either:
 * - a plain string (legacy format)
 * - an array of content blocks: [{type: "text", text: "..."}]
 * - undefined/null
 *
 * This helper handles all three cases safely.
 */
export function normalizeContent(
  content: unknown,
): string {
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .filter((block): block is { type: string; text: string } =>
        block !== null &&
        typeof block === "object" &&
        block.type === "text" &&
        typeof block.text === "string",
      )
      .map((block) => block.text)
      .join("");
  }
  return "";
}

/**
 * Parse a MEMORY.md file into importable memory entries.
 *
 * Heading levels map to tiers:
 * - H1/H2 → architectural
 * - H3 → pattern
 * - H4+ → procedural
 *
 * Exported for testing.
 */
export function parseMemoryMdForImport(
  content: string,
): Array<{ key: string; value: string; tier: string }> {
  const lines = content.split("\n");
  const entries: Array<{ key: string; value: string; tier: string }> = [];
  let currentKey = "";
  let currentTier = "procedural";
  let currentBody: string[] = [];

  const flush = (): void => {
    if (currentKey && currentBody.length > 0) {
      const value = currentBody.join("\n").trim();
      const key = slugify(currentKey);
      // Skip entries where value is empty or the heading produced an empty slug
      // (e.g. a heading consisting entirely of non-alphanumeric characters).
      if (value && key) {
        entries.push({ key, value, tier: currentTier });
      }
    }
    currentBody = [];
  };

  for (const line of lines) {
    const headingMatch = /^(#{1,6})\s+(.+)$/.exec(line);
    if (headingMatch) {
      flush();
      const level = headingMatch[1].length;
      currentKey = headingMatch[2].trim();
      currentTier =
        level <= 2 ? "architectural" : level === 3 ? "pattern" : "procedural";
    } else {
      currentBody.push(line);
    }
  }
  flush();

  return entries;
}

/**
 * Slugify a heading string for use as a memory key.
 */
function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
