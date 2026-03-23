/**
 * MCP Client — JSON-RPC 2.0 over stdio transport.
 *
 * Spawns `tapps-brain-mcp` as a child process and communicates via
 * the Model Context Protocol (JSON-RPC 2.0 with Content-Length framing).
 *
 * @module @tapps-brain/openclaw-plugin/mcp-client
 */

import { spawn, type ChildProcess } from "node:child_process";
import { resolve } from "node:path";
import { existsSync } from "node:fs";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** MCP protocol version this client implements. */
const MCP_PROTOCOL_VERSION = "2024-11-05";

/** Request timeout in milliseconds (10 s). */
const REQUEST_TIMEOUT_MS = 10_000;

/** Exponential backoff delays for reconnect retries (ms). */
const RECONNECT_DELAYS_MS = [100, 200, 400] as const;

/** Health check interval (ms). */
const HEALTH_CHECK_INTERVAL_MS = 60_000;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** JSON-RPC 2.0 request. */
interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: Record<string, unknown>;
}

/** JSON-RPC 2.0 response. */
interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

/** Pending request waiting for a response. */
interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
}

// ---------------------------------------------------------------------------
// McpClient
// ---------------------------------------------------------------------------

/**
 * A minimal MCP client that communicates with `tapps-brain-mcp` over stdio.
 *
 * Uses Content-Length framed JSON-RPC 2.0 messages, which is the standard
 * MCP stdio transport format.
 *
 * Note: Content-Length values are UTF-8 byte counts. The internal buffer
 * stores decoded JavaScript strings (UTF-16 internally). For ASCII-only JSON
 * (the common case for MCP messages) byte count equals character count. For
 * messages containing non-ASCII characters the comparison in processBuffer()
 * may be incorrect; full correctness would require working with raw Buffers
 * throughout, which is left as a future improvement.
 */
export class McpClient {
  private process: ChildProcess | null = null;
  private nextId = 1;
  private pending = new Map<number, PendingRequest>();
  private buffer = "";
  private _projectDir: string;
  private _command = "tapps-brain-mcp";
  private _extraArgs: string[] = [];
  private _healthCheckTimer: ReturnType<typeof setInterval> | null = null;

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
    if (this.process) {
      return; // Already running
    }

    // Store params so reconnect() can re-use them.
    this._command = command;
    this._extraArgs = extraArgs;

    const args = ["--project-dir", this._projectDir, ...extraArgs];
    this.process = spawn(command, args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env },
    });

    this.process.stdout?.on("data", (chunk: Buffer) => {
      this.onData(chunk.toString("utf-8"));
    });

    this.process.on("error", (err: Error) => {
      // Reject all pending requests on process error
      for (const [id, req] of this.pending) {
        req.reject(new Error(`MCP process error: ${err.message}`));
        this.pending.delete(id);
      }
    });

    this.process.on("exit", (code: number | null) => {
      for (const [id, req] of this.pending) {
        req.reject(new Error(`MCP process exited with code ${code}`));
        this.pending.delete(id);
      }
      this.process = null;
    });

    // Wait briefly for the process to be ready
    await new Promise<void>((res) => setTimeout(res, 100));

    // MCP initialization handshake (protocol-level, not a tool call).
    // See: https://spec.modelcontextprotocol.io/specification/basic/lifecycle/
    await this.sendRpc("initialize", {
      protocolVersion: MCP_PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: { name: "tapps-brain-openclaw", version: "1.3.0" },
    });

    // Notify server that initialization is complete (no response expected).
    this.sendNotification("notifications/initialized");

    // Start periodic health checks (skip if already running from a previous start).
    if (!this._healthCheckTimer) {
      this._healthCheckTimer = setInterval(() => {
        this._runHealthCheck().catch(() => {
          // Health check errors are non-fatal; reconnect will be attempted on
          // the next callTool() call if the process is dead.
        });
      }, HEALTH_CHECK_INTERVAL_MS);
    }
  }

  /** Stop the MCP child process and cancel the health-check timer. */
  stop(): void {
    if (this._healthCheckTimer) {
      clearInterval(this._healthCheckTimer);
      this._healthCheckTimer = null;
    }
    if (this.process) {
      this.process.kill("SIGTERM");
      this.process = null;
    }
    this.pending.clear();
  }

  /**
   * Reconnect: tear down any dead process state and spawn a fresh child.
   *
   * Safe to call when the process is already null (e.g. after an unexpected
   * exit).  Should not be called when the process is still alive — use
   * `stop()` first in that case.
   */
  async reconnect(): Promise<void> {
    // Ensure any lingering process handle is cleared before re-spawning.
    if (this.process) {
      this.process.kill("SIGTERM");
      this.process = null;
    }
    this.buffer = "";
    await this.start(this._command, this._extraArgs);
  }

  /** Whether the MCP process is running. */
  get isRunning(): boolean {
    return this.process !== null;
  }

  /**
   * Read an MCP resource by URI.
   *
   * Uses the `resources/read` JSON-RPC method from the MCP specification.
   * Automatically reconnects on process death with the same retry logic as
   * `callTool()`.
   *
   * @param uri - The resource URI (e.g. "memory://stats", "memory://entries/my-key").
   * @returns The parsed result from the resource response.
   */
  async readResource(uri: string): Promise<unknown> {
    let lastErr: unknown;

    for (let attempt = 0; attempt <= RECONNECT_DELAYS_MS.length; attempt++) {
      if (!this.isRunning) {
        if (attempt > 0) {
          await new Promise<void>((res) => setTimeout(res, RECONNECT_DELAYS_MS[attempt - 1]));
        }
        try {
          await this.reconnect();
        } catch (reconnErr) {
          lastErr = reconnErr;
          continue;
        }
      }

      try {
        return await this.sendRpc("resources/read", { uri });
      } catch (err) {
        lastErr = err;
        if (this.isRunning) {
          this.process!.kill("SIGTERM");
          this.process = null;
        }
      }
    }

    throw lastErr ?? new Error(`MCP readResource(${uri}) failed after ${RECONNECT_DELAYS_MS.length} retries`);
  }

  /**
   * Get an MCP prompt by name with the given arguments.
   *
   * Uses the `prompts/get` JSON-RPC method from the MCP specification.
   * Automatically reconnects on process death with the same retry logic as
   * `callTool()`.
   *
   * @param name - The prompt name (e.g. "recall", "store_summary", "remember").
   * @param args - Named string arguments for the prompt template.
   * @returns The parsed result from the prompts/get response.
   */
  async callPrompt(name: string, args: Record<string, string>): Promise<unknown> {
    let lastErr: unknown;

    for (let attempt = 0; attempt <= RECONNECT_DELAYS_MS.length; attempt++) {
      if (!this.isRunning) {
        if (attempt > 0) {
          await new Promise<void>((res) => setTimeout(res, RECONNECT_DELAYS_MS[attempt - 1]));
        }
        try {
          await this.reconnect();
        } catch (reconnErr) {
          lastErr = reconnErr;
          continue;
        }
      }

      try {
        return await this.sendRpc("prompts/get", { name, arguments: args });
      } catch (err) {
        lastErr = err;
        if (this.isRunning) {
          this.process!.kill("SIGTERM");
          this.process = null;
        }
      }
    }

    throw lastErr ?? new Error(`MCP callPrompt(${name}) failed after ${RECONNECT_DELAYS_MS.length} retries`);
  }

  /**
   * Call an MCP tool by name with the given arguments.
   *
   * Automatically reconnects the child process if it has died, retrying up to
   * 3 times with exponential back-off (100 ms / 200 ms / 400 ms).
   *
   * @returns The parsed result from the tool response.
   */
  async callTool(name: string, args: Record<string, unknown>): Promise<unknown> {
    let lastErr: unknown;

    for (let attempt = 0; attempt <= RECONNECT_DELAYS_MS.length; attempt++) {
      // If the process is gone, wait (on retries) then reconnect.
      if (!this.isRunning) {
        if (attempt > 0) {
          await new Promise<void>((res) => setTimeout(res, RECONNECT_DELAYS_MS[attempt - 1]));
        }
        try {
          await this.reconnect();
        } catch (reconnErr) {
          lastErr = reconnErr;
          continue; // Try again after the next delay
        }
      }

      try {
        return await this.sendRpc("tools/call", { name, arguments: args });
      } catch (err) {
        lastErr = err;
        // If the process exited while we were waiting, the exit handler already
        // set this.process = null — the next loop iteration will reconnect.
        // If the process is still alive (e.g. a request timeout), mark it dead
        // so the next attempt triggers a fresh reconnect.
        if (this.isRunning) {
          this.process!.kill("SIGTERM");
          this.process = null;
        }
      }
    }

    throw lastErr ?? new Error(`MCP callTool(${name}) failed after ${RECONNECT_DELAYS_MS.length} retries`);
  }

  // -----------------------------------------------------------------------
  // Internal: health check
  // -----------------------------------------------------------------------

  /**
   * Lightweight liveness probe: calls `memory_list` with limit=0.
   * If the call fails the process is likely dead; the next `callTool()` call
   * will trigger reconnection automatically.
   */
  private async _runHealthCheck(): Promise<void> {
    await this.sendRpc("tools/call", { name: "memory_list", arguments: { limit: 0 } });
  }

  // -----------------------------------------------------------------------
  // Internal: protocol helpers
  // -----------------------------------------------------------------------

  /**
   * Send a JSON-RPC request and await its response.
   *
   * Rejects after REQUEST_TIMEOUT_MS if no response is received.
   */
  private async sendRpc(method: string, params?: Record<string, unknown>): Promise<unknown> {
    if (!this.process?.stdin) {
      throw new Error("MCP process not running");
    }

    const id = this.nextId++;
    const request: JsonRpcRequest = {
      jsonrpc: "2.0",
      id,
      method,
      ...(params !== undefined ? { params } : {}),
    };

    const body = JSON.stringify(request);
    const message = `Content-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`;

    return new Promise<unknown>((res, rej) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        rej(new Error(`MCP request timeout after ${REQUEST_TIMEOUT_MS}ms: ${method}`));
      }, REQUEST_TIMEOUT_MS);

      this.pending.set(id, {
        resolve: (value) => {
          clearTimeout(timer);
          res(value);
        },
        reject: (reason) => {
          clearTimeout(timer);
          rej(reason);
        },
      });

      this.process!.stdin!.write(message, "utf-8");
    });
  }

  /**
   * Send a JSON-RPC notification (no `id`, no response expected).
   */
  private sendNotification(method: string): void {
    if (!this.process?.stdin) return;
    const body = JSON.stringify({ jsonrpc: "2.0", method });
    const message = `Content-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`;
    this.process.stdin.write(message, "utf-8");
  }

  // -----------------------------------------------------------------------
  // Internal: Content-Length framed message parsing
  // -----------------------------------------------------------------------

  private onData(chunk: string): void {
    this.buffer += chunk;
    this.processBuffer();
  }

  private processBuffer(): void {
    while (true) {
      const headerEnd = this.buffer.indexOf("\r\n\r\n");
      if (headerEnd === -1) break;

      const header = this.buffer.slice(0, headerEnd);
      const match = /Content-Length:\s*(\d+)/i.exec(header);
      if (!match) {
        // Skip malformed header
        this.buffer = this.buffer.slice(headerEnd + 4);
        continue;
      }

      const contentLength = parseInt(match[1], 10);
      const bodyStart = headerEnd + 4;

      if (this.buffer.length < bodyStart + contentLength) {
        break; // Wait for more data
      }

      const body = this.buffer.slice(bodyStart, bodyStart + contentLength);
      this.buffer = this.buffer.slice(bodyStart + contentLength);

      try {
        const response = JSON.parse(body) as JsonRpcResponse;
        this.handleResponse(response);
      } catch {
        // Skip malformed JSON
      }
    }
  }

  private handleResponse(response: JsonRpcResponse): void {
    const pending = this.pending.get(response.id);
    if (!pending) return;

    this.pending.delete(response.id);

    if (response.error) {
      pending.reject(
        new Error(`MCP error ${response.error.code}: ${response.error.message}`),
      );
    } else {
      pending.resolve(response.result);
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
