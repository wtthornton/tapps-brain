/**
 * tapps-brain OpenClaw ContextEngine Plugin
 *
 * Integrates tapps-brain persistent memory as the ContextEngine for OpenClaw.
 * Provides hooks for bootstrap, auto-recall, auto-capture, and pre-compaction flush.
 *
 * @module @tapps-brain/openclaw-plugin
 */

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
// Hook stubs — implementations in follow-up stories (012-E through 012-H)
// ---------------------------------------------------------------------------

/**
 * Bootstrap hook: spawns tapps-brain-mcp, imports MEMORY.md on first run,
 * and runs initial recall for session primer.
 *
 * Implementation: story 012-E
 */
export async function bootstrap(
  _ctx: OpenClawContext,
): Promise<BootstrapResult> {
  // TODO(012-E): spawn MCP child process, first-run import, initial recall
  return { success: false };
}

/**
 * Ingest hook: receives user message, calls memory_recall via MCP,
 * and injects relevant memories into context.
 *
 * Implementation: story 012-F
 */
export async function ingest(
  _ctx: OpenClawContext,
  _message: UserMessage,
): Promise<IngestResult> {
  // TODO(012-F): call memory_recall, build memory_section, dedup keys
  return { memorySection: "", keysInjected: [] };
}

/**
 * AfterTurn hook: receives agent response, calls memory_capture via MCP.
 * Rate limited to max once every 3 turns.
 *
 * Implementation: story 012-G
 */
export async function afterTurn(
  _ctx: OpenClawContext,
  _response: AgentResponse,
): Promise<CaptureResult> {
  // TODO(012-G): call memory_capture, respect rate limit
  return { captured: false };
}

/**
 * Compact hook: receives context being compacted, flushes to memory store
 * and indexes the session.
 *
 * Implementation: story 012-H
 */
export async function compact(
  _ctx: OpenClawContext,
  _chunks: CompactionChunk[],
): Promise<CompactResult> {
  // TODO(012-H): call memory_ingest + memory_index_session
  return { entriesIngested: 0, sessionIndexed: false };
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
