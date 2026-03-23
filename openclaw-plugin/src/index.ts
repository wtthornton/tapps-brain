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

/** OpenClaw plugin API passed to the register() callback. */
interface OpenClawPluginApi {
  logger: PluginLogger;
  config: PluginConfig;
  runtime: { workspaceDir: string; sessionId: string };
  /** Semver-like version string of the running OpenClaw instance (e.g. "2026.3.7"). */
  version?: string;
  /** Register a ContextEngine — available v2026.3.7+. */
  registerContextEngine?: (
    id: string,
    factory: (config: PluginConfig) => TappsBrainEngine,
  ) => void;
  /** Register a lifecycle hook — available v2026.3.1+. */
  registerHook?: (
    event: string,
    handler: (ctx: HookContext) => Promise<void>,
  ) => void;
  /** Register a native tool — available in all versions. */
  registerTool?: (name: string, definition: ToolDefinition) => void;
}

/** Plugin entry definition. */
interface PluginEntryDef {
  id: string;
  name: string;
  register: (api: OpenClawPluginApi) => void;
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

let definePluginEntry: (def: PluginEntryDef) => PluginEntryDef;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const sdk = require("openclaw/plugin-sdk/core") as { definePluginEntry: typeof definePluginEntry };
  definePluginEntry = sdk.definePluginEntry;
} catch {
  // Fallback: identity function (dev/test without openclaw installed)
  definePluginEntry = (def: PluginEntryDef) => def;
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
    version: "1.2.0",
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
   * dispose — called on gateway shutdown.
   * Stops the MCP child process.
   */
  dispose(): void {
    this.mcpClient.stop();
  }
}

// ---------------------------------------------------------------------------
// Plugin entry — the default export OpenClaw loads
// ---------------------------------------------------------------------------

export default definePluginEntry({
  id: "tapps-brain-memory",
  name: "tapps-brain — Persistent Memory",
  register(api) {
    const mode = getCompatibilityMode(api.version, api.logger);

    if (mode === "context-engine") {
      // v2026.3.7+: full ContextEngine lifecycle (ingest/assemble/compact)
      if (!api.registerContextEngine) {
        api.logger.warn(
          "[tapps-brain] context-engine mode selected but registerContextEngine is unavailable.",
        );
        return;
      }
      api.registerContextEngine(
        "tapps-brain-memory",
        (config: PluginConfig) => {
          const workspaceDir = api.runtime.workspaceDir;
          const engine = new TappsBrainEngine(config, workspaceDir, api.logger);

          // Bootstrap asynchronously — don't block registration
          engine.bootstrap().catch((err: unknown) => {
            api.logger.warn("[tapps-brain] bootstrap failed:", err);
          });

          return engine;
        },
      );
    } else if (mode === "hook-only") {
      // v2026.3.1-2026.3.6: inject memories via before_agent_start hook
      if (!api.registerHook) {
        api.logger.warn(
          "[tapps-brain] hook-only mode selected but registerHook is unavailable.",
        );
        return;
      }
      const workspaceDir = api.runtime.workspaceDir;
      const engine = new TappsBrainEngine(
        api.config,
        workspaceDir,
        api.logger,
      );

      // Bootstrap once at plugin load — hook calls will await this.ready
      engine.bootstrap().catch((err: unknown) => {
        api.logger.warn("[tapps-brain] bootstrap failed:", err);
      });

      api.registerHook(
        "before_agent_start",
        async (ctx: HookContext): Promise<void> => {
          const messages = ctx.messages ?? [];
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
    } else {
      // <v2026.3.1: tools-only — warning already logged in getCompatibilityMode
      // No memory injection is possible without at least registerHook.
      // If registerTool is available, we could expose MCP tools in future work.
      // For now, the warning is the only action (memory injection unavailable).
    }
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
