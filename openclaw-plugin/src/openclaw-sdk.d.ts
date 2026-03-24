/**
 * Ambient type declarations for the OpenClaw Plugin SDK.
 *
 * These types match the real OpenClaw SDK as verified against the source at
 * github.com/openclaw/openclaw (2026-03-23). They enable type-safe plugin
 * development without requiring the openclaw package to be installed.
 *
 * Key source files in the real SDK:
 *   - src/plugins/types.ts            — OpenClawPluginApi, tool types
 *   - src/plugins/runtime/types-core.ts — PluginRuntime, PluginAgent
 *   - src/context-engine/types.ts      — ContextEngine, CompactResult
 *   - src/context-engine/registry.ts   — ContextEngineFactory
 *   - src/plugin-sdk/plugin-entry.ts   — definePluginEntry
 *   - src/context-engine/delegate.ts   — delegateCompactionToRuntime
 *   - src/agents/tools/common.ts       — AnyAgentTool
 */

// ---------------------------------------------------------------------------
// openclaw/plugin-sdk/plugin-entry
// ---------------------------------------------------------------------------

declare module "openclaw/plugin-sdk/plugin-entry" {
  import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

  export interface DefinedPluginEntry {
    id: string;
    name: string;
    description: string;
    kind?: "memory" | "context-engine";
    register: (api: OpenClawPluginApi) => void;
  }

  export interface PluginEntryOptions {
    id: string;
    name: string;
    description: string;
    kind?: "memory" | "context-engine";
    configSchema?: Record<string, unknown> | (() => Record<string, unknown>);
    register: (api: OpenClawPluginApi) => void;
  }

  export function definePluginEntry(options: PluginEntryOptions): DefinedPluginEntry;
}

// ---------------------------------------------------------------------------
// openclaw/plugin-sdk/core
// ---------------------------------------------------------------------------

declare module "openclaw/plugin-sdk/core" {
  // -- Agent Tool types ---------------------------------------------------

  /** Result returned from a tool's execute() method. */
  export interface AgentToolResult {
    content: Array<{ type: string; text: string }>;
    details?: unknown;
  }

  /**
   * A single agent tool. The real SDK extends AgentTool from
   * @mariozechner/pi-agent-core; we declare the subset our plugin uses.
   */
  export interface AnyAgentTool {
    name: string;
    label?: string;
    description: string;
    /** JSON Schema for the tool's parameters. */
    parameters?: Record<string, unknown>;
    ownerOnly?: boolean;
    execute(
      toolCallId: string,
      params: Record<string, unknown>,
    ): Promise<AgentToolResult>;
  }

  /** Context passed to tool factory functions. */
  export interface OpenClawPluginToolContext {
    config?: Record<string, unknown>;
    workspaceDir?: string;
    agentDir?: string;
    agentId?: string;
    sessionKey?: string;
    sessionId?: string;
    messageChannel?: string;
    agentAccountId?: string;
    requesterSenderId?: string;
    senderIsOwner?: boolean;
    sandboxed?: boolean;
  }

  /** Factory function that creates tool(s) from context. */
  export type OpenClawPluginToolFactory = (
    ctx: OpenClawPluginToolContext,
  ) => AnyAgentTool | AnyAgentTool[] | null | undefined;

  /** Options for registerTool(). */
  export interface OpenClawPluginToolOptions {
    name?: string;
    names?: string[];
    optional?: boolean;
  }

  // -- Context Engine types -----------------------------------------------

  /** Engine metadata descriptor. */
  export interface ContextEngineInfo {
    id: string;
    name: string;
    version: string;
    ownsCompaction: boolean;
  }

  /** Message in the conversation context. */
  export interface AgentMessage {
    role: string;
    content: string;
    [key: string]: unknown;
  }

  export interface BootstrapResult {
    /** Whether bootstrap ran and initialized the engine's store. */
    bootstrapped: boolean;
    /** Number of historical messages imported (if applicable). */
    importedMessages?: number;
    /** Optional reason when bootstrap was skipped. */
    reason?: string;
  }

  export interface IngestResult {
    ingested: boolean;
  }

  export interface AssembleResult {
    messages: AgentMessage[];
    estimatedTokens: number;
    systemPromptAddition?: string;
  }

  export interface CompactResult {
    ok: boolean;
    compacted: boolean;
    reason?: string;
    result?: {
      summary?: string;
      firstKeptEntryId?: string;
      tokensBefore: number;
      tokensAfter?: number;
      details?: unknown;
    };
  }

  /** Runtime-owned context passed to engines that need caller state. */
  export type ContextEngineRuntimeContext = Record<string, unknown>;

  /** The ContextEngine lifecycle interface. */
  export interface ContextEngine {
    readonly info: ContextEngineInfo;

    bootstrap?(params: {
      sessionId: string;
      sessionKey?: string;
      sessionFile: string;
    }): Promise<BootstrapResult>;

    ingest(params: {
      sessionId: string;
      sessionKey?: string;
      message: AgentMessage;
      isHeartbeat?: boolean;
    }): Promise<IngestResult>;

    assemble(params: {
      sessionId: string;
      sessionKey?: string;
      messages: AgentMessage[];
      tokenBudget?: number;
      model?: string;
      prompt?: string;
    }): Promise<AssembleResult>;

    compact(params: {
      sessionId: string;
      sessionKey?: string;
      sessionFile: string;
      tokenBudget?: number;
      force?: boolean;
      currentTokenCount?: number;
      compactionTarget?: "budget" | "threshold";
      customInstructions?: string;
      runtimeContext?: ContextEngineRuntimeContext;
    }): Promise<CompactResult>;

    dispose?(): Promise<void>;
  }

  /** Factory function that creates a ContextEngine (parameterless). */
  export type ContextEngineFactory = () => ContextEngine | Promise<ContextEngine>;

  // -- Runtime types ------------------------------------------------------

  /** Agent-scoped runtime helpers. */
  export interface PluginAgent {
    /**
     * Resolve the workspace directory for the current agent.
     * Requires the full OpenClaw config and the agent ID.
     */
    resolveAgentWorkspaceDir(
      cfg: Record<string, unknown>,
      agentId: string,
    ): string | undefined;
  }

  /** OpenClaw runtime context injected by the host application. */
  export interface PluginRuntime {
    /** Host application version string (e.g. "2026.3.23"). */
    version: string;
    /** Current session identifier. */
    sessionId: string;
    /** Agent-scoped methods. */
    agent: PluginAgent;
  }

  /** Logger provided by the host application. */
  export interface PluginLogger {
    info: (...args: unknown[]) => void;
    warn: (...args: unknown[]) => void;
    debug?: (...args: unknown[]) => void;
  }

  // -- Plugin API ---------------------------------------------------------

  /** The full OpenClaw plugin API passed to a plugin's register() callback. */
  export interface OpenClawPluginApi {
    /** Plugin identifier from the manifest. */
    id: string;
    /** Plugin display name. */
    name: string;
    /** Plugin version (from manifest). */
    version?: string;
    /** Plugin description. */
    description?: string;
    /** Source path or identifier. */
    source: string;
    /** Plugin root directory. */
    rootDir?: string;
    /** Full OpenClaw application configuration. */
    config: Record<string, unknown>;
    /** Plugin-specific configuration from plugins.entries.<id>.config. */
    pluginConfig?: Record<string, unknown>;
    /** Runtime context. */
    runtime: PluginRuntime;
    /** Logger. */
    logger: PluginLogger;

    /** Register an agent tool (direct object or factory). */
    registerTool: (
      tool: AnyAgentTool | OpenClawPluginToolFactory,
      opts?: OpenClawPluginToolOptions,
    ) => void;

    /** Register lifecycle event hooks. */
    registerHook: (
      events: string | string[],
      handler: (...args: unknown[]) => Promise<void>,
      opts?: Record<string, unknown>,
    ) => void;

    /** Register a ContextEngine (exclusive slot). */
    registerContextEngine: (
      id: string,
      factory: ContextEngineFactory,
    ) => void;
  }

  // -- Re-exports from plugin-entry ---------------------------------------

  export {
    definePluginEntry,
    type DefinedPluginEntry,
    type PluginEntryOptions,
  } from "openclaw/plugin-sdk/plugin-entry";

  /** Delegate compaction to the OpenClaw runtime's built-in compaction. */
  export function delegateCompactionToRuntime(
    params: Parameters<ContextEngine["compact"]>[0],
  ): Promise<CompactResult>;
}
