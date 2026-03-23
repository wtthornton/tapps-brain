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

/** Request timeout in milliseconds (30 s). */
const REQUEST_TIMEOUT_MS = 30_000;

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
      clientInfo: { name: "tapps-brain-openclaw", version: "1.0.0" },
    });

    // Notify server that initialization is complete (no response expected).
    this.sendNotification("notifications/initialized");
  }

  /** Stop the MCP child process. */
  stop(): void {
    if (this.process) {
      this.process.kill("SIGTERM");
      this.process = null;
    }
    this.pending.clear();
  }

  /** Whether the MCP process is running. */
  get isRunning(): boolean {
    return this.process !== null;
  }

  /**
   * Call an MCP tool by name with the given arguments.
   *
   * @returns The parsed result from the tool response.
   */
  async callTool(name: string, args: Record<string, unknown>): Promise<unknown> {
    return this.sendRpc("tools/call", { name, arguments: args });
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
