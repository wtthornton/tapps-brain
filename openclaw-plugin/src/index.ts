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
}

/** Logger interface matching OpenClaw's api.logger shape. */
export interface PluginLogger {
  info: (...args: unknown[]) => void;
  warn: (...args: unknown[]) => void;
}

/** OpenClaw plugin API passed to the register() callback. */
interface OpenClawPluginApi {
  logger: PluginLogger;
  config: PluginConfig;
  runtime: { workspaceDir: string; sessionId: string };
  registerContextEngine(
    id: string,
    factory: (config: PluginConfig) => TappsBrainEngine,
  ): void;
}

/** Plugin entry definition. */
interface PluginEntryDef {
  id: string;
  name: string;
  register: (api: OpenClawPluginApi) => void;
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

      for (const mem of newMemories) {
        const entry = `- **${mem.key}**: ${mem.value}\n`;
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
    api.registerContextEngine("tapps-brain-memory", (config: PluginConfig) => {
      const workspaceDir = api.runtime.workspaceDir;
      const engine = new TappsBrainEngine(config, workspaceDir, api.logger);

      // Bootstrap asynchronously — don't block registration
      engine.bootstrap().catch((err) => {
        api.logger.warn("[tapps-brain] bootstrap failed:", err);
      });

      return engine;
    });
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
