/**
 * Tests for McpClient (039 — SDK-based transport)
 *
 * Covers:
 *  - SDK transport + client creation with correct params
 *  - Process lifecycle (start / stop / reconnect)
 *  - Method delegation (callTool, readResource, callPrompt)
 *  - Session-invalidation reconnection on error
 *  - isRunning based on transport.pid
 *  - Helper functions (hasMemoryMd, isFirstRun)
 */

import { describe, it, expect, vi, afterEach, type Mock } from "vitest";

// ---------------------------------------------------------------------------
// Mock setup — vi.mock factories are hoisted, so they cannot reference
// variables declared outside. We store mock state on globalThis so both
// the factory and the test code can access it.
// ---------------------------------------------------------------------------

/* eslint-disable @typescript-eslint/no-explicit-any */
const _g = globalThis as any;

// Reset mock state before each import.
_g.__mcpMocks = {
  connect: vi.fn().mockResolvedValue(undefined),
  clientClose: vi.fn().mockResolvedValue(undefined),
  callTool: vi.fn().mockResolvedValue({ content: [] }),
  readResource: vi.fn().mockResolvedValue({ contents: [] }),
  getPrompt: vi.fn().mockResolvedValue({ messages: [] }),
  transportClose: vi.fn().mockResolvedValue(undefined),
  transportPid: 12345 as number | null,
  transportStderr: null as unknown,
  // Track constructor calls.
  ClientCtor: vi.fn(),
  TransportCtor: vi.fn(),
};

vi.mock("@modelcontextprotocol/sdk/client/index.js", () => {
  const g = globalThis as any;
  return {
    Client: class MockClient {
      connect: any;
      close: any;
      callTool: any;
      readResource: any;
      getPrompt: any;
      constructor(...args: any[]) {
        g.__mcpMocks.ClientCtor(...args);
        this.connect = g.__mcpMocks.connect;
        this.close = g.__mcpMocks.clientClose;
        this.callTool = g.__mcpMocks.callTool;
        this.readResource = g.__mcpMocks.readResource;
        this.getPrompt = g.__mcpMocks.getPrompt;
      }
    },
  };
});

vi.mock("@modelcontextprotocol/sdk/client/stdio.js", () => {
  const g = globalThis as any;
  return {
    StdioClientTransport: class MockStdioClientTransport {
      close: any;
      constructor(...args: any[]) {
        g.__mcpMocks.TransportCtor(...args);
        this.close = g.__mcpMocks.transportClose;
      }
      get pid() {
        return g.__mcpMocks.transportPid;
      }
      get stderr() {
        return g.__mcpMocks.transportStderr;
      }
    },
  };
});

vi.mock("node:fs", () => ({ existsSync: vi.fn().mockReturnValue(false) }));
vi.mock("node:path", () => ({
  resolve: vi.fn((...args: string[]) => args.filter(Boolean).join("/")),
}));
/* eslint-enable @typescript-eslint/no-explicit-any */

import { McpClient, hasMemoryMd, isFirstRun } from "../src/mcp_client.js";
import { extractMcpToolText } from "../src/mcp_tool_text.js";

// Shorthand accessors for the mock state.
function mocks() {
  return _g.__mcpMocks as {
    connect: Mock;
    clientClose: Mock;
    callTool: Mock;
    readResource: Mock;
    getPrompt: Mock;
    transportClose: Mock;
    transportPid: number | null;
    transportStderr: unknown;
    ClientCtor: Mock;
    TransportCtor: Mock;
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function startedClient(projectDir = "/fake/project"): Promise<McpClient> {
  const client = new McpClient(projectDir);
  await client.start();
  return client;
}

function resetMockState(): void {
  const m = mocks();
  m.transportPid = 12345;
  m.transportStderr = null;
  m.connect.mockResolvedValue(undefined);
  m.clientClose.mockResolvedValue(undefined);
  m.callTool.mockResolvedValue({ content: [] });
  m.readResource.mockResolvedValue({ contents: [] });
  m.getPrompt.mockResolvedValue({ messages: [] });
  m.transportClose.mockResolvedValue(undefined);
  vi.clearAllMocks();
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("McpClient — process lifecycle", () => {
  afterEach(resetMockState);

  it("creates StdioClientTransport with correct params", async () => {
    const client = await startedClient();
    expect(mocks().TransportCtor).toHaveBeenCalledWith({
      command: "tapps-brain-mcp",
      args: ["--project-dir", "/fake/project"],
      stderr: "pipe",
    });
    client.stop();
  });

  it("passes extraArgs to StdioClientTransport", async () => {
    const client = new McpClient("/fake/project");
    await client.start("tapps-brain-mcp", ["--agent-id", "test-agent"]);
    expect(mocks().TransportCtor).toHaveBeenCalledWith({
      command: "tapps-brain-mcp",
      args: ["--project-dir", "/fake/project", "--agent-id", "test-agent"],
      stderr: "pipe",
    });
    client.stop();
  });

  it("passes custom command to StdioClientTransport", async () => {
    const client = new McpClient("/fake/project");
    await client.start("custom-mcp-server");
    expect(mocks().TransportCtor).toHaveBeenCalledWith(
      expect.objectContaining({ command: "custom-mcp-server" }),
    );
    client.stop();
  });

  it("creates Client with correct name and version", async () => {
    const client = await startedClient();
    expect(mocks().ClientCtor).toHaveBeenCalledWith(
      { name: "tapps-brain-openclaw", version: "2.0.3" },
      {},
    );
    client.stop();
  });

  it("calls client.connect(transport) on start", async () => {
    const client = await startedClient();
    expect(mocks().connect).toHaveBeenCalledTimes(1);
    client.stop();
  });

  it("reports isRunning = true after start", async () => {
    const client = await startedClient();
    expect(client.isRunning).toBe(true);
    client.stop();
  });

  it("reports isRunning = false after stop", async () => {
    const client = await startedClient();
    client.stop();
    expect(client.isRunning).toBe(false);
  });

  it("does not create a second transport if already running", async () => {
    const client = await startedClient();
    await client.start(); // second call — should be a no-op
    expect(mocks().TransportCtor).toHaveBeenCalledTimes(1);
    expect(mocks().ClientCtor).toHaveBeenCalledTimes(1);
    client.stop();
  });

  it("exposes the projectDir getter", () => {
    const client = new McpClient("/my/project");
    expect(client.projectDir).toBe("/my/project");
  });

  it("reports isRunning = false when transport.pid is null (process died)", async () => {
    const client = await startedClient();
    expect(client.isRunning).toBe(true);
    mocks().transportPid = null; // Simulate process death.
    expect(client.isRunning).toBe(false);
    client.stop();
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — stop / close lifecycle", () => {
  afterEach(resetMockState);

  it("calls client.close() and transport.close() on stop", async () => {
    const client = await startedClient();
    client.stop();
    expect(mocks().clientClose).toHaveBeenCalledTimes(1);
    expect(mocks().transportClose).toHaveBeenCalledTimes(1);
  });

  it("swallows client.close() errors", async () => {
    const client = await startedClient();
    mocks().clientClose.mockRejectedValueOnce(new Error("close failed"));
    expect(() => client.stop()).not.toThrow();
  });

  it("swallows transport.close() errors", async () => {
    const client = await startedClient();
    mocks().transportClose.mockRejectedValueOnce(new Error("close failed"));
    expect(() => client.stop()).not.toThrow();
  });

  it("stop is safe to call multiple times", async () => {
    const client = await startedClient();
    client.stop();
    client.stop(); // second call — should not throw
    expect(mocks().clientClose).toHaveBeenCalledTimes(1);
  });

  it("stop when never started does not throw", () => {
    const client = new McpClient("/fake/project");
    expect(() => client.stop()).not.toThrow();
  });
});

// ---------------------------------------------------------------------------

describe("extractMcpToolText", () => {
  it("returns raw string unchanged", () => {
    expect(extractMcpToolText('{"a":1}')).toBe('{"a":1}');
  });

  it("concatenates text parts from MCP CallToolResult", () => {
    expect(
      extractMcpToolText({
        content: [
          { type: "text", text: '{"mem' },
          { type: "text", text: 'ories":[]}' },
        ],
      }),
    ).toBe('{"memories":[]}');
  });

  it("returns empty string for empty content array", () => {
    expect(extractMcpToolText({ content: [] })).toBe("");
  });
});

describe("McpClient — callTool delegation", () => {
  afterEach(resetMockState);

  it("delegates to client.callTool with correct params", async () => {
    const client = await startedClient();
    mocks().callTool.mockResolvedValueOnce({ content: [{ type: "text", text: "ok" }] });

    const result = await client.callTool("memory_list", { limit: 5 });

    expect(mocks().callTool).toHaveBeenCalledWith({
      name: "memory_list",
      arguments: { limit: 5 },
    });
    expect(result).toBe("ok");
    client.stop();
  });

  it("invalidates session on callTool error", async () => {
    const client = await startedClient();
    mocks().callTool.mockRejectedValueOnce(new Error("transport error"));

    await expect(client.callTool("memory_list", {})).rejects.toThrow("transport error");
    expect(client.isRunning).toBe(false);
    expect(mocks().clientClose).toHaveBeenCalledTimes(1);
    expect(mocks().transportClose).toHaveBeenCalledTimes(1);
  });

  it("reconnects when called while not running", async () => {
    const client = await startedClient();
    client.stop();
    vi.clearAllMocks();

    mocks().callTool.mockResolvedValueOnce({ content: [] });
    await client.callTool("memory_list", {});

    expect(mocks().TransportCtor).toHaveBeenCalledTimes(1);
    expect(mocks().ClientCtor).toHaveBeenCalledTimes(1);
    expect(mocks().connect).toHaveBeenCalledTimes(1);
    client.stop();
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — readResource delegation", () => {
  afterEach(resetMockState);

  it("delegates to client.readResource with correct params", async () => {
    const client = await startedClient();
    mocks().readResource.mockResolvedValueOnce({
      contents: [{ uri: "memory://stats", text: "{}" }],
    });

    const result = await client.readResource("memory://stats");

    expect(mocks().readResource).toHaveBeenCalledWith({ uri: "memory://stats" });
    expect(result).toEqual({
      contents: [{ uri: "memory://stats", text: "{}" }],
    });
    client.stop();
  });

  it("invalidates session on readResource error", async () => {
    const client = await startedClient();
    mocks().readResource.mockRejectedValueOnce(new Error("resource error"));

    await expect(client.readResource("memory://stats")).rejects.toThrow("resource error");
    expect(client.isRunning).toBe(false);
  });

  it("reconnects when called while not running", async () => {
    const client = await startedClient();
    client.stop();
    vi.clearAllMocks();

    mocks().readResource.mockResolvedValueOnce({ contents: [] });
    await client.readResource("memory://stats");

    expect(mocks().TransportCtor).toHaveBeenCalledTimes(1);
    expect(mocks().connect).toHaveBeenCalledTimes(1);
    client.stop();
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — callPrompt delegation", () => {
  afterEach(resetMockState);

  it("delegates to client.getPrompt with correct params", async () => {
    const client = await startedClient();
    mocks().getPrompt.mockResolvedValueOnce({
      messages: [{ role: "user", content: { type: "text", text: "hello" } }],
    });

    const result = await client.callPrompt("recall", { query: "test" });

    expect(mocks().getPrompt).toHaveBeenCalledWith({
      name: "recall",
      arguments: { query: "test" },
    });
    expect(result).toEqual({
      messages: [{ role: "user", content: { type: "text", text: "hello" } }],
    });
    client.stop();
  });

  it("invalidates session on callPrompt error", async () => {
    const client = await startedClient();
    mocks().getPrompt.mockRejectedValueOnce(new Error("prompt error"));

    await expect(client.callPrompt("recall", { query: "x" })).rejects.toThrow("prompt error");
    expect(client.isRunning).toBe(false);
  });

  it("reconnects when called while not running", async () => {
    const client = await startedClient();
    client.stop();
    vi.clearAllMocks();

    mocks().getPrompt.mockResolvedValueOnce({ messages: [] });
    await client.callPrompt("recall", { query: "test" });

    expect(mocks().TransportCtor).toHaveBeenCalledTimes(1);
    expect(mocks().connect).toHaveBeenCalledTimes(1);
    client.stop();
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — reconnection via session invalidation", () => {
  afterEach(resetMockState);

  it("reconnect() closes existing session and starts fresh", async () => {
    const client = await startedClient();
    vi.clearAllMocks();

    await client.reconnect();

    // Old session closed.
    expect(mocks().clientClose).toHaveBeenCalledTimes(1);
    expect(mocks().transportClose).toHaveBeenCalledTimes(1);
    // New session created.
    expect(mocks().TransportCtor).toHaveBeenCalledTimes(1);
    expect(mocks().ClientCtor).toHaveBeenCalledTimes(1);
    expect(mocks().connect).toHaveBeenCalledTimes(1);
    expect(client.isRunning).toBe(true);
    client.stop();
  });

  it("calling callTool after invalidation reconnects and succeeds", async () => {
    const client = await startedClient();

    // First call fails — invalidates session.
    mocks().callTool.mockRejectedValueOnce(new Error("connection lost"));
    await expect(client.callTool("memory_list", {})).rejects.toThrow("connection lost");
    expect(client.isRunning).toBe(false);

    // Second call should reconnect and succeed.
    vi.clearAllMocks();
    mocks().callTool.mockResolvedValueOnce({ content: [] });
    const result = await client.callTool("memory_list", {});

    expect(result).toBe("");
    expect(mocks().TransportCtor).toHaveBeenCalledTimes(1);
    expect(mocks().connect).toHaveBeenCalledTimes(1);
    client.stop();
  });

  it("reconnect preserves stored command and extraArgs", async () => {
    const client = new McpClient("/fake/project");
    await client.start("custom-mcp", ["--enable-hive"]);
    vi.clearAllMocks();

    await client.reconnect();

    expect(mocks().TransportCtor).toHaveBeenCalledWith({
      command: "custom-mcp",
      args: ["--project-dir", "/fake/project", "--enable-hive"],
      stderr: "pipe",
    });
    client.stop();
  });
});

// ---------------------------------------------------------------------------

describe("McpClient — stderr logging", () => {
  afterEach(resetMockState);

  it("attaches stderr listener when transport provides stderr stream", async () => {
    const stderrOn = vi.fn();
    mocks().transportStderr = { on: stderrOn };

    const client = await startedClient();

    expect(stderrOn).toHaveBeenCalledWith("data", expect.any(Function));
    client.stop();
  });

  it("logs stderr output to console.error", async () => {
    let capturedHandler: ((chunk: Buffer | string) => void) | null = null;
    mocks().transportStderr = {
      on: vi.fn((_event: string, handler: (chunk: Buffer | string) => void) => {
        capturedHandler = handler;
      }),
    };

    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const client = await startedClient();

    expect(capturedHandler).not.toBeNull();
    capturedHandler!(Buffer.from("test error message\n"));

    expect(spy).toHaveBeenCalledWith("[tapps-brain-mcp] test error message");
    spy.mockRestore();
    client.stop();
  });

  it("does not fail when stderr is null", async () => {
    mocks().transportStderr = null;

    const client = await startedClient();
    expect(client.isRunning).toBe(true);
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
