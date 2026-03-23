/**
 * Tests for TappsBrainEngine — bootstrap race condition fix (028-A)
 *
 * Verifies that concurrent calls to ingest/assemble/compact before
 * bootstrap completes are correctly queued (await this.ready) and
 * return graceful fallbacks when bootstrap fails.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// vi.mock is hoisted — all new McpClient() calls use this mock
vi.mock("../src/mcp_client.js", () => ({
  McpClient: vi.fn(),
  hasMemoryMd: vi.fn().mockReturnValue(false),
  isFirstRun: vi.fn().mockReturnValue(false),
}));

import { TappsBrainEngine } from "../src/index.js";
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
