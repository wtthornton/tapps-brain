/**
 * tapps-brain OpenClaw ContextEngine Plugin
 *
 * Integrates tapps-brain persistent memory as the ContextEngine for OpenClaw.
 * Provides hooks for bootstrap, auto-recall, auto-capture, and pre-compaction flush.
 *
 * @module @tapps-brain/openclaw-plugin
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { McpClient, hasMemoryMd, isFirstRun } from "./mcp_client.js";

// ---------------------------------------------------------------------------
// Plugin state — shared across hooks within a session
// ---------------------------------------------------------------------------

/** Singleton MCP client, initialized by bootstrap(). */
let mcpClient: McpClient | null = null;

/** Keys already injected in this session — used for dedup in ingest(). */
const injectedKeys: Set<string> = new Set();

/** Turn counter for afterTurn rate limiting — capture at most once every 3 turns. */
let lastCaptureTurn = 0;

/** Minimum number of turns between captures. */
const CAPTURE_RATE_LIMIT = 3;

/** Default token budget for memory injection (characters ≈ tokens × 4). */
const DEFAULT_TOKEN_BUDGET = 4000;

/**
 * Get the active MCP client. Throws if bootstrap() has not been called.
 */
export function getMcpClient(): McpClient {
  if (!mcpClient) {
    throw new Error("MCP client not initialized — call bootstrap() first");
  }
  return mcpClient;
}

// ---------------------------------------------------------------------------
// Types — OpenClaw ContextEngine hook signatures
// ---------------------------------------------------------------------------

/** OpenClaw context passed to hooks. */
export interface OpenClawContext {
  projectDir: string;
  sessionId: string;
  workspaceDir: string;
}

/** Message structure for ingest hook. */
export interface UserMessage {
  role: "user";
  content: string;
}

/** Agent response structure for afterTurn hook. */
export interface AgentResponse {
  role: "assistant";
  content: string;
  turnNumber: number;
}

/** Context chunk being compacted. */
export interface CompactionChunk {
  content: string;
  tokenCount: number;
}

/** Result returned from bootstrap hook. */
export interface BootstrapResult {
  success: boolean;
  memoriesImported?: number;
  primerKeys?: string[];
}

/** Result returned from ingest hook. */
export interface IngestResult {
  memorySection: string;
  keysInjected: string[];
}

/** Result returned from afterTurn hook. */
export interface CaptureResult {
  captured: boolean;
  keys?: string[];
}

/** Result returned from compact hook. */
export interface CompactResult {
  entriesIngested: number;
  sessionIndexed: boolean;
}

// ---------------------------------------------------------------------------
// Hooks — bootstrap (012-E), stubs for 012-F through 012-H
// ---------------------------------------------------------------------------

/**
 * Bootstrap hook: spawns tapps-brain-mcp, imports MEMORY.md on first run,
 * and runs initial recall for session primer.
 *
 * 1. Spawns `tapps-brain-mcp --project-dir <workspaceDir>` as a child process.
 * 2. On first run (no `.tapps-brain/` dir), imports MEMORY.md via MCP tool.
 * 3. Runs initial `memory_recall` to generate a session primer.
 */
export async function bootstrap(
  ctx: OpenClawContext,
): Promise<BootstrapResult> {
  const projectDir = ctx.workspaceDir || ctx.projectDir;

  try {
    // 1. Spawn MCP child process
    mcpClient = new McpClient(projectDir);
    await mcpClient.start();

    let memoriesImported = 0;

    // 2. First-run: import MEMORY.md if it exists and store is fresh
    if (isFirstRun(projectDir) && hasMemoryMd(projectDir)) {
      const memoryMdPath = resolve(projectDir, "MEMORY.md");
      const content = readFileSync(memoryMdPath, "utf-8");

      // Parse MEMORY.md headings into memory entries for import
      const memories = parseMemoryMdForImport(content);
      if (memories.length > 0) {
        const importResult = (await mcpClient.callTool("memory_import", {
          memories_json: JSON.stringify({ memories }),
          overwrite: false,
        })) as string;

        const parsed = JSON.parse(
          typeof importResult === "string" ? importResult : JSON.stringify(importResult),
        ) as { imported?: number };
        memoriesImported = parsed.imported ?? 0;
      }
    }

    // 3. Initial recall for session primer
    const recallResult = (await mcpClient.callTool("memory_recall", {
      message: "session start — retrieve key project context",
    })) as string;

    const recall = JSON.parse(
      typeof recallResult === "string" ? recallResult : JSON.stringify(recallResult),
    ) as { memories?: Array<{ key: string }> };

    const primerKeys = (recall.memories ?? []).map(
      (m: { key: string }) => m.key,
    );

    return {
      success: true,
      memoriesImported,
      primerKeys,
    };
  } catch (err) {
    // Clean up on failure
    if (mcpClient) {
      mcpClient.stop();
      mcpClient = null;
    }
    return { success: false };
  }
}

/**
 * Parse a MEMORY.md file into importable memory entries.
 *
 * Heading levels map to tiers:
 * - H1/H2 → architectural
 * - H3 → pattern
 * - H4+ → procedural
 *
 * Body text under each heading becomes the value.
 */
function parseMemoryMdForImport(
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
      if (value) {
        entries.push({ key: slugify(currentKey), value, tier: currentTier });
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

/**
 * Ingest hook: receives user message, calls memory_recall via MCP,
 * and injects relevant memories into context.
 *
 * 1. Calls `memory_recall(message)` to get ranked memories.
 * 2. Filters out keys already injected in this session (dedup).
 * 3. Builds a `memory_section` string within the token budget.
 * 4. Returns the section for injection as a system-level prefix.
 *
 * Implementation: story 012-F
 */
export async function ingest(
  _ctx: OpenClawContext,
  message: UserMessage,
): Promise<IngestResult> {
  const client = getMcpClient();

  try {
    // 1. Call memory_recall with the user's message
    const recallResult = await client.callTool("memory_recall", {
      message: message.content,
    });

    const recall = JSON.parse(
      typeof recallResult === "string" ? recallResult : JSON.stringify(recallResult),
    ) as { memories?: Array<{ key: string; value: string; tier?: string; confidence?: number }> };

    const memories = recall.memories ?? [];

    // 2. Skip if no relevant memories found
    if (memories.length === 0) {
      return { memorySection: "", keysInjected: [] };
    }

    // 3. Filter out keys already injected in this session (dedup)
    const newMemories = memories.filter((m) => !injectedKeys.has(m.key));

    if (newMemories.length === 0) {
      return { memorySection: "", keysInjected: [] };
    }

    // 4. Build memory_section within token budget
    const lines: string[] = [];
    let charCount = 0;
    const keysInjected: string[] = [];
    const budgetChars = DEFAULT_TOKEN_BUDGET;

    // Header
    const header = "## Relevant Memories\n";
    charCount += header.length;
    lines.push(header);

    for (const mem of newMemories) {
      const entry = `- **${mem.key}**: ${mem.value}\n`;
      if (charCount + entry.length > budgetChars) {
        break; // Respect token budget
      }
      lines.push(entry);
      charCount += entry.length;
      keysInjected.push(mem.key);
      injectedKeys.add(mem.key);
    }

    const memorySection = keysInjected.length > 0 ? lines.join("") : "";

    return { memorySection, keysInjected };
  } catch {
    // Fail gracefully — don't block the turn if recall fails
    return { memorySection: "", keysInjected: [] };
  }
}

/**
 * AfterTurn hook: receives agent response, calls memory_capture via MCP.
 * Rate limited to max once every 3 turns.
 *
 * 1. Checks turn-based rate limit (every 3 turns).
 * 2. Calls `memory_capture` with the agent response content.
 * 3. Returns captured keys for observability.
 *
 * Implementation: story 012-G
 */
export async function afterTurn(
  _ctx: OpenClawContext,
  response: AgentResponse,
): Promise<CaptureResult> {
  // 1. Rate limit: only capture once every CAPTURE_RATE_LIMIT turns
  const turnsSinceCapture = response.turnNumber - lastCaptureTurn;
  if (turnsSinceCapture < CAPTURE_RATE_LIMIT) {
    return { captured: false };
  }

  const client = getMcpClient();

  try {
    // 2. Call memory_capture with the agent response
    const captureResult = await client.callTool("memory_capture", {
      content: response.content,
    });

    const parsed = JSON.parse(
      typeof captureResult === "string" ? captureResult : JSON.stringify(captureResult),
    ) as { captured?: string[]; keys?: string[] };

    const keys = parsed.keys ?? parsed.captured ?? [];

    // 3. Update rate limit state
    lastCaptureTurn = response.turnNumber;

    // Log captured keys (console.log for plugin observability)
    if (keys.length > 0) {
      console.log(`[tapps-brain] afterTurn: captured ${keys.length} key(s):`, keys);
    }

    return { captured: keys.length > 0, keys };
  } catch {
    // Fail gracefully — don't block the turn if capture fails
    return { captured: false };
  }
}

/**
 * Compact hook: receives context being compacted, flushes durable facts
 * to the memory store and indexes the session for later search.
 *
 * 1. Concatenates compaction chunks into a single context string.
 * 2. Calls `memory_ingest(context)` to extract and persist durable facts.
 * 3. Calls `memory_index_session(session_id, chunks)` to index the session.
 * 4. Returns counts for observability.
 *
 * Only processes non-empty chunks. Fails gracefully — never blocks compaction.
 *
 * Implementation: story 012-H
 */
export async function compact(
  ctx: OpenClawContext,
  chunks: CompactionChunk[],
): Promise<CompactResult> {
  // Skip if no chunks to process
  if (!chunks || chunks.length === 0) {
    return { entriesIngested: 0, sessionIndexed: false };
  }

  const client = getMcpClient();

  try {
    // 1. Concatenate chunk contents, filtering out empty chunks
    const nonEmptyChunks = chunks.filter((c) => c.content.trim().length > 0);
    if (nonEmptyChunks.length === 0) {
      return { entriesIngested: 0, sessionIndexed: false };
    }

    const context = nonEmptyChunks.map((c) => c.content).join("\n\n");

    // 2. Call memory_ingest to extract durable facts from compacted context
    const ingestResult = await client.callTool("memory_ingest", {
      context,
      source: "compaction",
    });

    const ingestParsed = JSON.parse(
      typeof ingestResult === "string" ? ingestResult : JSON.stringify(ingestResult),
    ) as { created_keys?: string[]; keys?: string[] };

    const createdKeys = ingestParsed.created_keys ?? ingestParsed.keys ?? [];
    const entriesIngested = createdKeys.length;

    if (entriesIngested > 0) {
      console.log(
        `[tapps-brain] compact: ingested ${entriesIngested} entries from compaction`,
      );
    }

    // 3. Call memory_index_session to index the session chunks
    let sessionIndexed = false;
    const sessionId = ctx.sessionId;

    if (sessionId) {
      const sessionChunks = nonEmptyChunks.map((c) => c.content);

      await client.callTool("memory_index_session", {
        session_id: sessionId,
        chunks: sessionChunks,
      });

      sessionIndexed = true;
      console.log(
        `[tapps-brain] compact: indexed session ${sessionId} with ${sessionChunks.length} chunk(s)`,
      );
    }

    return { entriesIngested, sessionIndexed };
  } catch {
    // Fail gracefully — never block compaction if memory ops fail
    return { entriesIngested: 0, sessionIndexed: false };
  }
}

// ---------------------------------------------------------------------------
// Testing helpers — reset plugin state between tests
// ---------------------------------------------------------------------------

/**
 * Reset all plugin state. Intended for use in tests only.
 */
export function _resetPluginState(): void {
  mcpClient = null;
  injectedKeys.clear();
  lastCaptureTurn = 0;
}

// ---------------------------------------------------------------------------
// Plugin export — default ContextEngine interface
// ---------------------------------------------------------------------------

export default {
  bootstrap,
  ingest,
  afterTurn,
  compact,
};
