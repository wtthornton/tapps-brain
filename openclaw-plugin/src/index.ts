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

import type { OpenClawPluginApi, PluginEntry } from "openclaw/plugin-sdk/core";

import { McpClient, hasMemoryMd, isFirstRun } from "./mcp_client.js";

// ---------------------------------------------------------------------------
// Types — OpenClaw ContextEngine interface (v2026.3.7)
// ---------------------------------------------------------------------------

/** Message in the conversation context. */
interface Message {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  [key: string]: unknown;
}

/** Token budget with soft and hard limits. */
interface TokenBudget {
  soft: number;
  hard: number;
}

/** Parameters passed to ingest(). */
interface IngestParams {
  sessionId: string;
  message: Message;
  isHeartbeat?: boolean;
}

/** Parameters passed to assemble(). */
interface AssembleParams {
  sessionId: string;
  messages: Message[];
  tokenBudget: TokenBudget;
}

/** Result returned from assemble(). */
interface AssembleResult {
  messages: Message[];
  estimatedTokens: number;
  systemPromptAddition?: string;
}

/** Parameters passed to compact(). */
interface CompactParams {
  sessionId: string;
  force?: boolean;
}

/** Engine info descriptor. */
interface ContextEngineInfo {
  id: string;
  name: string;
  version: string;
  ownsCompaction: boolean;
}

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
  /**
   * Tool groups to register as native OpenClaw tools.
   *
   * Use this to restrict which tool groups are exposed to a specific agent.
   * Accepted values: `"all"` (default) or an array of group names:
   *
   * - `"core"`        — memory_search, memory_get (CRUD memory slot tools)
   * - `"lifecycle"`   — memory_reinforce, memory_supersede, memory_history, memory_search_sessions
   * - `"search"`      — memory_stats, memory_health, memory_metrics, memory_entry_detail,
   *                     memory_recall_prompt, memory_store_summary_prompt, memory_remember_prompt
   * - `"admin"`       — audit, tags, profile, maintenance, GC, consolidation config, export, import
   * - `"hive"`        — hive_status, hive_search, hive_propagate, agent_register/create/list/delete
   * - `"federation"`  — federation_status/subscribe/unsubscribe/publish
   * - `"graph"`       — memory_relations, memory_find_related, memory_query_relations
   *
   * @example
   * // Coder agent: recall + capture only
   * toolGroups: ["core", "lifecycle", "search"]
   *
   * @example
   * // Admin agent: full access
   * toolGroups: "all"
   *
   * @default "all"
   */
  toolGroups?: string[] | "all";
}

/**
 * Names of all supported tool groups.
 *
 * Exported so tests and downstream consumers can reference them without
 * hard-coding strings.
 */
export const TOOL_GROUPS = [
  "core",
  "lifecycle",
  "search",
  "admin",
  "hive",
  "federation",
  "graph",
] as const;

/** Union of all valid tool group names. */
export type ToolGroup = (typeof TOOL_GROUPS)[number];

/**
 * Return true if the given tool group should be registered based on the plugin
 * config.  When `toolGroups` is absent or `"all"`, every group is enabled.
 * When it is an array, only listed groups are enabled.
 */
export function isGroupEnabled(config: PluginConfig, group: string): boolean {
  const { toolGroups } = config;
  if (!toolGroups || toolGroups === "all") return true;
  return (toolGroups as string[]).includes(group);
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

/** Logger interface matching OpenClaw's api.logger shape. */
export interface PluginLogger {
  info: (...args: unknown[]) => void;
  warn: (...args: unknown[]) => void;
}

/** Context passed to before_agent_start hooks (v2026.3.1-3.6). */
export interface HookContext {
  sessionId: string;
  messages?: Message[];
  [key: string]: unknown;
}

/** Tool definition shape for registerTool() (all versions). */
export interface ToolDefinition {
  description: string;
  inputSchema?: Record<string, unknown>;
  handler: (args: Record<string, unknown>) => Promise<unknown>;
}

// ---------------------------------------------------------------------------
// Version compatibility layer
//
// OpenClaw ships distinct APIs across versions. This layer detects which
// API surface is available and selects the appropriate registration path.
//
//   v2026.3.7+         → ContextEngine API (ingest/assemble/compact)
//   v2026.3.1-2026.3.6 → hook-only via before_agent_start
//   <v2026.3.1         → tools-only; memory injection is unavailable
// ---------------------------------------------------------------------------

/** Parsed OpenClaw version as a [year, month, day] tuple for ordering. */
export type OpenClawVersionTuple = [year: number, month: number, day: number];

/** Compatibility mode derived from the detected OpenClaw version. */
export type CompatibilityMode = "context-engine" | "hook-only" | "tools-only";

// Version milestones --------------------------------------------------------

/** First version with the full ContextEngine API. */
const V_CONTEXT_ENGINE: OpenClawVersionTuple = [2026, 3, 7];

/** First version with before_agent_start hook support. */
const V_HOOK_ONLY: OpenClawVersionTuple = [2026, 3, 1];

/**
 * Parse an OpenClaw version string (e.g. "2026.3.7") into a comparable
 * tuple. Returns [0, 0, 0] if the string is absent or malformed.
 */
export function parseOpenClawVersion(
  version: string | undefined,
): OpenClawVersionTuple {
  if (!version) return [0, 0, 0];
  const parts = version.split(".").map((s) => parseInt(s, 10));
  const year = Number.isFinite(parts[0]) ? (parts[0] as number) : 0;
  const month = Number.isFinite(parts[1]) ? (parts[1] as number) : 0;
  const day = Number.isFinite(parts[2]) ? (parts[2] as number) : 0;
  return [year, month, day];
}

/**
 * Compare two version tuples lexicographically.
 * Returns a positive number when a > b, negative when a < b, 0 when equal.
 */
export function compareVersionTuples(
  a: OpenClawVersionTuple,
  b: OpenClawVersionTuple,
): number {
  for (let i = 0; i < 3; i++) {
    const diff = (a[i] as number) - (b[i] as number);
    if (diff !== 0) return diff;
  }
  return 0;
}

/**
 * Determine the compatibility mode for the given OpenClaw version string.
 * Logs a warning when falling back from the full ContextEngine mode.
 */
export function getCompatibilityMode(
  version: string | undefined,
  logger: PluginLogger,
): CompatibilityMode {
  const parsed = parseOpenClawVersion(version);
  const label = version ?? "unknown";

  if (compareVersionTuples(parsed, V_CONTEXT_ENGINE) >= 0) {
    return "context-engine";
  }

  if (compareVersionTuples(parsed, V_HOOK_ONLY) >= 0) {
    logger.warn(
      `[tapps-brain] OpenClaw ${label} detected. ` +
        `ContextEngine API requires v2026.3.7+. ` +
        `Falling back to hook-only mode (before_agent_start).`,
    );
    return "hook-only";
  }

  logger.warn(
    `[tapps-brain] OpenClaw ${label} detected (minimum supported: v2026.3.1). ` +
      `Falling back to tools-only mode — memory injection is unavailable. ` +
      `Upgrade to v2026.3.7+ for full ContextEngine support.`,
  );
  return "tools-only";
}

// ---------------------------------------------------------------------------
// definePluginEntry shim
//
// At runtime OpenClaw provides `definePluginEntry` from
// `openclaw/plugin-sdk/core`. For build-time we define a passthrough so
// TypeScript compiles without the runtime dependency.
// ---------------------------------------------------------------------------

let definePluginEntry: (def: PluginEntry) => PluginEntry;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const sdk = require("openclaw/plugin-sdk/core") as { definePluginEntry?: typeof definePluginEntry };
  if (typeof sdk.definePluginEntry === "function") {
    definePluginEntry = sdk.definePluginEntry;
  } else {
    // Module resolved but definePluginEntry is not exported (older OpenClaw).
    // This happens on OpenClaw versions that ship the plugin-sdk module but
    // predate the definePluginEntry API (e.g. 2026.3.13 stable).
    console.warn(
      "[tapps-brain] openclaw/plugin-sdk/core does not export definePluginEntry. " +
        "Using identity shim — the plugin will still work but may lack " +
        "ContextEngine integration. Consider upgrading OpenClaw.",
    );
    definePluginEntry = (def: PluginEntry) => def;
  }
} catch {
  // Fallback: identity function (dev/test without openclaw installed)
  definePluginEntry = (def: PluginEntry) => def;
}

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
    version: "1.3.1",
    ownsCompaction: false,
  };

  constructor(config: PluginConfig, workspaceDir: string, logger?: PluginLogger) {
    this.config = config;
    this.workspaceDir = workspaceDir;
    this.captureRateLimit = config.captureRateLimit ?? 3;
    this.tokenBudget = (config.tokenBudget ?? 2000) * 4; // tokens → chars
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
  async bootstrap(): Promise<void> {
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
  }: IngestParams): Promise<{ ingested: true }> {
    // Wait for bootstrap — graceful fallback if it failed
    try {
      await this.ready;
    } catch {
      return { ingested: true };
    }

    if (isHeartbeat || !message.content?.trim()) {
      return { ingested: true };
    }

    // Track recent messages for compact() flush
    this.recentMessages.push(message.content);
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
        response: message.content,
        source: message.role === "user" ? "human" : "agent",
        agent_scope: this.hiveEnabled ? "hive" : "private",
      });
      this.logger.info("[tapps-brain] ingest:", { elapsed_ms: Date.now() - t0 });
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
  }: AssembleParams): Promise<AssembleResult> {
    // Wait for bootstrap — graceful fallback if it failed
    try {
      await this.ready;
    } catch {
      return { messages, estimatedTokens: 0 };
    }

    const budget = Math.min(this.tokenBudget, (tokenBudget.soft ?? 2000) * 4);

    const t0 = Date.now();
    try {
      // Build a query from recent user messages
      const recentUserMessages = messages
        .filter((m) => m.role === "user")
        .slice(-3)
        .map((m) => m.content)
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
        this.logger.info("[tapps-brain] assemble:", { elapsed_ms: Date.now() - t0, memories: 0 });
        return { messages, estimatedTokens: 0 };
      }

      // Filter out keys already injected in this session
      const newMemories = memories.filter((m) => !this.injectedKeys.has(m.key));
      if (newMemories.length === 0) {
        this.logger.info("[tapps-brain] assemble:", { elapsed_ms: Date.now() - t0, memories: 0 });
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

      this.logger.info("[tapps-brain] assemble:", {
        elapsed_ms: Date.now() - t0,
        memories: newMemories.length,
      });
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
   * Since ownsCompaction is false, OpenClaw handles the actual
   * compaction. We use this signal to flush recent conversation
   * context into tapps-brain before it's discarded.
   *
   * Awaits `this.ready` to ensure the MCP client is initialised before
   * any tool calls. Returns ok if bootstrap failed.
   */
  async compact({
    sessionId,
  }: CompactParams): Promise<{ ok: true; compacted: true }> {
    // Wait for bootstrap — graceful fallback if it failed
    try {
      await this.ready;
    } catch {
      return { ok: true, compacted: true };
    }

    if (this.recentMessages.length === 0) {
      return { ok: true, compacted: true };
    }

    try {
      const context = this.recentMessages.join("\n\n");

      // Extract and persist durable facts
      await this.mcpClient.callTool("memory_ingest", {
        context,
        source: "compaction",
        agent_scope: this.hiveEnabled ? "hive" : "private",
      });

      // Index the session chunks
      if (sessionId) {
        await this.mcpClient.callTool("memory_index_session", {
          session_id: sessionId,
          chunks: this.recentMessages,
        });
      }

      // Clear — these messages are now persisted
      this.recentMessages = [];
    } catch (err) {
      // Fail gracefully — never block compaction
      this.logger.warn("[tapps-brain] compact:", err);
    }

    return { ok: true, compacted: true };
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
  dispose(): void {
    this.mcpClient.stop();
  }
}

// ---------------------------------------------------------------------------
// Memory slot tools — memory_search and memory_get backed by tapps-brain MCP
//
// When `plugins.slots.memory = "tapps-brain-memory"` is configured in
// OpenClaw, these tools replace the built-in memory-core tools so that all
// memory operations route through tapps-brain's SQLite store.
//
// Registered unconditionally (all compatibility modes) if `registerTool` is
// available. Falls back gracefully if the API is absent.
// ---------------------------------------------------------------------------

/**
 * Register `memory_search` and `memory_get` tools backed by tapps-brain MCP.
 * Safe to call in all compatibility modes; no-ops if `registerTool` is absent.
 */
function registerMemorySlotTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  if (!api.registerTool) return;

  // ------------------------------------------------------------------
  // memory_search — full-text search over tapps-brain persistent store
  // ------------------------------------------------------------------
  api.registerTool("memory_search", {
    description:
      "Search tapps-brain persistent memory using full-text search (BM25 ranking). " +
      "Returns matching entries in OpenClaw snippet format. " +
      "Replaces memory-core when plugins.slots.memory = 'tapps-brain-memory'.",
    inputSchema: {
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
    handler: async (args: Record<string, unknown>) => {
      const query = args.query as string;
      const tier = args.tier as string | undefined;
      const limit = (args.limit as number | undefined) ?? 10;

      const raw = await engine.callMcpTool("memory_search", {
        query,
        ...(tier !== undefined ? { tier } : {}),
      });

      if (raw === null) {
        return { snippets: [] };
      }

      try {
        const entries = JSON.parse(
          typeof raw === "string" ? raw : JSON.stringify(raw),
        ) as Array<{
          key: string;
          value: string;
          tier?: string;
          confidence?: number;
        }>;

        const snippets = entries.slice(0, limit).map((e) => ({
          text: e.value,
          path: `memory/${e.tier ?? "context"}/${e.key}.md`,
          score: e.confidence ?? 0,
        }));

        return { snippets };
      } catch {
        return { snippets: [] };
      }
    },
  });

  // ------------------------------------------------------------------
  // memory_get — retrieve a single entry by key or path
  // ------------------------------------------------------------------
  api.registerTool("memory_get", {
    description:
      "Retrieve a single tapps-brain memory entry by key or path. " +
      "Returns the entry value as Markdown text. " +
      "Accepts bare keys ('my-key') or path format ('memory/tier/my-key.md'). " +
      "Returns empty string for missing entries (graceful degradation).",
    inputSchema: {
      type: "object",
      properties: {
        key: {
          type: "string",
          description:
            "Memory key or path (e.g. 'my-key' or 'memory/tier/my-key.md')",
        },
      },
      required: ["key"],
    },
    handler: async (args: Record<string, unknown>) => {
      let key = (args.key as string) ?? "";

      // Extract bare key from path format: "memory/tier/my-key.md" → "my-key"
      if (key.includes("/")) {
        key = key.replace(/\.md$/, "").split("/").pop() ?? key;
      }

      const raw = await engine.callMcpTool("memory_get", { key });

      if (raw === null) {
        return "";
      }

      try {
        const entry = JSON.parse(
          typeof raw === "string" ? raw : JSON.stringify(raw),
        ) as { value?: string; error?: string };

        if (entry.error) {
          return "";
        }
        return entry.value ?? "";
      } catch {
        return "";
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Lifecycle tools — memory_reinforce, memory_supersede, memory_history,
//                   memory_search_sessions
//
// Registered unconditionally (all compatibility modes) if `registerTool` is
// available. Falls back gracefully if the API is absent.
// ---------------------------------------------------------------------------

/**
 * Register lifecycle tools: reinforce, supersede, history, search_sessions.
 * Safe to call in all compatibility modes; no-ops if `registerTool` is absent.
 */
function registerLifecycleTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  if (!api.registerTool) return;

  // ------------------------------------------------------------------
  // memory_reinforce — boost confidence and reset decay
  // ------------------------------------------------------------------
  api.registerTool("memory_reinforce", {
    description:
      "Reinforce a tapps-brain memory entry, boosting its confidence and resetting decay. " +
      "Call this when a memory proved useful during a session to keep it fresh.",
    inputSchema: {
      type: "object",
      properties: {
        key: { type: "string", description: "The memory entry key to reinforce" },
        confidence_boost: {
          type: "number",
          description: "Confidence increase in range [0.0, 0.2]. Defaults to 0.0.",
        },
      },
      required: ["key"],
    },
    handler: async (args: Record<string, unknown>) => {
      const key = args.key as string;
      const confidence_boost = (args.confidence_boost as number | undefined) ?? 0.0;
      const raw = await engine.callMcpTool("memory_reinforce", { key, confidence_boost });
      if (raw === null) {
        return { error: "unavailable", message: "tapps-brain MCP not ready" };
      }
      try {
        return JSON.parse(typeof raw === "string" ? raw : JSON.stringify(raw));
      } catch {
        return { error: "parse_error" };
      }
    },
  });

  // ------------------------------------------------------------------
  // memory_supersede — create a new version, mark old as invalid
  // ------------------------------------------------------------------
  api.registerTool("memory_supersede", {
    description:
      "Create a new version of a tapps-brain memory, superseding the old one. " +
      "The old entry is marked invalid; a new entry is created with valid_at = now.",
    inputSchema: {
      type: "object",
      properties: {
        old_key: { type: "string", description: "Key of the existing entry to supersede" },
        new_value: { type: "string", description: "Value for the replacement entry" },
        key: {
          type: "string",
          description: "Optional explicit key for the new entry (auto-generated if omitted)",
        },
        tier: {
          type: "string",
          enum: ["architectural", "pattern", "procedural", "context"],
          description: "Optional tier override for the new entry",
        },
        tags: {
          type: "array",
          items: { type: "string" },
          description: "Optional tags override for the new entry",
        },
      },
      required: ["old_key", "new_value"],
    },
    handler: async (args: Record<string, unknown>) => {
      const old_key = args.old_key as string;
      const new_value = args.new_value as string;
      const mcpArgs: Record<string, unknown> = { old_key, new_value };
      if (args.key !== undefined) mcpArgs.key = args.key;
      if (args.tier !== undefined) mcpArgs.tier = args.tier;
      if (args.tags !== undefined) mcpArgs.tags = args.tags;

      const raw = await engine.callMcpTool("memory_supersede", mcpArgs);
      if (raw === null) {
        return { error: "unavailable", message: "tapps-brain MCP not ready" };
      }
      try {
        return JSON.parse(typeof raw === "string" ? raw : JSON.stringify(raw));
      } catch {
        return { error: "parse_error" };
      }
    },
  });

  // ------------------------------------------------------------------
  // memory_history — show full version chain for a key
  // ------------------------------------------------------------------
  api.registerTool("memory_history", {
    description:
      "Show the full version chain for a tapps-brain memory key. " +
      "Follows the superseded_by chain to return all versions ordered by valid_at.",
    inputSchema: {
      type: "object",
      properties: {
        key: { type: "string", description: "Any key in the version chain" },
      },
      required: ["key"],
    },
    handler: async (args: Record<string, unknown>) => {
      const key = args.key as string;
      const raw = await engine.callMcpTool("memory_history", { key });
      if (raw === null) {
        return { error: "unavailable", message: "tapps-brain MCP not ready" };
      }
      try {
        return JSON.parse(typeof raw === "string" ? raw : JSON.stringify(raw));
      } catch {
        return { error: "parse_error" };
      }
    },
  });

  // ------------------------------------------------------------------
  // memory_search_sessions — search past session summaries
  // ------------------------------------------------------------------
  api.registerTool("memory_search_sessions", {
    description:
      "Search past session summaries indexed by tapps-brain. " +
      "Returns matching chunks from previously indexed sessions, ranked by relevance.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query text" },
        limit: {
          type: "number",
          description: "Maximum number of results to return (default: 10)",
        },
      },
      required: ["query"],
    },
    handler: async (args: Record<string, unknown>) => {
      const query = args.query as string;
      const limit = (args.limit as number | undefined) ?? 10;
      const raw = await engine.callMcpTool("memory_search_sessions", { query, limit });
      if (raw === null) {
        return { error: "unavailable", message: "tapps-brain MCP not ready" };
      }
      try {
        return JSON.parse(typeof raw === "string" ? raw : JSON.stringify(raw));
      } catch {
        return { error: "parse_error" };
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Hive tools — hive_status, hive_search, hive_propagate, agent_register,
//              agent_create, agent_list, agent_delete
//
// Registered unconditionally (all compatibility modes) if `registerTool` is
// available. Each tool degrades gracefully if the Hive is disabled on the
// MCP server side — the server returns a JSON error object which is passed
// through rather than throwing.
// ---------------------------------------------------------------------------

/**
 * Register all 7 Hive/agent tools as native OpenClaw tools.
 * Safe to call in all compatibility modes; no-ops if `registerTool` is absent.
 */
function registerHiveTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  if (!api.registerTool) return;

  /** Shared helper: proxy MCP call, handle unavailable/parse errors. */
  const proxy = async (
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<unknown> => {
    const raw = await engine.callMcpTool(toolName, args);
    if (raw === null) {
      return { error: "unavailable", message: "tapps-brain MCP not ready" };
    }
    try {
      return JSON.parse(typeof raw === "string" ? raw : JSON.stringify(raw));
    } catch {
      return { error: "parse_error" };
    }
  };

  // ------------------------------------------------------------------
  // hive_status — namespaces, entry counts, registered agents
  // ------------------------------------------------------------------
  api.registerTool("hive_status", {
    description:
      "Return Hive status: namespaces, entry counts, and registered agents. " +
      "Use this to discover what other agents exist, which profiles they use, " +
      "and how many shared memories are in each namespace. " +
      "Returns an error object if the Hive is disabled.",
    inputSchema: {
      type: "object",
      properties: {},
    },
    handler: async (_args: Record<string, unknown>) => {
      return proxy("hive_status", {});
    },
  });

  // ------------------------------------------------------------------
  // hive_search — full-text search over shared Hive memories
  // ------------------------------------------------------------------
  api.registerTool("hive_search", {
    description:
      "Search the shared Hive for memories contributed by other agents. " +
      "The Hive contains memories saved with agent_scope 'domain' or 'hive'. " +
      "Returns an error object if the Hive is disabled.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Full-text search query" },
        namespace: {
          type: "string",
          description:
            "Optional namespace filter (e.g. 'repo-brain' for domain-scoped, 'universal' for hive-scoped)",
        },
      },
      required: ["query"],
    },
    handler: async (args: Record<string, unknown>) => {
      const query = args.query as string;
      const mcpArgs: Record<string, unknown> = { query };
      if (args.namespace !== undefined) mcpArgs.namespace = args.namespace;
      return proxy("hive_search", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // hive_propagate — manually share a local memory to the Hive
  // ------------------------------------------------------------------
  api.registerTool("hive_propagate", {
    description:
      "Manually propagate a local memory entry to the Hive shared store. " +
      "Use this to share an existing local memory with other agents. " +
      "'domain' scope is visible to same-profile agents; 'hive' scope is visible to all. " +
      "Returns an error object if the Hive is disabled.",
    inputSchema: {
      type: "object",
      properties: {
        key: { type: "string", description: "Key of the local memory entry to share" },
        agent_scope: {
          type: "string",
          enum: ["domain", "hive"],
          description: "Propagation scope: 'domain' (same-profile) or 'hive' (all agents). Defaults to 'hive'.",
        },
      },
      required: ["key"],
    },
    handler: async (args: Record<string, unknown>) => {
      const key = args.key as string;
      const mcpArgs: Record<string, unknown> = { key };
      if (args.agent_scope !== undefined) mcpArgs.agent_scope = args.agent_scope;
      return proxy("hive_propagate", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // agent_register — register an agent in the Hive registry
  // ------------------------------------------------------------------
  api.registerTool("agent_register", {
    description:
      "Register an agent in the Hive registry. " +
      "Registration enables domain-scoped memory sharing with same-profile agents.",
    inputSchema: {
      type: "object",
      properties: {
        agent_id: { type: "string", description: "Unique agent identifier" },
        profile: {
          type: "string",
          description: "Memory profile name (determines domain namespace). Defaults to 'repo-brain'.",
        },
        skills: {
          type: "string",
          description: "Comma-separated list of skills (e.g. 'coding,review'). Defaults to ''.",
        },
      },
      required: ["agent_id"],
    },
    handler: async (args: Record<string, unknown>) => {
      const agent_id = args.agent_id as string;
      const mcpArgs: Record<string, unknown> = { agent_id };
      if (args.profile !== undefined) mcpArgs.profile = args.profile;
      if (args.skills !== undefined) mcpArgs.skills = args.skills;
      return proxy("agent_register", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // agent_create — register with profile validation and namespace assignment
  // ------------------------------------------------------------------
  api.registerTool("agent_create", {
    description:
      "Create an agent: register in the Hive with profile validation and namespace assignment. " +
      "Combines agent_register with profile validation — returns an error listing " +
      "available profiles when the profile name is invalid.",
    inputSchema: {
      type: "object",
      properties: {
        agent_id: { type: "string", description: "Unique agent identifier (slug)" },
        profile: {
          type: "string",
          description: "Memory profile name (must be a valid built-in or project profile). Defaults to 'repo-brain'.",
        },
        skills: {
          type: "string",
          description: "Comma-separated list of skills. Defaults to ''.",
        },
      },
      required: ["agent_id"],
    },
    handler: async (args: Record<string, unknown>) => {
      const agent_id = args.agent_id as string;
      const mcpArgs: Record<string, unknown> = { agent_id };
      if (args.profile !== undefined) mcpArgs.profile = args.profile;
      if (args.skills !== undefined) mcpArgs.skills = args.skills;
      return proxy("agent_create", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // agent_list — list all registered agents
  // ------------------------------------------------------------------
  api.registerTool("agent_list", {
    description:
      "List all agents registered in the Hive registry, with their profiles and skills.",
    inputSchema: {
      type: "object",
      properties: {},
    },
    handler: async (_args: Record<string, unknown>) => {
      return proxy("agent_list", {});
    },
  });

  // ------------------------------------------------------------------
  // agent_delete — remove an agent from the Hive registry
  // ------------------------------------------------------------------
  api.registerTool("agent_delete", {
    description:
      "Delete a registered agent from the Hive registry. " +
      "Returns deleted: false (not an error) if the agent was not found.",
    inputSchema: {
      type: "object",
      properties: {
        agent_id: { type: "string", description: "Unique agent identifier to remove" },
      },
      required: ["agent_id"],
    },
    handler: async (args: Record<string, unknown>) => {
      const agent_id = args.agent_id as string;
      return proxy("agent_delete", { agent_id });
    },
  });
}

// ---------------------------------------------------------------------------
// Knowledge graph tools — memory_relations, memory_find_related,
//                         memory_query_relations
//
// Registered unconditionally (all compatibility modes) if `registerTool` is
// available. Falls back gracefully if the API is absent.
// ---------------------------------------------------------------------------

/**
 * Register knowledge graph tools: relations, find_related, query_relations.
 * Safe to call in all compatibility modes; no-ops if `registerTool` is absent.
 */
function registerKnowledgeGraphTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  if (!api.registerTool) return;

  /** Shared helper: proxy MCP call, handle unavailable/parse errors. */
  const proxy = async (
    tool: string,
    args: Record<string, unknown>,
  ): Promise<unknown> => {
    const raw = await engine.callMcpTool(tool, args);
    if (raw === null) {
      return { error: "unavailable", message: "tapps-brain MCP not ready" };
    }
    try {
      return JSON.parse(typeof raw === "string" ? raw : JSON.stringify(raw)) as unknown;
    } catch {
      return { raw };
    }
  };

  // ------------------------------------------------------------------
  // memory_relations — all relations for a memory entry key
  // ------------------------------------------------------------------
  api.registerTool("memory_relations", {
    description:
      "Return all relations associated with a tapps-brain memory entry. " +
      "Relations are triples (subject, predicate, object) linking memory entries " +
      "in the knowledge graph.",
    inputSchema: {
      type: "object",
      properties: {
        key: { type: "string", description: "Memory entry key to look up relations for" },
      },
      required: ["key"],
    },
    handler: async (args: Record<string, unknown>) => {
      const key = args.key as string;
      return proxy("memory_relations", { key });
    },
  });

  // ------------------------------------------------------------------
  // memory_find_related — BFS traversal of the relation graph
  // ------------------------------------------------------------------
  api.registerTool("memory_find_related", {
    description:
      "Find memory entries related to a given key via BFS traversal of the relation graph. " +
      "Returns keys and their hop distance from the starting entry. " +
      "Use max_hops to control traversal depth (default 2).",
    inputSchema: {
      type: "object",
      properties: {
        key: { type: "string", description: "Starting entry key" },
        max_hops: {
          type: "number",
          description: "Maximum traversal depth, must be >= 1 (default 2)",
        },
      },
      required: ["key"],
    },
    handler: async (args: Record<string, unknown>) => {
      const key = args.key as string;
      const mcpArgs: Record<string, unknown> = { key };
      if (args.max_hops !== undefined) mcpArgs.max_hops = args.max_hops;
      return proxy("memory_find_related", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // memory_query_relations — filter relations by subject/predicate/object
  // ------------------------------------------------------------------
  api.registerTool("memory_query_relations", {
    description:
      "Filter tapps-brain knowledge graph relations by subject, predicate, and/or object. " +
      "All filters use case-insensitive matching combined with AND logic. " +
      "Omit any filter field (or pass empty string) to skip that filter.",
    inputSchema: {
      type: "object",
      properties: {
        subject: {
          type: "string",
          description: "Filter by subject entity (optional — omit to skip)",
        },
        predicate: {
          type: "string",
          description: "Filter by predicate/relationship type (optional — omit to skip)",
        },
        object_entity: {
          type: "string",
          description: "Filter by object entity (optional — omit to skip)",
        },
      },
    },
    handler: async (args: Record<string, unknown>) => {
      const mcpArgs: Record<string, unknown> = {};
      if (args.subject !== undefined) mcpArgs.subject = args.subject;
      if (args.predicate !== undefined) mcpArgs.predicate = args.predicate;
      if (args.object_entity !== undefined) mcpArgs.object_entity = args.object_entity;
      return proxy("memory_query_relations", mcpArgs);
    },
  });
}

// ---------------------------------------------------------------------------
// Audit, tags, and profile tools — memory_audit, memory_list_tags,
//   memory_update_tags, memory_entries_by_tag, profile_info, profile_switch
//
// Registered unconditionally (all compatibility modes) if `registerTool` is
// available. Falls back gracefully if the API is absent.
// ---------------------------------------------------------------------------

/**
 * Register audit, tags, and profile tools as native OpenClaw tools.
 * Safe to call in all compatibility modes; no-ops if `registerTool` is absent.
 */
function registerAuditTagsProfileTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  if (!api.registerTool) return;

  /** Shared helper: proxy MCP call, handle unavailable/parse errors. */
  const proxy = async (
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<unknown> => {
    const raw = await engine.callMcpTool(toolName, args);
    if (raw === null) {
      return { error: "unavailable", message: "tapps-brain MCP not ready" };
    }
    try {
      return JSON.parse(typeof raw === "string" ? raw : JSON.stringify(raw));
    } catch {
      return { error: "parse_error" };
    }
  };

  // ------------------------------------------------------------------
  // memory_audit — query the audit trail for memory events
  // ------------------------------------------------------------------
  api.registerTool("memory_audit", {
    description:
      "Query the tapps-brain audit trail for memory events. " +
      "Returns matching events from the append-only JSONL audit log. " +
      "All filters are optional and combined with AND logic.",
    inputSchema: {
      type: "object",
      properties: {
        key: {
          type: "string",
          description: "Filter by memory entry key (optional)",
        },
        event_type: {
          type: "string",
          description: "Filter by event type, e.g. 'save', 'delete' (optional)",
        },
        since: {
          type: "string",
          description: "ISO-8601 lower bound, inclusive (optional)",
        },
        until: {
          type: "string",
          description: "ISO-8601 upper bound, inclusive (optional)",
        },
        limit: {
          type: "number",
          description: "Maximum number of events to return (default 50, must be >= 1)",
        },
      },
    },
    handler: async (args: Record<string, unknown>) => {
      const mcpArgs: Record<string, unknown> = {};
      if (args.key !== undefined) mcpArgs.key = args.key;
      if (args.event_type !== undefined) mcpArgs.event_type = args.event_type;
      if (args.since !== undefined) mcpArgs.since = args.since;
      if (args.until !== undefined) mcpArgs.until = args.until;
      if (args.limit !== undefined) mcpArgs.limit = args.limit;
      return proxy("memory_audit", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // memory_list_tags — list all tags with usage counts
  // ------------------------------------------------------------------
  api.registerTool("memory_list_tags", {
    description:
      "List all tags used in the tapps-brain memory store with their usage counts. " +
      "Returns tags sorted by count descending. Use this to discover what topics are tagged.",
    inputSchema: {
      type: "object",
      properties: {},
    },
    handler: async (_args: Record<string, unknown>) => {
      return proxy("memory_list_tags", {});
    },
  });

  // ------------------------------------------------------------------
  // memory_update_tags — atomically add/remove tags on an entry
  // ------------------------------------------------------------------
  api.registerTool("memory_update_tags", {
    description:
      "Atomically add and/or remove tags on an existing tapps-brain memory entry. " +
      "Tags are deduplicated; the 10-tag maximum is enforced. " +
      "Removing a non-existent tag is a no-op. Adding an already-present tag is a no-op.",
    inputSchema: {
      type: "object",
      properties: {
        key: { type: "string", description: "The memory entry key to update" },
        add: {
          type: "array",
          items: { type: "string" },
          description: "List of tags to add (optional)",
        },
        remove: {
          type: "array",
          items: { type: "string" },
          description: "List of tags to remove (optional)",
        },
      },
      required: ["key"],
    },
    handler: async (args: Record<string, unknown>) => {
      const key = args.key as string;
      const mcpArgs: Record<string, unknown> = { key };
      if (args.add !== undefined) mcpArgs.add = args.add;
      if (args.remove !== undefined) mcpArgs.remove = args.remove;
      return proxy("memory_update_tags", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // memory_entries_by_tag — list all entries carrying a specific tag
  // ------------------------------------------------------------------
  api.registerTool("memory_entries_by_tag", {
    description:
      "Return all tapps-brain memory entries that carry a specific tag. " +
      "Optionally filter by tier. Useful for tag-based retrieval workflows.",
    inputSchema: {
      type: "object",
      properties: {
        tag: { type: "string", description: "The tag to filter by" },
        tier: {
          type: "string",
          enum: ["architectural", "pattern", "procedural", "context"],
          description: "Optional tier filter — omit to return entries across all tiers",
        },
      },
      required: ["tag"],
    },
    handler: async (args: Record<string, unknown>) => {
      const tag = args.tag as string;
      const mcpArgs: Record<string, unknown> = { tag };
      if (args.tier !== undefined) mcpArgs.tier = args.tier;
      return proxy("memory_entries_by_tag", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // profile_info — active profile name, layers, and scoring config
  // ------------------------------------------------------------------
  api.registerTool("profile_info", {
    description:
      "Return the active tapps-brain memory profile: name, description, version, " +
      "tier layers with half-lives and decay models, and scoring weight configuration.",
    inputSchema: {
      type: "object",
      properties: {},
    },
    handler: async (_args: Record<string, unknown>) => {
      return proxy("profile_info", {});
    },
  });

  // ------------------------------------------------------------------
  // profile_switch — switch to a different built-in profile
  // ------------------------------------------------------------------
  api.registerTool("profile_switch", {
    description:
      "Switch the active tapps-brain memory profile for this session. " +
      "Built-in profiles: repo-brain, personal-assistant, customer-support, " +
      "home-automation, project-management, research-knowledge. " +
      "For a permanent change, use the CLI: tapps-brain profile set <name>.",
    inputSchema: {
      type: "object",
      properties: {
        name: {
          type: "string",
          description: "Name of the built-in profile to switch to (e.g. 'personal-assistant')",
        },
      },
      required: ["name"],
    },
    handler: async (args: Record<string, unknown>) => {
      const name = args.name as string;
      return proxy("profile_switch", { name });
    },
  });
}

// ---------------------------------------------------------------------------
// Maintenance, config, export/import tools — maintenance_consolidate,
//   maintenance_gc, memory_gc_config, memory_gc_config_set,
//   memory_consolidation_config, memory_consolidation_config_set,
//   memory_export, memory_import
//
// Registered unconditionally (all compatibility modes) if `registerTool` is
// available. Falls back gracefully if the API is absent.
// ---------------------------------------------------------------------------

/**
 * Register maintenance, config, export, and import tools as native OpenClaw tools.
 * Safe to call in all compatibility modes; no-ops if `registerTool` is absent.
 */
function registerMaintenanceConfigTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  if (!api.registerTool) return;

  /** Shared helper: proxy MCP call, handle unavailable/parse errors. */
  const proxy = async (
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<unknown> => {
    const raw = await engine.callMcpTool(toolName, args);
    if (raw === null) {
      return { error: "unavailable", message: "tapps-brain MCP not ready" };
    }
    try {
      return JSON.parse(typeof raw === "string" ? raw : JSON.stringify(raw));
    } catch {
      return { error: "parse_error" };
    }
  };

  // ------------------------------------------------------------------
  // maintenance_consolidate — merge similar memories
  // ------------------------------------------------------------------
  api.registerTool("maintenance_consolidate", {
    description:
      "Trigger tapps-brain memory consolidation to merge similar entries. " +
      "Entries with Jaccard + TF-IDF similarity above `threshold` are merged " +
      "deterministically (no LLM). Returns consolidated count and merged keys.",
    inputSchema: {
      type: "object",
      properties: {
        threshold: {
          type: "number",
          description: "Similarity threshold for merging (default 0.7, range 0–1)",
        },
        min_group_size: {
          type: "number",
          description: "Minimum number of similar entries to form a consolidation group (default 3)",
        },
        force: {
          type: "boolean",
          description: "Force consolidation even if below auto-trigger threshold (default true)",
        },
      },
    },
    handler: async (args: Record<string, unknown>) => {
      const mcpArgs: Record<string, unknown> = {};
      if (args.threshold !== undefined) mcpArgs.threshold = args.threshold;
      if (args.min_group_size !== undefined) mcpArgs.min_group_size = args.min_group_size;
      if (args.force !== undefined) mcpArgs.force = args.force;
      return proxy("maintenance_consolidate", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // maintenance_gc — archive stale memories
  // ------------------------------------------------------------------
  api.registerTool("maintenance_gc", {
    description:
      "Run tapps-brain garbage collection to archive stale memories. " +
      "Entries that have decayed below the GC floor or exceeded the retention " +
      "window are moved to the archive JSONL (not deleted). " +
      "Use dry_run=true to preview which entries would be archived.",
    inputSchema: {
      type: "object",
      properties: {
        dry_run: {
          type: "boolean",
          description: "If true, preview archived entries without actually archiving (default false)",
        },
      },
    },
    handler: async (args: Record<string, unknown>) => {
      const mcpArgs: Record<string, unknown> = {};
      if (args.dry_run !== undefined) mcpArgs.dry_run = args.dry_run;
      return proxy("maintenance_gc", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // memory_gc_config — return current GC configuration
  // ------------------------------------------------------------------
  api.registerTool("memory_gc_config", {
    description:
      "Return the current tapps-brain garbage collection configuration. " +
      "Shows floor_retention_days, session_expiry_days, and contradicted_threshold.",
    inputSchema: {
      type: "object",
      properties: {},
    },
    handler: async (_args: Record<string, unknown>) => {
      return proxy("memory_gc_config", {});
    },
  });

  // ------------------------------------------------------------------
  // memory_gc_config_set — update GC configuration thresholds
  // ------------------------------------------------------------------
  api.registerTool("memory_gc_config_set", {
    description:
      "Update tapps-brain garbage collection configuration thresholds. " +
      "All fields are optional; omitted fields retain their current values. " +
      "Changes take effect immediately for subsequent GC runs.",
    inputSchema: {
      type: "object",
      properties: {
        floor_retention_days: {
          type: "number",
          description: "Minimum days to retain any memory regardless of decay (must be >= 1)",
        },
        session_expiry_days: {
          type: "number",
          description: "Days after which inactive session indexes are purged (must be >= 1)",
        },
        contradicted_threshold: {
          type: "number",
          description: "Confidence below which contradicted entries are archived (range 0–1)",
        },
      },
    },
    handler: async (args: Record<string, unknown>) => {
      const mcpArgs: Record<string, unknown> = {};
      if (args.floor_retention_days !== undefined)
        mcpArgs.floor_retention_days = args.floor_retention_days;
      if (args.session_expiry_days !== undefined)
        mcpArgs.session_expiry_days = args.session_expiry_days;
      if (args.contradicted_threshold !== undefined)
        mcpArgs.contradicted_threshold = args.contradicted_threshold;
      return proxy("memory_gc_config_set", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // memory_consolidation_config — return current auto-consolidation config
  // ------------------------------------------------------------------
  api.registerTool("memory_consolidation_config", {
    description:
      "Return the current tapps-brain auto-consolidation configuration. " +
      "Shows enabled, threshold (similarity cutoff), and min_entries (trigger count).",
    inputSchema: {
      type: "object",
      properties: {},
    },
    handler: async (_args: Record<string, unknown>) => {
      return proxy("memory_consolidation_config", {});
    },
  });

  // ------------------------------------------------------------------
  // memory_consolidation_config_set — update auto-consolidation config
  // ------------------------------------------------------------------
  api.registerTool("memory_consolidation_config_set", {
    description:
      "Update tapps-brain auto-consolidation configuration. " +
      "All fields are optional; omitted fields retain their current values.",
    inputSchema: {
      type: "object",
      properties: {
        enabled: {
          type: "boolean",
          description: "Enable or disable automatic consolidation",
        },
        threshold: {
          type: "number",
          description: "Similarity threshold for auto-consolidation (range 0–1, default 0.7)",
        },
        min_entries: {
          type: "number",
          description: "Minimum entry count before auto-consolidation is triggered",
        },
      },
    },
    handler: async (args: Record<string, unknown>) => {
      const mcpArgs: Record<string, unknown> = {};
      if (args.enabled !== undefined) mcpArgs.enabled = args.enabled;
      if (args.threshold !== undefined) mcpArgs.threshold = args.threshold;
      if (args.min_entries !== undefined) mcpArgs.min_entries = args.min_entries;
      return proxy("memory_consolidation_config_set", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // memory_export — export entries as JSON
  // ------------------------------------------------------------------
  api.registerTool("memory_export", {
    description:
      "Export tapps-brain memory entries as a JSON string. " +
      "Optionally filter by tier, scope, or minimum confidence. " +
      "The output can be passed to memory_import to restore entries.",
    inputSchema: {
      type: "object",
      properties: {
        tier: {
          type: "string",
          description:
            "Filter by tier: architectural, pattern, procedural, or context (optional)",
        },
        scope: {
          type: "string",
          description: "Filter by scope: project or global (optional)",
        },
        min_confidence: {
          type: "number",
          description: "Minimum confidence threshold for exported entries (optional, range 0–1)",
        },
      },
    },
    handler: async (args: Record<string, unknown>) => {
      const mcpArgs: Record<string, unknown> = {};
      if (args.tier !== undefined) mcpArgs.tier = args.tier;
      if (args.scope !== undefined) mcpArgs.scope = args.scope;
      if (args.min_confidence !== undefined) mcpArgs.min_confidence = args.min_confidence;
      return proxy("memory_export", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // memory_import — import entries from JSON
  // ------------------------------------------------------------------
  api.registerTool("memory_import", {
    description:
      "Import tapps-brain memory entries from a JSON string. " +
      "The JSON should be the output of memory_export (an array of entry objects). " +
      "Set overwrite=true to replace existing entries with matching keys.",
    inputSchema: {
      type: "object",
      properties: {
        memories_json: {
          type: "string",
          description: "JSON string containing an array of memory entry objects to import",
        },
        overwrite: {
          type: "boolean",
          description:
            "If true, overwrite existing entries with matching keys (default false — skip duplicates)",
        },
      },
      required: ["memories_json"],
    },
    handler: async (args: Record<string, unknown>) => {
      const memories_json = args.memories_json as string;
      const mcpArgs: Record<string, unknown> = { memories_json };
      if (args.overwrite !== undefined) mcpArgs.overwrite = args.overwrite;
      return proxy("memory_import", mcpArgs);
    },
  });
}

// ---------------------------------------------------------------------------
// Federation tools — federation_status, federation_subscribe,
//                   federation_unsubscribe, federation_publish
//
// Registered unconditionally (all compatibility modes) if `registerTool` is
// available. Proxy directly to the MCP server; the server handles missing
// federation config gracefully and returns a JSON error object.
// ---------------------------------------------------------------------------

/**
 * Register federation tools: status, subscribe, unsubscribe, publish.
 * Safe to call in all compatibility modes; no-ops if `registerTool` is absent.
 */
function registerFederationTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  if (!api.registerTool) return;

  /** Shared helper: proxy MCP call, handle unavailable/parse errors. */
  const proxy = async (
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<unknown> => {
    const raw = await engine.callMcpTool(toolName, args);
    if (raw === null) {
      return { error: "unavailable", message: "tapps-brain MCP not ready" };
    }
    try {
      return JSON.parse(typeof raw === "string" ? raw : JSON.stringify(raw));
    } catch {
      return { error: "parse_error" };
    }
  };

  // ------------------------------------------------------------------
  // federation_status — hub status, projects, and subscriptions
  // ------------------------------------------------------------------
  api.registerTool("federation_status", {
    description:
      "Show the federation hub status: registered projects and active subscriptions. " +
      "Returns hub statistics, project list, and subscription config. " +
      "Returns an error object if the federation hub is unavailable.",
    inputSchema: {
      type: "object",
      properties: {},
    },
    handler: async (_args: Record<string, unknown>) => {
      return proxy("federation_status", {});
    },
  });

  // ------------------------------------------------------------------
  // federation_subscribe — subscribe a project to receive memories
  // ------------------------------------------------------------------
  api.registerTool("federation_subscribe", {
    description:
      "Subscribe a project to receive memories from other federated projects. " +
      "The project is auto-registered if not already known. " +
      "If sources is omitted, subscribes to all other projects.",
    inputSchema: {
      type: "object",
      properties: {
        project_id: {
          type: "string",
          description: "The project ID to subscribe (e.g. 'my-project')",
        },
        sources: {
          type: "array",
          items: { type: "string" },
          description:
            "Optional list of source project IDs to subscribe to. " +
            "Omit or pass empty list to subscribe to all federated projects.",
        },
        tag_filter: {
          type: "array",
          items: { type: "string" },
          description:
            "Optional tag filter — only import memories carrying these tags.",
        },
        min_confidence: {
          type: "number",
          description:
            "Minimum confidence threshold for imported memories (range 0–1, default 0.5).",
        },
      },
      required: ["project_id"],
    },
    handler: async (args: Record<string, unknown>) => {
      const project_id = args.project_id as string;
      const mcpArgs: Record<string, unknown> = { project_id };
      if (args.sources !== undefined) mcpArgs.sources = args.sources;
      if (args.tag_filter !== undefined) mcpArgs.tag_filter = args.tag_filter;
      if (args.min_confidence !== undefined) mcpArgs.min_confidence = args.min_confidence;
      return proxy("federation_subscribe", mcpArgs);
    },
  });

  // ------------------------------------------------------------------
  // federation_unsubscribe — remove a project subscription
  // ------------------------------------------------------------------
  api.registerTool("federation_unsubscribe", {
    description:
      "Remove a project's federation subscription. " +
      "After unsubscribing, the project will no longer receive memories from the hub. " +
      "Returns subscriptions_removed: 0 (not an error) if no subscription existed.",
    inputSchema: {
      type: "object",
      properties: {
        project_id: {
          type: "string",
          description: "The project ID to unsubscribe",
        },
      },
      required: ["project_id"],
    },
    handler: async (args: Record<string, unknown>) => {
      const project_id = args.project_id as string;
      return proxy("federation_unsubscribe", { project_id });
    },
  });

  // ------------------------------------------------------------------
  // federation_publish — publish shared-scope memories to hub
  // ------------------------------------------------------------------
  api.registerTool("federation_publish", {
    description:
      "Publish shared-scope memories from this project to the federation hub. " +
      "Only entries with scope='shared' are published. " +
      "Pass keys to publish only specific entries; omit to publish all shared entries.",
    inputSchema: {
      type: "object",
      properties: {
        project_id: {
          type: "string",
          description: "This project's federation identifier",
        },
        keys: {
          type: "array",
          items: { type: "string" },
          description:
            "Optional list of specific memory entry keys to publish. " +
            "Omit to publish all entries with scope='shared'.",
        },
      },
      required: ["project_id"],
    },
    handler: async (args: Record<string, unknown>) => {
      const project_id = args.project_id as string;
      const mcpArgs: Record<string, unknown> = { project_id };
      if (args.keys !== undefined) mcpArgs.keys = args.keys;
      return proxy("federation_publish", mcpArgs);
    },
  });
}

// ---------------------------------------------------------------------------
// Resource and prompt tools — memory stats, health, metrics, entry detail,
//                             recall, store_summary, remember
//
// Exposes MCP resources (memory://stats, memory://health, memory://metrics,
// memory://entries/{key}) and MCP prompts (recall, store_summary, remember)
// as native OpenClaw tools so agents can query store state and invoke
// guided workflows without requiring a separate MCP sidecar client.
//
// Resources use the `resources/read` MCP method; prompts use `prompts/get`.
// Registered unconditionally when `registerTool` is available.
// ---------------------------------------------------------------------------

/**
 * Register resource-backed tools (memory_stats, memory_health,
 * memory_metrics, memory_entry_detail) and prompt-backed tools
 * (memory_recall_prompt, memory_store_summary_prompt,
 * memory_remember_prompt).
 *
 * Safe to call in all compatibility modes; no-ops if `registerTool` is absent.
 */
function registerResourceAndPromptTools(
  api: OpenClawPluginApi,
  engine: TappsBrainEngine,
): void {
  if (!api.registerTool) return;

  // ----------------------------------------------------------------
  // Resource tools — wrap MCP resources/read as callable tools
  // ----------------------------------------------------------------

  // memory_stats — memory://stats resource
  api.registerTool("memory_stats", {
    description:
      "Return tapps-brain store statistics: total entry count, tier distribution, " +
      "max capacity (500), and schema version. " +
      "Use this to understand how much memory is in use before saving new entries.",
    inputSchema: { type: "object", properties: {} },
    handler: async (_args: Record<string, unknown>) => {
      const raw = await engine.callMcpResource("memory://stats");
      if (raw === null) {
        return { error: "unavailable", message: "tapps-brain MCP not ready" };
      }
      // resources/read returns { contents: [{ uri, text }] }
      try {
        const result = raw as { contents?: Array<{ text?: string }> };
        const text = result.contents?.[0]?.text ?? JSON.stringify(raw);
        return JSON.parse(text);
      } catch {
        return raw;
      }
    },
  });

  // memory_health — memory://health resource
  api.registerTool("memory_health", {
    description:
      "Return the tapps-brain store health report: database status, WAL mode, " +
      "entry counts, decay health, and consolidation readiness. " +
      "Use this to diagnose memory store issues.",
    inputSchema: { type: "object", properties: {} },
    handler: async (_args: Record<string, unknown>) => {
      const raw = await engine.callMcpResource("memory://health");
      if (raw === null) {
        return { error: "unavailable", message: "tapps-brain MCP not ready" };
      }
      try {
        const result = raw as { contents?: Array<{ text?: string }> };
        const text = result.contents?.[0]?.text ?? JSON.stringify(raw);
        return JSON.parse(text);
      } catch {
        return raw;
      }
    },
  });

  // memory_metrics — memory://metrics resource
  api.registerTool("memory_metrics", {
    description:
      "Return tapps-brain operation metrics: counters and latency histograms " +
      "for save, recall, delete, consolidation, and GC operations. " +
      "Use this for observability and performance monitoring.",
    inputSchema: { type: "object", properties: {} },
    handler: async (_args: Record<string, unknown>) => {
      const raw = await engine.callMcpResource("memory://metrics");
      if (raw === null) {
        return { error: "unavailable", message: "tapps-brain MCP not ready" };
      }
      try {
        const result = raw as { contents?: Array<{ text?: string }> };
        const text = result.contents?.[0]?.text ?? JSON.stringify(raw);
        return JSON.parse(text);
      } catch {
        return raw;
      }
    },
  });

  // memory_entry_detail — memory://entries/{key} resource
  api.registerTool("memory_entry_detail", {
    description:
      "Return the full detail view of a single tapps-brain memory entry by key: " +
      "all fields including tier, confidence, source, tags, timestamps, " +
      "access_count, and decay state. " +
      "Use this for inspection and debugging individual entries.",
    inputSchema: {
      type: "object",
      properties: {
        key: {
          type: "string",
          description: "The memory entry key to retrieve in detail",
        },
      },
      required: ["key"],
    },
    handler: async (args: Record<string, unknown>) => {
      const key = (args.key as string) ?? "";
      const raw = await engine.callMcpResource(`memory://entries/${key}`);
      if (raw === null) {
        return { error: "unavailable", message: "tapps-brain MCP not ready" };
      }
      try {
        const result = raw as { contents?: Array<{ text?: string }> };
        const text = result.contents?.[0]?.text ?? JSON.stringify(raw);
        return JSON.parse(text);
      } catch {
        return raw;
      }
    },
  });

  // ----------------------------------------------------------------
  // Prompt tools — wrap MCP prompts/get as callable tools
  // ----------------------------------------------------------------

  // memory_recall_prompt — recall prompt
  api.registerTool("memory_recall_prompt", {
    description:
      "Invoke the tapps-brain 'recall' prompt: automatically retrieve memories " +
      "about a given topic from the store and return them formatted for review. " +
      "Returns the prompt messages ready to pass to the model.",
    inputSchema: {
      type: "object",
      properties: {
        topic: {
          type: "string",
          description: "The topic or question to recall memories about",
        },
      },
      required: ["topic"],
    },
    handler: async (args: Record<string, unknown>) => {
      const topic = (args.topic as string) ?? "";
      const raw = await engine.callMcpPrompt("recall", { topic });
      if (raw === null) {
        return { error: "unavailable", message: "tapps-brain MCP not ready" };
      }
      try {
        return raw as Record<string, unknown>;
      } catch {
        return raw;
      }
    },
  });

  // memory_store_summary_prompt — store_summary prompt
  api.registerTool("memory_store_summary_prompt", {
    description:
      "Invoke the tapps-brain 'store_summary' prompt: generate a natural-language " +
      "summary of what's currently in the memory store, including statistics, " +
      "tier distribution, and a sample of recent entries.",
    inputSchema: { type: "object", properties: {} },
    handler: async (_args: Record<string, unknown>) => {
      const raw = await engine.callMcpPrompt("store_summary", {});
      if (raw === null) {
        return { error: "unavailable", message: "tapps-brain MCP not ready" };
      }
      try {
        return raw as Record<string, unknown>;
      } catch {
        return raw;
      }
    },
  });

  // memory_remember_prompt — remember prompt
  api.registerTool("memory_remember_prompt", {
    description:
      "Invoke the tapps-brain 'remember' prompt: guided workflow to save a fact " +
      "to the memory store with an appropriate key, tier, tags, and confidence. " +
      "The prompt instructs the model to call memory_save with correct parameters.",
    inputSchema: {
      type: "object",
      properties: {
        fact: {
          type: "string",
          description: "The fact, decision, or piece of knowledge to remember",
        },
      },
      required: ["fact"],
    },
    handler: async (args: Record<string, unknown>) => {
      const fact = (args.fact as string) ?? "";
      const raw = await engine.callMcpPrompt("remember", { fact });
      if (raw === null) {
        return { error: "unavailable", message: "tapps-brain MCP not ready" };
      }
      try {
        return raw as Record<string, unknown>;
      } catch {
        return raw;
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Plugin entry — the default export OpenClaw loads
// ---------------------------------------------------------------------------

export default definePluginEntry({
  id: "tapps-brain-memory",
  name: "tapps-brain — Persistent Memory",
  register(api) {
    const mode = getCompatibilityMode(api.runtime.version, api.logger);
    const resolvedDir = api.runtime.agent?.resolveAgentWorkspaceDir() ?? null;
    if (!resolvedDir) {
      api.logger.warn(
        "[tapps-brain] Could not resolve workspace directory from runtime. " +
          "Falling back to process.cwd(). Memory may be stored in the wrong location.",
      );
    }
    const workspaceDir = resolvedDir ?? process.cwd();

    // Create a shared engine instance used across all registration paths.
    // Bootstrap runs asynchronously; hooks and tool handlers await readiness
    // via the internal `ready` promise — they never block or fail hard.
    const config = api.config as PluginConfig;
    const engine = new TappsBrainEngine(config, workspaceDir, api.logger);
    engine.bootstrap().catch((err: unknown) => {
      api.logger.warn("[tapps-brain] bootstrap failed:", err);
    });

    // Register memory slot tools (memory_search / memory_get) unconditionally.
    // When plugins.slots.memory = "tapps-brain-memory" is set in the OpenClaw
    // config, these tools replace the built-in memory-core tools. Falls back
    // gracefully if registerTool is unavailable (older OpenClaw builds).
    if (api.registerTool) {
      const cfg = api.config;
      // core — memory_search, memory_get (memory slot replacement tools).
      // Always check per toolGroups config; defaults to "all" (enabled).
      if (isGroupEnabled(cfg, "core")) registerMemorySlotTools(api, engine);
      // lifecycle — memory_reinforce, memory_supersede, memory_history,
      // memory_search_sessions. Available in all compatibility modes.
      if (isGroupEnabled(cfg, "lifecycle")) registerLifecycleTools(api, engine);
      // hive — hive_status, hive_search, hive_propagate, agent_register,
      // agent_create, agent_list, agent_delete.
      // Degrade gracefully if Hive is disabled on the server side.
      if (isGroupEnabled(cfg, "hive")) registerHiveTools(api, engine);
      // graph — memory_relations, memory_find_related, memory_query_relations.
      if (isGroupEnabled(cfg, "graph")) registerKnowledgeGraphTools(api, engine);
      // admin — memory_audit, tags, profile, maintenance, GC config,
      // consolidation config, memory_export, memory_import.
      if (isGroupEnabled(cfg, "admin")) {
        registerAuditTagsProfileTools(api, engine);
        registerMaintenanceConfigTools(api, engine);
      }
      // federation — federation_status/subscribe/unsubscribe/publish.
      if (isGroupEnabled(cfg, "federation")) registerFederationTools(api, engine);
      // search — memory_stats, memory_health, memory_metrics,
      // memory_entry_detail, memory_recall_prompt, memory_store_summary_prompt,
      // memory_remember_prompt. Uses resources/read and prompts/get MCP methods.
      if (isGroupEnabled(cfg, "search")) registerResourceAndPromptTools(api, engine);
    }

    if (mode === "context-engine") {
      // v2026.3.7+: full ContextEngine lifecycle (ingest/assemble/compact)
      if (!api.registerContextEngine) {
        api.logger.warn(
          "[tapps-brain] context-engine mode selected but registerContextEngine is unavailable.",
        );
        return;
      }
      // Return the shared engine from the factory. The engine is already
      // bootstrapped above; the factory config parameter is intentionally
      // unused since we consumed api.config at construction time.
      api.registerContextEngine(
        "tapps-brain-memory",
        (_config) => engine,
      );
    } else if (mode === "hook-only") {
      // v2026.3.1-2026.3.6: inject memories via before_agent_start hook
      if (!api.registerHook) {
        api.logger.warn(
          "[tapps-brain] hook-only mode selected but registerHook is unavailable.",
        );
        return;
      }

      api.registerHook(
        "before_agent_start",
        async (ctx): Promise<void> => {
          const messages = (ctx.messages ?? []) as Message[];
          const sessionId = ctx.sessionId ?? "";
          try {
            const result = await engine.assemble({
              sessionId,
              messages,
              tokenBudget: { soft: 2000, hard: 4000 },
            });
            if (result.systemPromptAddition) {
              // Prepend memory context to the messages array in-place
              messages.unshift({
                role: "system",
                content: result.systemPromptAddition,
              });
            }
          } catch (err) {
            api.logger.warn("[tapps-brain] before_agent_start hook:", err);
          }
        },
      );
    }
    // <v2026.3.1: tools-only — warning already logged in getCompatibilityMode.
    // Memory injection is unavailable without registerHook; memory slot tools
    // are still registered above if registerTool is available.
  },
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
