/**
 * MCP Client — official SDK transport layer.
 *
 * Uses `@modelcontextprotocol/sdk`'s `StdioClientTransport` and `Client`
 * to communicate with `tapps-brain-mcp`. This is the same SDK used by
 * OpenClaw, Claude Desktop, and every other MCP host.
 *
 * @module @tapps-brain/openclaw-plugin/mcp-client
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { resolve } from "node:path";
import { existsSync } from "node:fs";

// ---------------------------------------------------------------------------
// McpClient
// ---------------------------------------------------------------------------

/**
 * MCP client that communicates with `tapps-brain-mcp` over stdio.
 *
 * Backed by `@modelcontextprotocol/sdk`'s `StdioClientTransport` and `Client`.
 * The SDK handles Content-Length framing, JSON-RPC 2.0 protocol, the MCP
 * initialization handshake, and stdio lifecycle automatically.
 *
 * Reconnection uses OpenClaw's session-invalidation pattern: on error,
 * the session is torn down and lazily re-created on the next call.
 */
export class McpClient {
  private client: Client | null = null;
  private transport: StdioClientTransport | null = null;
  private _projectDir: string;
  private _command = "tapps-brain-mcp";
  private _extraArgs: string[] = [];

  constructor(projectDir: string) {
    this._projectDir = resolve(projectDir);
  }

  /** The project directory this client was initialized with. */
  get projectDir(): string {
    return this._projectDir;
  }

  /**
   * Spawn the `tapps-brain-mcp` child process and perform the MCP
   * initialization handshake.
   *
   * @param command - Override for the MCP command (default: "tapps-brain-mcp").
   * @param extraArgs - Additional CLI arguments (e.g., "--agent-id", "--enable-hive").
   */
  async start(command = "tapps-brain-mcp", extraArgs: string[] = []): Promise<void> {
    if (this.client) {
      return; // Already running
    }

    // Store params so reconnect() can re-use them.
    this._command = command;
    this._extraArgs = extraArgs;

    const args = ["--project-dir", this._projectDir, ...extraArgs];

    this.transport = new StdioClientTransport({
      command,
      args,
      stderr: "pipe",
    });

    // Attach stderr logging — forward MCP server diagnostic output.
    // The SDK returns `Stream | null`; cast via unknown to access `.on()`.
    const stderr = this.transport.stderr as unknown as
      | (NodeJS.ReadableStream & { on: (e: string, cb: (d: Buffer | string) => void) => void })
      | null;
    if (stderr && typeof stderr.on === "function") {
      stderr.on("data", (chunk: Buffer | string) => {
        const lines = chunk.toString().split("\n").filter(Boolean);
        for (const line of lines) {
          console.error(`[tapps-brain-mcp] ${line}`);
        }
      });
    }

    this.client = new Client(
      { name: "tapps-brain-openclaw", version: "1.4.0" },
      {},
    );

    // connect() internally calls transport.start() and performs the MCP
    // initialization handshake (initialize + notifications/initialized).
    await this.client.connect(this.transport);
  }

  /**
   * Stop the MCP child process.
   *
   * Uses two-phase close matching OpenClaw's `disposeSession()` pattern:
   * client.close() then transport.close(), each with swallowed errors.
   */
  stop(): void {
    const client = this.client;
    const transport = this.transport;
    this.client = null;
    this.transport = null;

    // Fire-and-forget async close to preserve sync API.
    if (client) {
      void client.close().catch(() => {});
    }
    if (transport) {
      void transport.close().catch(() => {});
    }
  }

  /**
   * Reconnect: tear down any existing session and spawn a fresh one.
   */
  async reconnect(): Promise<void> {
    this.stop();
    await this.start(this._command, this._extraArgs);
  }

  /** Whether the MCP process is running. */
  get isRunning(): boolean {
    return this.transport !== null && this.transport.pid !== null;
  }

  /**
   * Call an MCP tool by name with the given arguments.
   *
   * On transport/connection error the session is invalidated so the next
   * call triggers a fresh reconnect (OpenClaw's session-invalidation pattern).
   *
   * @returns The parsed result from the tool response.
   */
  async callTool(name: string, args: Record<string, unknown>): Promise<unknown> {
    if (!this.isRunning) {
      await this.reconnect();
    }

    try {
      const result = await this.client!.callTool({
        name,
        arguments: args,
      }) as CallToolResult;
      return result;
    } catch (err) {
      // Session invalidation: tear down so next call reconnects.
      this.stop();
      throw err;
    }
  }

  /**
   * Read an MCP resource by URI.
   *
   * @param uri - The resource URI (e.g. "memory://stats", "memory://entries/my-key").
   * @returns The parsed result from the resource response.
   */
  async readResource(uri: string): Promise<unknown> {
    if (!this.isRunning) {
      await this.reconnect();
    }

    try {
      return await this.client!.readResource({ uri });
    } catch (err) {
      this.stop();
      throw err;
    }
  }

  /**
   * Get an MCP prompt by name with the given arguments.
   *
   * @param name - The prompt name (e.g. "recall", "store_summary", "remember").
   * @param args - Named string arguments for the prompt template.
   * @returns The parsed result from the prompts/get response.
   */
  async callPrompt(name: string, args: Record<string, string>): Promise<unknown> {
    if (!this.isRunning) {
      await this.reconnect();
    }

    try {
      return await this.client!.getPrompt({ name, arguments: args });
    } catch (err) {
      this.stop();
      throw err;
    }
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Check whether a MEMORY.md file exists in the given workspace directory.
 */
export function hasMemoryMd(workspaceDir: string): boolean {
  return existsSync(resolve(workspaceDir, "MEMORY.md"));
}

/**
 * Check whether this is a first-run scenario (no `.tapps-brain/` directory yet).
 */
export function isFirstRun(projectDir: string): boolean {
  return !existsSync(resolve(projectDir, ".tapps-brain"));
}
