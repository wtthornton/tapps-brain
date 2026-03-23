/**
 * Ambient type declarations for `openclaw/plugin-sdk/core`.
 *
 * These types reflect the actual OpenClaw SDK shape as of v2026.3.7+.
 * They enable type-safe plugin development without a full openclaw install.
 * The openclaw package is an optional peer dependency — the try/catch in
 * index.ts handles the runtime case where it is not installed.
 */
declare module "openclaw/plugin-sdk/core" {
  /** Agent-scoped runtime helpers. */
  export interface PluginAgent {
    /**
     * Resolve the workspace directory for the current agent.
     * Returns `undefined` when no workspace can be determined (e.g. no project
     * is open). Callers should fall back to `process.cwd()` in that case.
     */
    resolveAgentWorkspaceDir(): string | undefined;
  }

  /** OpenClaw runtime context injected by the host application. */
  export interface PluginRuntime {
    /**
     * OpenClaw runtime version string (e.g. "2026.3.13").
     * This is the *host application* version — NOT the plugin's own version.
     */
    version: string;
    /** Current session identifier. */
    sessionId: string;
    /** Agent-scoped methods. */
    agent: PluginAgent;
  }

  /**
   * Plugin configuration read from the host application's config schema.
   * The concrete shape is defined by each plugin's `openclaw.plugin.json`
   * `configSchema` field; the SDK treats it as an open record.
   */
  export interface PluginConfig {
    [key: string]: unknown;
  }

  /** Logger provided by the host application. */
  export interface PluginLogger {
    info: (...args: unknown[]) => void;
    warn: (...args: unknown[]) => void;
  }

  /** Tool definition passed to `registerTool()`. */
  export interface PluginToolDefinition {
    description: string;
    inputSchema?: Record<string, unknown>;
    handler: (args: Record<string, unknown>) => Promise<unknown>;
  }

  /**
   * Context passed to hook handlers registered via `registerHook()`.
   * Additional properties may be present depending on the hook event type.
   */
  export interface PluginHookContext {
    sessionId: string;
    messages?: Array<{ role: string; content: string; [key: string]: unknown }>;
    [key: string]: unknown;
  }

  /** The full OpenClaw plugin API passed to a plugin's `register()` callback. */
  export interface OpenClawPluginApi {
    logger: PluginLogger;
    config: PluginConfig;
    runtime: PluginRuntime;
    /**
     * Register a ContextEngine — available on OpenClaw v2026.3.7+.
     * The factory receives the plugin config and must return an engine instance.
     */
    registerContextEngine?: (
      id: string,
      factory: (config: PluginConfig) => object,
    ) => void;
    /**
     * Register a lifecycle hook — available on OpenClaw v2026.3.1+.
     * The `event` string identifies the hook point (e.g. `"before_agent_start"`).
     */
    registerHook?: (
      event: string,
      handler: (ctx: PluginHookContext) => Promise<void>,
    ) => void;
    /** Register a native tool — available in all versions. */
    registerTool?: (name: string, definition: PluginToolDefinition) => void;
  }

  /**
   * Plugin entry definition — the shape passed to `definePluginEntry()`.
   * OpenClaw loads the default export of the plugin module and calls `register()`
   * with the full plugin API.
   */
  export interface PluginEntry {
    id: string;
    name: string;
    register: (api: OpenClawPluginApi) => void;
  }

  /**
   * Wrap a plugin entry definition.
   *
   * At runtime this function is provided by the openclaw/plugin-sdk/core module.
   * The try/catch shim in index.ts provides an identity fallback when openclaw
   * is not installed (dev/test environments).
   */
  export function definePluginEntry(def: PluginEntry): PluginEntry;
}
