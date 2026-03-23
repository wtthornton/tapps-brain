/**
 * Tests for TappsBrainEngine
 *
 * 028-A: Verifies that concurrent calls to ingest/assemble/compact before
 * bootstrap completes are correctly queued (await this.ready) and
 * return graceful fallbacks when bootstrap fails.
 *
 * 028-B: Verifies structured error logging — silent catch blocks replaced
 * with logger.warn() calls. Also verifies elapsed_ms timing in ingest/assemble.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// vi.mock is hoisted — all new McpClient() calls use this mock
vi.mock("../src/mcp_client.js", () => ({
  McpClient: vi.fn(),
  hasMemoryMd: vi.fn().mockReturnValue(false),
  isFirstRun: vi.fn().mockReturnValue(false),
}));

import { TappsBrainEngine, type PluginLogger } from "../src/index.js";
import { McpClient } from "../src/mcp_client.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Minimal McpClient mock factory with overrideable methods. */
function makeMockClient(overrides: {
  start?: () => Promise<void>;
  callTool?: (tool: string, args: Record<string, unknown>) => Promise<unknown>;
  stop?: () => void;
} = {}): InstanceType<typeof McpClient> {
  return {
    start: vi.fn().mockResolvedValue(undefined),
    callTool: vi
      .fn()
      .mockResolvedValue(JSON.stringify({ memories: [] })),
    stop: vi.fn(),
    ...overrides,
  } as unknown as InstanceType<typeof McpClient>;
}

/** Minimal logger mock for testing warning calls. */
function makeMockLogger(): PluginLogger & {
  info: ReturnType<typeof vi.fn>;
  warn: ReturnType<typeof vi.fn>;
} {
  return { info: vi.fn(), warn: vi.fn() };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TappsBrainEngine — bootstrap race condition (028-A)", () => {
  beforeEach(() => {
    vi.mocked(McpClient).mockImplementation(() => makeMockClient());
  });

  afterEach(() => {
    vi.resetAllMocks();
  });

  // -------------------------------------------------------------------------
  // ingest() waits for bootstrap
  // -------------------------------------------------------------------------

  it("ingest waits for bootstrap to complete before calling MCP", async () => {
    // Simulate a slow bootstrap by controlling when start() resolves
    let resolveStart!: () => void;
    const slowStart = new Promise<void>((resolve) => {
      resolveStart = resolve;
    });

    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ start: vi.fn().mockReturnValue(slowStart) }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");

    // Start bootstrap without awaiting — it's blocked on slowStart
    const bootstrapPromise = engine.bootstrap();

    // Call ingest concurrently
    let ingestResolved = false;
    const ingestPromise = engine
      .ingest({
        sessionId: "s1",
        message: { role: "user", content: "important fact to remember" },
      })
      .then((r) => {
        ingestResolved = true;
        return r;
      });

    // Allow microtasks to run — ingest should still be blocked on this.ready
    await new Promise<void>((r) => setTimeout(r, 10));
    expect(ingestResolved).toBe(false);

    // Unblock bootstrap
    resolveStart();
    await bootstrapPromise;

    // Now ingest should be able to proceed and complete
    const result = await ingestPromise;
    expect(ingestResolved).toBe(true);
    expect(result).toEqual({ ingested: true });
  });

  it("ingest returns graceful fallback when bootstrap fails", async () => {
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({
        start: vi.fn().mockRejectedValue(new Error("MCP process failed")),
      }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");

    // Bootstrap fails — error propagates to caller
    await expect(engine.bootstrap()).rejects.toThrow("MCP process failed");

    // ingest must not throw — graceful fallback
    const result = await engine.ingest({
      sessionId: "s1",
      message: { role: "user", content: "hello world" },
    });
    expect(result).toEqual({ ingested: true });
  });

  // -------------------------------------------------------------------------
  // assemble() waits for bootstrap
  // -------------------------------------------------------------------------

  it("assemble returns empty result when bootstrap fails", async () => {
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({
        start: vi.fn().mockRejectedValue(new Error("MCP start error")),
      }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await expect(engine.bootstrap()).rejects.toThrow("MCP start error");

    const messages = [{ role: "user" as const, content: "hello" }];
    const result = await engine.assemble({
      sessionId: "s1",
      messages,
      tokenBudget: { soft: 2000, hard: 4000 },
    });

    expect(result).toEqual({ messages, estimatedTokens: 0 });
  });

  it("assemble waits for bootstrap before recalling memories", async () => {
    let resolveStart!: () => void;
    const slowStart = new Promise<void>((resolve) => {
      resolveStart = resolve;
    });

    const mockCallTool = vi
      .fn()
      .mockResolvedValue(JSON.stringify({ memories: [] }));

    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({
        start: vi.fn().mockReturnValue(slowStart),
        callTool: mockCallTool,
      }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    const bootstrapPromise = engine.bootstrap();

    let assembleResolved = false;
    const assemblePromise = engine
      .assemble({
        sessionId: "s1",
        messages: [{ role: "user", content: "what do you remember?" }],
        tokenBudget: { soft: 2000, hard: 4000 },
      })
      .then((r) => {
        assembleResolved = true;
        return r;
      });

    // Should be blocked on bootstrap
    await new Promise<void>((r) => setTimeout(r, 10));
    expect(assembleResolved).toBe(false);
    expect(mockCallTool).not.toHaveBeenCalled();

    // Unblock
    resolveStart();
    await bootstrapPromise;
    await assemblePromise;

    expect(assembleResolved).toBe(true);
    // callTool should have been called once for memory_recall
    expect(mockCallTool).toHaveBeenCalledWith("memory_recall", expect.any(Object));
  });

  // -------------------------------------------------------------------------
  // compact() waits for bootstrap
  // -------------------------------------------------------------------------

  it("compact returns ok when bootstrap fails", async () => {
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({
        start: vi.fn().mockRejectedValue(new Error("MCP start error")),
      }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await expect(engine.bootstrap()).rejects.toThrow("MCP start error");

    const result = await engine.compact({ sessionId: "s1" });
    expect(result).toEqual({ ok: true, compacted: true });
  });

  // -------------------------------------------------------------------------
  // Normal path — bootstrap succeeds, hooks work correctly
  // -------------------------------------------------------------------------

  it("ingest works normally after successful bootstrap", async () => {
    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const result = await engine.ingest({
      sessionId: "s1",
      message: { role: "user", content: "important thing" },
    });
    expect(result).toEqual({ ingested: true });
  });
});

// ---------------------------------------------------------------------------
// 028-B: Structured error logging
// ---------------------------------------------------------------------------

describe("TappsBrainEngine — structured error logging (028-B)", () => {
  beforeEach(() => {
    vi.mocked(McpClient).mockImplementation(() => makeMockClient());
  });

  afterEach(() => {
    vi.resetAllMocks();
  });

  // -------------------------------------------------------------------------
  // ingest() — logger.warn on MCP error
  // -------------------------------------------------------------------------

  it("ingest calls logger.warn when memory_capture throws", async () => {
    const mcpError = new Error("capture failed");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({
        callTool: vi.fn().mockRejectedValue(mcpError),
      }),
    );

    const logger = makeMockLogger();
    const engine = new TappsBrainEngine({ captureRateLimit: 1 }, "/tmp/workspace", logger);
    await engine.bootstrap();

    // captureRateLimit=1 ensures every call triggers MCP
    const result = await engine.ingest({
      sessionId: "s1",
      message: { role: "user", content: "something important" },
    });

    // Graceful fallback — never throws
    expect(result).toEqual({ ingested: true });
    // logger.warn must be called with hook name and error
    expect(logger.warn).toHaveBeenCalledWith("[tapps-brain] ingest:", mcpError);
  });

  it("ingest calls logger.info with elapsed_ms on success", async () => {
    const logger = makeMockLogger();
    const engine = new TappsBrainEngine({ captureRateLimit: 1 }, "/tmp/workspace", logger);
    await engine.bootstrap();

    await engine.ingest({
      sessionId: "s1",
      message: { role: "user", content: "another message" },
    });

    // logger.info should have been called with elapsed_ms
    expect(logger.info).toHaveBeenCalledWith(
      "[tapps-brain] ingest:",
      expect.objectContaining({ elapsed_ms: expect.any(Number) }),
    );
  });

  // -------------------------------------------------------------------------
  // assemble() — logger.warn on MCP error
  // -------------------------------------------------------------------------

  it("assemble calls logger.warn when memory_recall throws", async () => {
    const mcpError = new Error("recall failed");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({
        callTool: vi.fn().mockRejectedValue(mcpError),
      }),
    );

    const logger = makeMockLogger();
    const engine = new TappsBrainEngine({}, "/tmp/workspace", logger);
    await engine.bootstrap();

    const messages = [{ role: "user" as const, content: "what do you know?" }];
    const result = await engine.assemble({
      sessionId: "s1",
      messages,
      tokenBudget: { soft: 2000, hard: 4000 },
    });

    // Graceful fallback — never throws, returns messages unchanged
    expect(result).toEqual({ messages, estimatedTokens: 0 });
    expect(logger.warn).toHaveBeenCalledWith("[tapps-brain] assemble:", mcpError);
  });

  it("assemble calls logger.info with elapsed_ms on success", async () => {
    const logger = makeMockLogger();
    const engine = new TappsBrainEngine({}, "/tmp/workspace", logger);
    await engine.bootstrap();

    const messages = [{ role: "user" as const, content: "hello" }];
    await engine.assemble({
      sessionId: "s1",
      messages,
      tokenBudget: { soft: 2000, hard: 4000 },
    });

    expect(logger.info).toHaveBeenCalledWith(
      "[tapps-brain] assemble:",
      expect.objectContaining({ elapsed_ms: expect.any(Number) }),
    );
  });

  // -------------------------------------------------------------------------
  // compact() — logger.warn on MCP error
  // -------------------------------------------------------------------------

  it("compact calls logger.warn when memory_ingest throws", async () => {
    const mcpError = new Error("ingest failed");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({
        callTool: vi.fn().mockRejectedValue(mcpError),
      }),
    );

    const logger = makeMockLogger();
    const engine = new TappsBrainEngine({}, "/tmp/workspace", logger);
    await engine.bootstrap();

    // Push a message so compact() has something to flush
    await engine.ingest({
      sessionId: "s1",
      // captureRateLimit defaults to 3 so this won't try MCP during ingest
      message: { role: "user", content: "message to compact" },
      isHeartbeat: false,
    });

    const result = await engine.compact({ sessionId: "s1" });

    // Graceful fallback — never throws
    expect(result).toEqual({ ok: true, compacted: true });
    expect(logger.warn).toHaveBeenCalledWith("[tapps-brain] compact:", mcpError);
  });

  // -------------------------------------------------------------------------
  // Default no-op logger (no logger provided)
  // -------------------------------------------------------------------------

  it("uses no-op logger when no logger is provided (does not throw)", async () => {
    const mcpError = new Error("silent failure");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({
        callTool: vi.fn().mockRejectedValue(mcpError),
      }),
    );

    // No logger passed — should not throw even when MCP fails
    const engine = new TappsBrainEngine({ captureRateLimit: 1 }, "/tmp/workspace");
    await engine.bootstrap();

    await expect(
      engine.ingest({
        sessionId: "s1",
        message: { role: "user", content: "test message" },
      }),
    ).resolves.toEqual({ ingested: true });
  });
});
