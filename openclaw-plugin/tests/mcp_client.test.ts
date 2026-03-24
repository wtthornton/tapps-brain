/**
 * Tests for McpClient (028-D)
 *
 * Covers:
 *  - JSON-RPC Content-Length message framing (parsing)
 *  - Request / response ID matching
 *  - Error response handling
 *  - Process spawn / stop lifecycle
 *  - Reconnection logic (auto-reconnect on dead process, 3 retries)
 *  - Request timeout
 */

import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest";
import { EventEmitter } from "node:events";

// ---------------------------------------------------------------------------
// Mock setup — must happen before importing McpClient
// ---------------------------------------------------------------------------

vi.mock("node:child_process", () => ({ spawn: vi.fn() }));
vi.mock("node:fs", () => ({ existsSync: vi.fn().mockReturnValue(false) }));
vi.mock("node:path", () => ({
  resolve: vi.fn((...args: string[]) => args.filter(Boolean).join("/")),
}));

import { spawn } from "node:child_process";
import { McpClient, hasMemoryMd, isFirstRun } from "../src/mcp_client.js";

const spawnMock = spawn as unknown as Mock;

// ---------------------------------------------------------------------------
// MockProcess — simulates a tapps-brain-mcp child process
// ---------------------------------------------------------------------------

/**
 * A fake ChildProcess that captures stdin.write() calls and can emit
 * Content-Length–framed JSON-RPC responses on stdout.
 */
class MockProcess extends EventEmitter {
  stdout = new EventEmitter();
  stderr = new EventEmitter();
  stdin: { write: Mock };
  killed = false;

  private _autoRespondDefault: unknown = {};

  constructor() {
    super();
    const self = this;
    this.stdin = {
      write: vi.fn((data: string) => {
        // Parse the framed JSON-RPC message and optionally auto-respond.
        const headerEnd = data.indexOf("\r\n\r\n");
        if (headerEnd === -1) return;
        const body = data.slice(headerEnd + 4);
        try {
          const req = JSON.parse(body) as { id?: number };
          if (req.id !== undefined && self._autoRespondDefault !== undefined) {
            // Emit response asynchronously so Promise resolvers run in order.
            setImmediate(() => self.respondWith(req.id!, self._autoRespondDefault));
          }
        } catch {
          // ignore malformed JSON
        }
      }),
    };
  }

  /** Disable auto-responses (client will hang until manually responded to). */
  disableAutoRespond(): void {
    this._autoRespondDefault = undefined;
  }

  /** Emit a well-formed JSON-RPC success response for the given request id. */
  respondWith(id: number, result: unknown): void {
    const payload = JSON.stringify({ jsonrpc: "2.0", id, result });
    const framed = `Content-Length: ${Buffer.byteLength(payload)}\r\n\r\n${payload}`;
    this.stdout.emit("data", Buffer.from(framed));
  }

  /** Emit a well-formed JSON-RPC error response for the given request id. */
  respondWithError(id: number, code: number, message: string): void {
    const payload = JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } });
    const framed = `Content-Length: ${Buffer.byteLength(payload)}\r\n\r\n${payload}`;
    this.stdout.emit("data", Buffer.from(framed));
  }

  /** Simulate the process exiting unexpectedly. */
  simulateExit(code = 1): void {
    setImmediate(() => {
      this.emit("exit", code);
    });
  }

  /** Simulate a process error event. */
  simulateError(message: string): void {
    setImmediate(() => {
      this.emit("error", new Error(message));
    });
  }

  kill(): boolean {
    this.killed = true;
    this.emit("exit", 0);
    return true;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Access private sendRpc without going through callTool's retry loop. */
function sendRpcDirect(
  client: McpClient,
  method: string,
  params?: Record<string, unknown>,
): Promise<unknown> {
  type Internals = { sendRpc: (m: string, p?: Record<string, unknown>) => Promise<unknown> };
  return (client as unknown as Internals).sendRpc(method, params);
}

/** Read the current nextId from the client (next request id that will be assigned). */
function peekNextId(client: McpClient): number {
  return (client as unknown as { nextId: number }).nextId;
}

/**
 * Start a client backed by the given mock process.
 * The mock process must auto-respond so that the initialize handshake completes.
 */
async function startedClient(mockProcess: MockProcess): Promise<McpClient> {
  spawnMock.mockReturnValue(mockProcess);
  const client = new McpClient("/fake/project");
  await client.start();
  return client;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("McpClient — process lifecycle", () => {
  let proc: MockProcess;
  let client: McpClient;

  beforeEach(async () => {
    proc = new MockProcess();
    client = await startedClient(proc);
  });

  afterEach(() => {
    client.stop();
    vi.clearAllMocks();
  });

  it("spawns the MCP process with correct arguments", () => {
    expect(spawnMock).toHaveBeenCalledWith(
      "tapps-brain-mcp",
      ["--project-dir", "/fake/project"],
      expect.objectContaining({ stdio: ["pipe", "pipe", "pipe"] }),
    );
  });

  it("passes extraArgs to the spawned process", async () => {
    client.stop();
    vi.clearAllMocks();
    const newProc = new MockProcess();
    spawnMock.mockReturnValue(newProc);
    const c = new McpClient("/fake/project");
    await c.start("tapps-brain-mcp", ["--agent-id", "test-agent"]);
    expect(spawnMock).toHaveBeenCalledWith(
      "tapps-brain-mcp",
      ["--project-dir", "/fake/project", "--agent-id", "test-agent"],
      expect.anything(),
    );
    c.stop();
  });

  it("reports isRunning = true after start", () => {
    expect(client.isRunning).toBe(true);
  });

  it("reports isRunning = false after stop", () => {
    client.stop();
    expect(client.isRunning).toBe(false);
  });

  it("does not spawn a second process if already running", async () => {
    await client.start(); // second call — should be a no-op
    expect(spawnMock).toHaveBeenCalledTimes(1);
  });

  it("sets isRunning = false when the process exits unexpectedly", async () => {
    expect(client.isRunning).toBe(true);
    await new Promise<void>((resolve) => {
      proc.simulateExit(1);
      setImmediate(resolve);
    });
    // need one more tick for the exit event to propagate
    await new Promise<void>((r) => setImmediate(r));
    expect(client.isRunning).toBe(false);
  });

  it("exposes the projectDir getter", () => {
    const c = new McpClient("/my/project");
    expect(c.projectDir).toBe("/my/project");
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — MCP initialization handshake", () => {
  let proc: MockProcess;
  let client: McpClient;

  beforeEach(async () => {
    proc = new MockProcess();
    client = await startedClient(proc);
  });

  afterEach(() => {
    client.stop();
    vi.clearAllMocks();
  });

  it("sends an initialize JSON-RPC request on start", () => {
    const calls: string[] = proc.stdin.write.mock.calls.map(([data]: [string]) => data);
    const initCall = calls.find((d) => d.includes('"initialize"'));
    expect(initCall).toBeDefined();
    const headerEnd = initCall!.indexOf("\r\n\r\n");
    const body = JSON.parse(initCall!.slice(headerEnd + 4)) as {
      method: string;
      params: { protocolVersion: string };
    };
    expect(body.method).toBe("initialize");
    expect(body.params.protocolVersion).toBe("2024-11-05");
  });

  it("sends an initialized notification after the initialize response", () => {
    const calls: string[] = proc.stdin.write.mock.calls.map(([data]: [string]) => data);
    const notifCall = calls.find((d) => d.includes('"notifications/initialized"'));
    expect(notifCall).toBeDefined();
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — Content-Length framing", () => {
  let proc: MockProcess;
  let client: McpClient;

  beforeEach(async () => {
    proc = new MockProcess();
    client = await startedClient(proc);
  });

  afterEach(() => {
    client.stop();
    vi.clearAllMocks();
  });

  it("parses a single framed response correctly", async () => {
    const result = await client.callTool("memory_list", { limit: 5 });
    expect(result).toEqual({});
  });

  it("handles two responses delivered in the same chunk (batched data)", async () => {
    proc.disableAutoRespond();

    const id1 = peekNextId(client);
    const id2 = id1 + 1;

    const p1 = sendRpcDirect(client, "tools/call", { name: "memory_list", arguments: {} });
    const p2 = sendRpcDirect(client, "tools/call", { name: "memory_recall", arguments: {} });

    // Build two framed responses and emit them in a single chunk.
    const pay1 = JSON.stringify({ jsonrpc: "2.0", id: id1, result: "first" });
    const pay2 = JSON.stringify({ jsonrpc: "2.0", id: id2, result: "second" });
    const frame1 = `Content-Length: ${Buffer.byteLength(pay1)}\r\n\r\n${pay1}`;
    const frame2 = `Content-Length: ${Buffer.byteLength(pay2)}\r\n\r\n${pay2}`;
    proc.stdout.emit("data", Buffer.from(frame1 + frame2));

    const [r1, r2] = await Promise.all([p1, p2]);
    expect(r1).toBe("first");
    expect(r2).toBe("second");
  });

  it("skips a frame with malformed JSON body and continues with the next", async () => {
    proc.disableAutoRespond();

    const validId = peekNextId(client);
    const callPromise = sendRpcDirect(client, "tools/call", {});

    const badFrame = `Content-Length: 5\r\n\r\nhello`;
    const goodPayload = JSON.stringify({ jsonrpc: "2.0", id: validId, result: { ok: true } });
    const goodFrame = `Content-Length: ${Buffer.byteLength(goodPayload)}\r\n\r\n${goodPayload}`;
    proc.stdout.emit("data", Buffer.from(badFrame + goodFrame));

    const result = await callPromise;
    expect(result).toEqual({ ok: true });
  });

  it("skips a frame with no Content-Length header and continues with the next", async () => {
    proc.disableAutoRespond();

    const validId = peekNextId(client);
    const callPromise = sendRpcDirect(client, "tools/call", {});

    // Emit bad header (no Content-Length) followed by a valid frame.
    const badFrame = `BadHeader: 10\r\n\r\nhelloworld`;
    const goodPayload = JSON.stringify({ jsonrpc: "2.0", id: validId, result: 42 });
    const goodFrame = `Content-Length: ${Buffer.byteLength(goodPayload)}\r\n\r\n${goodPayload}`;
    proc.stdout.emit("data", Buffer.from(badFrame + goodFrame));

    const result = await callPromise;
    expect(result).toBe(42);
  });

  it("handles a response split across two data chunks", async () => {
    proc.disableAutoRespond();

    const validId = peekNextId(client);
    const callPromise = sendRpcDirect(client, "tools/call", {});

    const payload = JSON.stringify({ jsonrpc: "2.0", id: validId, result: "split" });
    const full = `Content-Length: ${Buffer.byteLength(payload)}\r\n\r\n${payload}`;
    const half = Math.floor(full.length / 2);

    proc.stdout.emit("data", Buffer.from(full.slice(0, half)));
    proc.stdout.emit("data", Buffer.from(full.slice(half)));

    const result = await callPromise;
    expect(result).toBe("split");
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — request / response ID matching", () => {
  let proc: MockProcess;
  let client: McpClient;

  beforeEach(async () => {
    proc = new MockProcess();
    client = await startedClient(proc);
  });

  afterEach(() => {
    client.stop();
    vi.clearAllMocks();
  });

  it("routes a response to the correct pending promise by id", async () => {
    proc.disableAutoRespond();

    const id1 = peekNextId(client);
    const id2 = id1 + 1;

    const p1 = sendRpcDirect(client, "tools/call", { name: "op1" });
    const p2 = sendRpcDirect(client, "tools/call", { name: "op2" });

    // Respond out-of-order: send id2 before id1.
    proc.respondWith(id2, { from: "second" });
    proc.respondWith(id1, { from: "first" });

    const [r1, r2] = await Promise.all([p1, p2]);
    expect(r1).toEqual({ from: "first" });
    expect(r2).toEqual({ from: "second" });
  });

  it("ignores a response with an unknown id", async () => {
    proc.disableAutoRespond();

    const id1 = peekNextId(client);
    const callPromise = sendRpcDirect(client, "tools/call", {});

    // Unknown id response first, then the real one.
    proc.respondWith(9999, { garbage: true });
    proc.respondWith(id1, { real: true });

    const result = await callPromise;
    expect(result).toEqual({ real: true });
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — error response handling", () => {
  let proc: MockProcess;
  let client: McpClient;

  beforeEach(async () => {
    proc = new MockProcess();
    client = await startedClient(proc);
  });

  afterEach(() => {
    client.stop();
    vi.clearAllMocks();
  });

  it("rejects the pending promise on a JSON-RPC error response", async () => {
    proc.disableAutoRespond();

    const id = peekNextId(client);
    // Use sendRpcDirect to bypass callTool's retry loop.
    const callPromise = sendRpcDirect(client, "tools/call", {
      name: "memory_list",
      arguments: {},
    });

    proc.respondWithError(id, -32601, "Method not found");

    await expect(callPromise).rejects.toThrow("MCP error -32601: Method not found");
  });

  it("rejects all pending requests when the process emits an error event", async () => {
    proc.disableAutoRespond();

    const p1 = sendRpcDirect(client, "tools/call", { name: "memory_list", arguments: {} });
    const p2 = sendRpcDirect(client, "tools/call", { name: "memory_recall", arguments: {} });

    proc.simulateError("ENOENT");

    await expect(p1).rejects.toThrow(/MCP process error.*ENOENT/);
    await expect(p2).rejects.toThrow(/MCP process error.*ENOENT/);
  });

  it("rejects all pending requests when the process exits unexpectedly", async () => {
    proc.disableAutoRespond();

    const p1 = sendRpcDirect(client, "tools/call", { name: "memory_list", arguments: {} });

    proc.simulateExit(1);

    await expect(p1).rejects.toThrow(/MCP process exited with code 1/);
  });

  it("throws immediately when sendRpc is called with no running process", async () => {
    client.stop();
    await expect(sendRpcDirect(client, "tools/call", {})).rejects.toThrow(
      "MCP process not running",
    );
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — request timeout", () => {
  let proc: MockProcess;
  let client: McpClient;

  beforeEach(async () => {
    proc = new MockProcess();
    client = await startedClient(proc);
    // Disable auto-respond AFTER start so the initialize handshake succeeds.
    proc.disableAutoRespond();
  });

  afterEach(() => {
    vi.useRealTimers();
    client.stop();
    vi.clearAllMocks();
  });

  it("rejects with a timeout error when no response arrives within 10s", async () => {
    vi.useFakeTimers();

    const callPromise = sendRpcDirect(client, "tools/call", {});
    const timeoutAssertion = expect(callPromise).rejects.toThrow(/timeout.*10000ms/);

    // Advance past the 10 s timeout.
    await vi.advanceTimersByTimeAsync(10_001);

    await timeoutAssertion;
  });

  it("does not reject before the 10s deadline", async () => {
    vi.useFakeTimers();

    let settled = false;
    const callPromise = sendRpcDirect(client, "tools/call", {}).then(
      () => {
        settled = true;
      },
      () => {
        settled = true;
      },
    );

    await vi.advanceTimersByTimeAsync(9_000);
    expect(settled).toBe(false);

    // Clean up — advance past the timeout so the promise settles.
    await vi.advanceTimersByTimeAsync(2_000);
    await callPromise;
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — reconnection logic", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("reconnects automatically when the process dies and retries the call", async () => {
    const firstProc = new MockProcess();
    spawnMock.mockReturnValueOnce(firstProc);
    const client = await startedClient(firstProc);

    // Kill the first process.
    firstProc.kill();
    await new Promise<void>((r) => setImmediate(r));
    expect(client.isRunning).toBe(false);

    // callTool should reconnect and succeed.
    const secondProc = new MockProcess();
    spawnMock.mockReturnValue(secondProc);

    const result = await client.callTool("memory_list", { limit: 1 });
    expect(result).toEqual({});
    expect(spawnMock).toHaveBeenCalledTimes(2);

    client.stop();
  });

  it("retries on failure and succeeds on a later attempt", async () => {
    // First spawn: initial start (auto-respond).
    const goodProc = new MockProcess();
    spawnMock.mockReturnValueOnce(goodProc);
    const client = new McpClient("/fake/project");
    await client.start();

    // Kill the first process.
    goodProc.kill();
    await new Promise<void>((r) => setImmediate(r));

    let reconnectCount = 0;
    spawnMock.mockImplementation(() => {
      reconnectCount++;
      if (reconnectCount < 3) {
        // First two reconnect attempts fail: process exits before initialize completes.
        const failProc = new MockProcess();
        failProc.disableAutoRespond();
        setImmediate(() => failProc.emit("exit", 1));
        return failProc;
      }
      // Third attempt succeeds.
      return new MockProcess();
    });

    const result = await client.callTool("memory_list", {});
    expect(result).toEqual({});
    expect(reconnectCount).toBe(3);

    client.stop();
  });

  it("throws after exhausting all retries when every reconnect fails", async () => {
    const firstProc = new MockProcess();
    spawnMock.mockReturnValueOnce(firstProc);
    const client = await startedClient(firstProc);

    // Kill the initial process.
    firstProc.kill();
    await new Promise<void>((r) => setImmediate(r));

    // ALL reconnect attempts fail: each spawned process exits immediately.
    spawnMock.mockImplementation(() => {
      const failProc = new MockProcess();
      failProc.disableAutoRespond();
      setImmediate(() => failProc.emit("exit", 1));
      return failProc;
    });

    await expect(client.callTool("memory_list", {})).rejects.toThrow();

    client.stop();
  });

  it("reconnect() clears the internal buffer and restarts successfully", async () => {
    const initialProc = new MockProcess();
    const newProc = new MockProcess();

    spawnMock
      .mockReturnValueOnce(initialProc) // initial start
      .mockReturnValue(newProc); // reconnect

    const client = new McpClient("/fake/project");
    await client.start();

    // Inject garbage into the buffer via stdout.
    initialProc.stdout.emit("data", Buffer.from("garbage without any header"));

    // Reconnect should clear the buffer and re-initialize.
    await client.reconnect();

    expect(client.isRunning).toBe(true);

    const result = await client.callTool("memory_list", {});
    expect(result).toEqual({});

    client.stop();
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — helper functions", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("hasMemoryMd returns true when MEMORY.md exists", async () => {
    const { existsSync } = await import("node:fs");
    (existsSync as Mock).mockReturnValue(true);
    expect(hasMemoryMd("/some/dir")).toBe(true);
  });

  it("hasMemoryMd returns false when MEMORY.md does not exist", async () => {
    const { existsSync } = await import("node:fs");
    (existsSync as Mock).mockReturnValue(false);
    expect(hasMemoryMd("/other/dir")).toBe(false);
  });

  it("isFirstRun returns true when .tapps-brain/ does not exist", async () => {
    const { existsSync } = await import("node:fs");
    (existsSync as Mock).mockReturnValue(false);
    expect(isFirstRun("/new/dir")).toBe(true);
  });

  it("isFirstRun returns false when .tapps-brain/ already exists", async () => {
    const { existsSync } = await import("node:fs");
    (existsSync as Mock).mockReturnValue(true);
    expect(isFirstRun("/old/dir")).toBe(false);
  });
});
