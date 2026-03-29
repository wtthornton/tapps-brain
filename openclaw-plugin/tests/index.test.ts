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

// vi.mock is hoisted — registry on globalThis (same pattern as mcp_client.test.ts)
const _g = globalThis as {
  __tappsIndexMcpSingletons?: Map<string, { client: unknown; refCount: number }>;
};

// vi.mock is hoisted — McpClient mocked; acquireSingleton mirrors production ref-counting
vi.mock("../src/mcp_client.js", () => {
  const McpClient = vi.fn();
  return {
    McpClient,
    acquireSingleton(projectDir: string) {
      if (!_g.__tappsIndexMcpSingletons) {
        _g.__tappsIndexMcpSingletons = new Map();
      }
      const reg = _g.__tappsIndexMcpSingletons;
      let e = reg.get(projectDir);
      if (!e) {
        e = { client: new McpClient(projectDir), refCount: 0 };
        reg.set(projectDir, e);
      }
      e.refCount++;
      return e.client;
    },
    releaseSingleton(projectDir: string) {
      const reg = _g.__tappsIndexMcpSingletons;
      if (!reg) return;
      const e = reg.get(projectDir);
      if (!e) return;
      e.refCount--;
      if (e.refCount <= 0) {
        const c = e.client as { stop?: () => void };
        c.stop?.();
        reg.delete(projectDir);
      }
    },
    hasMemoryMd: vi.fn().mockReturnValue(false),
    isFirstRun: vi.fn().mockReturnValue(false),
  };
});

// Mock the OpenClaw SDK — not installed in test environment.
vi.mock("openclaw/plugin-sdk/core", () => ({
  delegateCompactionToRuntime: () =>
    Promise.resolve({ ok: true, compacted: true }),
}));
vi.mock("openclaw/plugin-sdk/plugin-entry", () => ({
  definePluginEntry: (def: Record<string, unknown>) => def,
}));

vi.mock("node:fs", () => ({
  readFileSync: vi.fn().mockReturnValue(""),
}));

// node:path is used by bootstrap() to resolve MEMORY.md path
vi.mock("node:path", () => ({
  resolve: vi.fn((...args: string[]) => args.filter(Boolean).join("/")),
}));

import { readFileSync } from "node:fs";
import {
  TappsBrainEngine,
  parseMemoryMdForImport,
} from "../src/index.js";
import type { PluginLogger } from "openclaw/plugin-sdk/core";
import { McpClient, hasMemoryMd, isFirstRun } from "../src/mcp_client.js";

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
    _g.__tappsIndexMcpSingletons?.clear();
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
      tokenBudget: 2000,
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
        tokenBudget: 2000,
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

    const result = await engine.compact({ sessionId: "s1", sessionFile: "/tmp/s1.json" });
    expect(result).toEqual({ ok: true, compacted: false, reason: "bootstrap_failed" });
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
    _g.__tappsIndexMcpSingletons?.clear();
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

    // logger.info should include elapsed time and capture count from memory_capture
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringMatching(/^\[tapps-brain\] ingest: \d+ms, captured=\d+$/),
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
      tokenBudget: 2000,
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
      tokenBudget: 2000,
    });

    expect(logger.info).toHaveBeenCalledWith(
      expect.stringMatching(/^\[tapps-brain\] assemble: .+\d+ms$/),
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

    const result = await engine.compact({ sessionId: "s1", sessionFile: "/tmp/s1.json" });

    // Flush fails gracefully, then delegates compaction to runtime
    expect(result).toMatchObject({ ok: true, compacted: true });
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringMatching(/^\[tapps-brain\] compact flush: Error: ingest failed$/),
    );
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

// ---------------------------------------------------------------------------
// 028-E: bootstrap — first-run import and Hive registration
// ---------------------------------------------------------------------------

describe("TappsBrainEngine — bootstrap first-run import (028-E)", () => {
  beforeEach(() => {
    vi.mocked(McpClient).mockImplementation(() => makeMockClient());
    vi.mocked(hasMemoryMd).mockReturnValue(false);
    vi.mocked(isFirstRun).mockReturnValue(false);
    vi.mocked(readFileSync).mockReturnValue("");
  });

  afterEach(() => {
    vi.resetAllMocks();
    _g.__tappsIndexMcpSingletons?.clear();
  });

  it("calls memory_import when isFirstRun and hasMemoryMd are both true", async () => {
    const mockCallTool = vi.fn().mockResolvedValue(JSON.stringify({ memories: [] }));
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );
    vi.mocked(isFirstRun).mockReturnValue(true);
    vi.mocked(hasMemoryMd).mockReturnValue(true);
    vi.mocked(readFileSync).mockReturnValue("# Architecture\nsome content\n");

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    expect(mockCallTool).toHaveBeenCalledWith(
      "memory_import",
      expect.objectContaining({ overwrite: false }),
    );
  });

  it("does NOT call memory_import when hasMemoryMd is false", async () => {
    const mockCallTool = vi.fn().mockResolvedValue(JSON.stringify({ memories: [] }));
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );
    vi.mocked(isFirstRun).mockReturnValue(true);
    vi.mocked(hasMemoryMd).mockReturnValue(false);

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    expect(mockCallTool).not.toHaveBeenCalledWith("memory_import", expect.anything());
  });

  it("does NOT call memory_import when isFirstRun is false", async () => {
    const mockCallTool = vi.fn().mockResolvedValue(JSON.stringify({ memories: [] }));
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );
    vi.mocked(isFirstRun).mockReturnValue(false);
    vi.mocked(hasMemoryMd).mockReturnValue(true);

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    expect(mockCallTool).not.toHaveBeenCalledWith("memory_import", expect.anything());
  });

  it("does NOT call memory_import when MEMORY.md has no parseable entries", async () => {
    const mockCallTool = vi.fn().mockResolvedValue(JSON.stringify({ memories: [] }));
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );
    vi.mocked(isFirstRun).mockReturnValue(true);
    vi.mocked(hasMemoryMd).mockReturnValue(true);
    // Content with only a heading and no body → no entries parsed
    vi.mocked(readFileSync).mockReturnValue("# Empty Heading\n");

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    expect(mockCallTool).not.toHaveBeenCalledWith("memory_import", expect.anything());
  });

  it("calls agent_register when hiveEnabled and agentId are set", async () => {
    const mockCallTool = vi.fn().mockResolvedValue(JSON.stringify({}));
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine(
      { hiveEnabled: true, agentId: "test-agent", profilePath: "repo-brain" },
      "/tmp/workspace",
    );
    await engine.bootstrap();

    expect(mockCallTool).toHaveBeenCalledWith("agent_register", {
      agent_id: "test-agent",
      profile: "repo-brain",
    });
  });

  it("does NOT call agent_register when hiveEnabled is false", async () => {
    const mockCallTool = vi.fn().mockResolvedValue(JSON.stringify({}));
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine(
      { hiveEnabled: false, agentId: "test-agent" },
      "/tmp/workspace",
    );
    await engine.bootstrap();

    expect(mockCallTool).not.toHaveBeenCalledWith("agent_register", expect.anything());
  });

  it("does NOT call agent_register when agentId is empty", async () => {
    const mockCallTool = vi.fn().mockResolvedValue(JSON.stringify({}));
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine(
      { hiveEnabled: true, agentId: "" },
      "/tmp/workspace",
    );
    await engine.bootstrap();

    expect(mockCallTool).not.toHaveBeenCalledWith("agent_register", expect.anything());
  });

  it("bootstrap succeeds even when agent_register throws (non-fatal)", async () => {
    const mockCallTool = vi
      .fn()
      .mockRejectedValue(new Error("already registered"));
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine(
      { hiveEnabled: true, agentId: "duplicate-agent" },
      "/tmp/workspace",
    );
    // Should NOT throw — agent_register error is swallowed intentionally
    await expect(engine.bootstrap()).resolves.toEqual({ bootstrapped: true });
  });
});

// ---------------------------------------------------------------------------
// 028-E: ingest — rate limiting and heartbeat skip
// ---------------------------------------------------------------------------

describe("TappsBrainEngine — ingest rate limiting and heartbeat (028-E)", () => {
  beforeEach(() => {
    vi.mocked(McpClient).mockImplementation(() => makeMockClient());
    vi.mocked(hasMemoryMd).mockReturnValue(false);
    vi.mocked(isFirstRun).mockReturnValue(false);
  });

  afterEach(() => {
    vi.resetAllMocks();
    _g.__tappsIndexMcpSingletons?.clear();
  });

  it("skips MCP call when isHeartbeat is true", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({ captureRateLimit: 1 }, "/tmp/workspace");
    await engine.bootstrap();

    const result = await engine.ingest({
      sessionId: "s1",
      message: { role: "user", content: "heartbeat message" },
      isHeartbeat: true,
    });

    expect(result).toEqual({ ingested: true });
    expect(mockCallTool).not.toHaveBeenCalled();
  });

  it("skips MCP call when message content is empty", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({ captureRateLimit: 1 }, "/tmp/workspace");
    await engine.bootstrap();

    await engine.ingest({
      sessionId: "s1",
      message: { role: "user", content: "   " },
    });

    expect(mockCallTool).not.toHaveBeenCalled();
  });

  it("calls MCP every 3rd call with default captureRateLimit=3", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace"); // default captureRateLimit=3
    await engine.bootstrap();

    const msg = { role: "user" as const, content: "content" };
    await engine.ingest({ sessionId: "s1", message: msg }); // count=1 — skip
    await engine.ingest({ sessionId: "s1", message: msg }); // count=2 — skip
    await engine.ingest({ sessionId: "s1", message: msg }); // count=3 — capture
    await engine.ingest({ sessionId: "s1", message: msg }); // count=4 — skip
    await engine.ingest({ sessionId: "s1", message: msg }); // count=5 — skip
    await engine.ingest({ sessionId: "s1", message: msg }); // count=6 — capture

    expect(mockCallTool).toHaveBeenCalledTimes(2);
    expect(mockCallTool).toHaveBeenCalledWith("memory_capture", expect.any(Object));
  });

  it("calls MCP on every call when captureRateLimit=1", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({ captureRateLimit: 1 }, "/tmp/workspace");
    await engine.bootstrap();

    const msg = { role: "user" as const, content: "data" };
    await engine.ingest({ sessionId: "s1", message: msg });
    await engine.ingest({ sessionId: "s1", message: msg });
    await engine.ingest({ sessionId: "s1", message: msg });

    expect(mockCallTool).toHaveBeenCalledTimes(3);
  });

  it("calls MCP on every call when captureRateLimit=0 (no rate limit)", async () => {
    // captureRateLimit=0 means the condition `captureRateLimit > 0 && ...` is false,
    // so the rate-limit skip never fires — every ingest call captures.
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({ captureRateLimit: 0 }, "/tmp/workspace");
    await engine.bootstrap();

    const msg = { role: "user" as const, content: "data" };
    for (let i = 0; i < 5; i++) {
      await engine.ingest({ sessionId: "s1", message: msg });
    }

    expect(mockCallTool).toHaveBeenCalledTimes(5);
  });

  it("maps user role to source=human", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({ captureRateLimit: 1 }, "/tmp/workspace");
    await engine.bootstrap();

    await engine.ingest({
      sessionId: "s1",
      message: { role: "user", content: "user message" },
    });

    expect(mockCallTool).toHaveBeenCalledWith(
      "memory_capture",
      expect.objectContaining({ source: "human" }),
    );
  });

  it("maps assistant role to source=agent", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({ captureRateLimit: 1 }, "/tmp/workspace");
    await engine.bootstrap();

    await engine.ingest({
      sessionId: "s1",
      message: { role: "assistant", content: "assistant response" },
    });

    expect(mockCallTool).toHaveBeenCalledWith(
      "memory_capture",
      expect.objectContaining({ source: "agent" }),
    );
  });

  it("uses agent_scope=hive when hiveEnabled=true", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine(
      { captureRateLimit: 1, hiveEnabled: true },
      "/tmp/workspace",
    );
    await engine.bootstrap();

    await engine.ingest({
      sessionId: "s1",
      message: { role: "user", content: "shared memory" },
    });

    expect(mockCallTool).toHaveBeenCalledWith(
      "memory_capture",
      expect.objectContaining({ agent_scope: "hive" }),
    );
  });

  it("uses agent_scope=private when hiveEnabled=false", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine(
      { captureRateLimit: 1, hiveEnabled: false },
      "/tmp/workspace",
    );
    await engine.bootstrap();

    await engine.ingest({
      sessionId: "s1",
      message: { role: "user", content: "private memory" },
    });

    expect(mockCallTool).toHaveBeenCalledWith(
      "memory_capture",
      expect.objectContaining({ agent_scope: "private" }),
    );
  });
});

// ---------------------------------------------------------------------------
// 028-E: assemble — recall injection, token budget, deduplication
// ---------------------------------------------------------------------------

describe("TappsBrainEngine — assemble recall injection (028-E)", () => {
  beforeEach(() => {
    vi.mocked(McpClient).mockImplementation(() => makeMockClient());
    vi.mocked(hasMemoryMd).mockReturnValue(false);
    vi.mocked(isFirstRun).mockReturnValue(false);
  });

  afterEach(() => {
    vi.resetAllMocks();
    _g.__tappsIndexMcpSingletons?.clear();
  });

  it("returns systemPromptAddition with recalled memories", async () => {
    const recallResponse = JSON.stringify({
      memories: [
        { key: "fact-1", value: "The sky is blue", tier: "architectural", confidence: 0.9 },
        { key: "fact-2", value: "Water is wet", tier: "pattern", confidence: 0.8 },
      ],
    });
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: vi.fn().mockResolvedValue(recallResponse) }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const result = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: "what do you know?" }],
      tokenBudget: 2000,
    });

    expect(result.systemPromptAddition).toBeDefined();
    expect(result.systemPromptAddition).toContain("## Relevant Memories");
    expect(result.systemPromptAddition).toContain("fact-1");
    expect(result.systemPromptAddition).toContain("The sky is blue");
    expect(result.estimatedTokens).toBeGreaterThan(0);
  });

  it("assemble parses MCP CallToolResult-shaped recall (text in content[])", async () => {
    const inner = JSON.stringify({
      memories: [{ key: "mcp-wrap", value: "from sdk", tier: "pattern", confidence: 0.9 }],
    });
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({
        callTool: vi.fn().mockResolvedValue({
          content: [{ type: "text", text: inner }],
        }),
      }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const result = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: "hello" }],
      tokenBudget: 2000,
    });

    expect(result.systemPromptAddition).toBeDefined();
    expect(result.systemPromptAddition).toContain("mcp-wrap");
    expect(result.systemPromptAddition).toContain("from sdk");
  });

  it("returns no systemPromptAddition when recall returns empty memories", async () => {
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({
        callTool: vi.fn().mockResolvedValue(JSON.stringify({ memories: [] })),
      }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const messages = [{ role: "user" as const, content: "test" }];
    const result = await engine.assemble({
      sessionId: "s1",
      messages,
      tokenBudget: 2000,
    });

    expect(result.systemPromptAddition).toBeUndefined();
    expect(result.estimatedTokens).toBe(0);
    expect(result.messages).toEqual(messages);
  });

  it("passes last 3 user messages as query to memory_recall", async () => {
    const mockCallTool = vi
      .fn()
      .mockResolvedValue(JSON.stringify({ memories: [] }));
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    await engine.assemble({
      sessionId: "s1",
      messages: [
        { role: "user", content: "first query" },
        { role: "assistant", content: "ignored response" },
        { role: "user", content: "second query" },
        { role: "user", content: "third query" },
        { role: "user", content: "fourth query" }, // only last 3 used
      ],
      tokenBudget: 2000,
    });

    expect(mockCallTool).toHaveBeenCalledWith(
      "memory_recall",
      expect.objectContaining({
        message: expect.stringContaining("second query"),
      }),
    );
    // "first query" should NOT be in the query (only last 3 user messages)
    const callArgs = mockCallTool.mock.calls[0][1] as { message: string };
    expect(callArgs.message).not.toContain("first query");
  });

  it("uses 'session context' fallback when no user messages", async () => {
    const mockCallTool = vi
      .fn()
      .mockResolvedValue(JSON.stringify({ memories: [] }));
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "assistant", content: "no user messages here" }],
      tokenBudget: 2000,
    });

    expect(mockCallTool).toHaveBeenCalledWith("memory_recall", {
      message: "session context",
    });
  });

  it("enforces token budget — truncates memories that exceed budget", async () => {
    // Create a large memory entry that would exceed a small budget
    const longValue = "x".repeat(500);
    const recallResponse = JSON.stringify({
      memories: [
        { key: "big-1", value: longValue, tier: "architectural", confidence: 0.9 },
        { key: "big-2", value: longValue, tier: "architectural", confidence: 0.9 },
        { key: "big-3", value: longValue, tier: "architectural", confidence: 0.9 },
        { key: "big-4", value: longValue, tier: "architectural", confidence: 0.9 },
      ],
    });
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: vi.fn().mockResolvedValue(recallResponse) }),
    );

    // tokenBudget=200 means char budget = 200*4 = 800
    const engine = new TappsBrainEngine({ tokenBudget: 200 }, "/tmp/workspace");
    await engine.bootstrap();

    const result = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: "test" }],
      tokenBudget: 200,
    });

    // Should not include all 4 entries due to budget
    if (result.systemPromptAddition) {
      // At most 1-2 entries should fit given the small budget
      const entryCount = (result.systemPromptAddition.match(/- \*\*/g) ?? []).length;
      expect(entryCount).toBeLessThan(4);
    }
  });

  it("deduplicates: does not re-inject memories already seen this session", async () => {
    const recallResponse = JSON.stringify({
      memories: [
        { key: "shared-fact", value: "Known fact", tier: "architectural" },
      ],
    });
    const mockCallTool = vi
      .fn()
      .mockResolvedValue(recallResponse);
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const messages = [{ role: "user" as const, content: "query" }];
    const params = { sessionId: "s1", messages, tokenBudget: { soft: 2000, hard: 4000 } };

    // First assemble — should inject the memory
    const first = await engine.assemble(params);
    expect(first.systemPromptAddition).toContain("shared-fact");

    // Second assemble — same key already in injectedKeys, should be filtered out
    const second = await engine.assemble(params);
    expect(second.systemPromptAddition).toBeUndefined();
    expect(second.estimatedTokens).toBe(0);
  });

  it("returns messages unchanged in all result shapes", async () => {
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({
        callTool: vi.fn().mockResolvedValue(JSON.stringify({ memories: [] })),
      }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const messages = [
      { role: "user" as const, content: "a" },
      { role: "assistant" as const, content: "b" },
    ];
    const result = await engine.assemble({
      sessionId: "s1",
      messages,
      tokenBudget: 2000,
    });

    // Messages must be passed through unchanged
    expect(result.messages).toEqual(messages);
  });
});

// ---------------------------------------------------------------------------
// 028-E: compact — context flush and session indexing
// ---------------------------------------------------------------------------

describe("TappsBrainEngine — compact context flush (028-E)", () => {
  beforeEach(() => {
    vi.mocked(McpClient).mockImplementation(() => makeMockClient());
    vi.mocked(hasMemoryMd).mockReturnValue(false);
    vi.mocked(isFirstRun).mockReturnValue(false);
  });

  afterEach(() => {
    vi.resetAllMocks();
    _g.__tappsIndexMcpSingletons?.clear();
  });

  it("returns ok immediately when no recent messages to flush", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    // No ingest calls — recentMessages is empty, delegates to runtime
    const result = await engine.compact({ sessionId: "s1", sessionFile: "/tmp/s1.json" });
    expect(result).toMatchObject({ ok: true, compacted: true });
    expect(mockCallTool).not.toHaveBeenCalledWith("memory_ingest", expect.anything());
  });

  it("calls memory_ingest with joined recent messages", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    // Ingest messages (captureRateLimit=3 default; just push to recentMessages)
    await engine.ingest({ sessionId: "s1", message: { role: "user", content: "msg one" } });
    await engine.ingest({ sessionId: "s1", message: { role: "user", content: "msg two" } });

    const result = await engine.compact({ sessionId: "s1", sessionFile: "/tmp/s1.json" });
    expect(result).toMatchObject({ ok: true, compacted: true });

    expect(mockCallTool).toHaveBeenCalledWith(
      "memory_ingest",
      expect.objectContaining({
        context: expect.stringContaining("msg one"),
        source: "compaction",
      }),
    );
  });

  it("calls memory_index_session with sessionId and message chunks", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    await engine.ingest({ sessionId: "s1", message: { role: "user", content: "chunk a" } });
    await engine.ingest({ sessionId: "s1", message: { role: "user", content: "chunk b" } });

    await engine.compact({ sessionId: "session-xyz", sessionFile: "/tmp/xyz.json" });

    expect(mockCallTool).toHaveBeenCalledWith(
      "memory_index_session",
      expect.objectContaining({
        session_id: "session-xyz",
        chunks: expect.arrayContaining(["chunk a", "chunk b"]),
      }),
    );
  });

  it("clears recentMessages after successful compact", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    await engine.ingest({ sessionId: "s1", message: { role: "user", content: "remember this" } });

    // First compact — should flush
    await engine.compact({ sessionId: "s1", sessionFile: "/tmp/s1.json" });
    const firstIngestCalls = mockCallTool.mock.calls.filter(
      (call) => call[0] === "memory_ingest",
    ).length;
    expect(firstIngestCalls).toBe(1);

    // Second compact — recentMessages is empty, should not flush again
    await engine.compact({ sessionId: "s1", sessionFile: "/tmp/s1.json" });
    const secondIngestCalls = mockCallTool.mock.calls.filter(
      (call) => call[0] === "memory_ingest",
    ).length;
    expect(secondIngestCalls).toBe(1); // unchanged
  });

  it("skips memory_index_session when sessionId is empty", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    await engine.ingest({ sessionId: "", message: { role: "user", content: "data" } });

    // compact with no sessionId (empty string is falsy)
    await engine.compact({ sessionId: "", sessionFile: "/tmp/empty.json" });

    expect(mockCallTool).not.toHaveBeenCalledWith("memory_index_session", expect.anything());
    expect(mockCallTool).toHaveBeenCalledWith("memory_ingest", expect.anything());
  });

  it("uses agent_scope=hive in compact when hiveEnabled=true", async () => {
    const mockCallTool = vi.fn().mockResolvedValue("{}");
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({ hiveEnabled: true }, "/tmp/workspace");
    await engine.bootstrap();

    await engine.ingest({ sessionId: "s1", message: { role: "user", content: "shared" } });
    await engine.compact({ sessionId: "s1", sessionFile: "/tmp/s1.json" });

    expect(mockCallTool).toHaveBeenCalledWith(
      "memory_ingest",
      expect.objectContaining({ agent_scope: "hive" }),
    );
  });
});

// ---------------------------------------------------------------------------
// 028-E: parseMemoryMdForImport — heading→tier mapping, slugify, edge cases
// ---------------------------------------------------------------------------

describe("parseMemoryMdForImport — heading tier mapping (028-E)", () => {
  it("maps H1 to architectural tier", () => {
    const entries = parseMemoryMdForImport("# System Design\nContent about architecture\n");
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ tier: "architectural", key: "system-design" });
  });

  it("maps H2 to architectural tier", () => {
    const entries = parseMemoryMdForImport("## Database Schema\nSchema content\n");
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ tier: "architectural", key: "database-schema" });
  });

  it("maps H3 to pattern tier", () => {
    const entries = parseMemoryMdForImport("### Authentication Flow\nAuth flow description\n");
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ tier: "pattern", key: "authentication-flow" });
  });

  it("maps H4 to procedural tier", () => {
    const entries = parseMemoryMdForImport("#### Deploy Steps\nStep 1: build\n");
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ tier: "procedural", key: "deploy-steps" });
  });

  it("maps H5 and H6 to procedural tier", () => {
    const content = "##### Sub Step\nbody\n\n###### Deep Step\nbody2\n";
    const entries = parseMemoryMdForImport(content);
    expect(entries).toHaveLength(2);
    expect(entries[0].tier).toBe("procedural");
    expect(entries[1].tier).toBe("procedural");
  });

  it("returns empty array for empty content", () => {
    expect(parseMemoryMdForImport("")).toHaveLength(0);
  });

  it("returns empty array when content has no headings", () => {
    expect(parseMemoryMdForImport("Just some plain text\nno headings here")).toHaveLength(0);
  });

  it("skips entries with no body content", () => {
    // Heading immediately followed by another heading — no body for first
    const content = "# First Heading\n# Second Heading\nActual content\n";
    const entries = parseMemoryMdForImport(content);
    expect(entries).toHaveLength(1);
    expect(entries[0].key).toBe("second-heading");
  });

  it("slugifies heading text correctly", () => {
    const entries = parseMemoryMdForImport("## My Component Name\nsome content\n");
    expect(entries[0].key).toBe("my-component-name");
  });

  it("strips leading/trailing hyphens from slug", () => {
    const entries = parseMemoryMdForImport("## --- Heading ---\nsome content\n");
    expect(entries[0].key).not.toMatch(/^-|-$/);
  });

  it("skips entries where slug is empty (all non-alphanumeric heading)", () => {
    const entries = parseMemoryMdForImport("## !!! ###\nsome content\n");
    expect(entries).toHaveLength(0);
  });

  it("parses multiple headings into separate entries", () => {
    const content = [
      "# Architecture",
      "System uses SQLite",
      "## Patterns",
      "BM25 for search",
      "### Procedures",
      "Run pytest to test",
    ].join("\n");

    const entries = parseMemoryMdForImport(content);
    expect(entries).toHaveLength(3);
    expect(entries[0]).toMatchObject({ key: "architecture", tier: "architectural" });
    expect(entries[1]).toMatchObject({ key: "patterns", tier: "architectural" });
    expect(entries[2]).toMatchObject({ key: "procedures", tier: "pattern" });
  });

  it("trims whitespace from entry values", () => {
    const entries = parseMemoryMdForImport("# Key\n\n  padded content  \n\n");
    expect(entries[0].value).toBe("padded content");
  });

  it("preserves multi-line body content", () => {
    const content = "# Multi\nLine 1\nLine 2\nLine 3\n";
    const entries = parseMemoryMdForImport(content);
    expect(entries[0].value).toContain("Line 1");
    expect(entries[0].value).toContain("Line 2");
    expect(entries[0].value).toContain("Line 3");
  });
});

// ---------------------------------------------------------------------------
// 028-E: dispose — cleanup
// ---------------------------------------------------------------------------

describe("TappsBrainEngine — dispose (028-E)", () => {
  beforeEach(() => {
    vi.mocked(McpClient).mockImplementation(() => makeMockClient());
    vi.mocked(hasMemoryMd).mockReturnValue(false);
    vi.mocked(isFirstRun).mockReturnValue(false);
  });

  afterEach(() => {
    vi.resetAllMocks();
    _g.__tappsIndexMcpSingletons?.clear();
  });

  it("calls mcpClient.stop() on dispose", async () => {
    const mockStop = vi.fn();
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ stop: mockStop }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    await engine.dispose();
    expect(mockStop).toHaveBeenCalledOnce();
  });
});

// ---------------------------------------------------------------------------
// 028-F: citations — citation footers in assemble() output
// ---------------------------------------------------------------------------

describe("TappsBrainEngine — citations in assemble() (028-F)", () => {
  const recallMemories = [
    { key: "my-key", value: "some value", tier: "architectural", confidence: 0.9 },
  ];

  beforeEach(() => {
    vi.mocked(hasMemoryMd).mockReturnValue(false);
    vi.mocked(isFirstRun).mockReturnValue(false);
  });

  afterEach(() => {
    vi.resetAllMocks();
    _g.__tappsIndexMcpSingletons?.clear();
  });

  it("appends citation footer when citations is 'auto' (default)", async () => {
    const mockCallTool = vi.fn().mockResolvedValue(
      JSON.stringify({ memories: recallMemories }),
    );
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    // Default config — citations defaults to "auto"
    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const result = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: "test query" }],
      tokenBudget: 2000,
    });

    expect(result.systemPromptAddition).toBeDefined();
    expect(result.systemPromptAddition).toContain("my-key");
    expect(result.systemPromptAddition).toContain(
      "Source: memory/architectural/my-key.md",
    );
  });

  it("appends citation footer when citations is 'on'", async () => {
    const mockCallTool = vi.fn().mockResolvedValue(
      JSON.stringify({ memories: recallMemories }),
    );
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({ citations: "on" }, "/tmp/workspace");
    await engine.bootstrap();

    const result = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: "test query" }],
      tokenBudget: 2000,
    });

    expect(result.systemPromptAddition).toContain(
      "Source: memory/architectural/my-key.md",
    );
  });

  it("omits citation footer when citations is 'off'", async () => {
    const mockCallTool = vi.fn().mockResolvedValue(
      JSON.stringify({ memories: recallMemories }),
    );
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({ citations: "off" }, "/tmp/workspace");
    await engine.bootstrap();

    const result = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: "test query" }],
      tokenBudget: 2000,
    });

    expect(result.systemPromptAddition).toBeDefined();
    expect(result.systemPromptAddition).toContain("my-key");
    expect(result.systemPromptAddition).not.toContain("Source:");
  });

  it("uses 'procedural' as fallback tier when tier field is missing", async () => {
    const memoriesNoTier = [{ key: "no-tier-key", value: "value without tier" }];
    const mockCallTool = vi.fn().mockResolvedValue(
      JSON.stringify({ memories: memoriesNoTier }),
    );
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const result = await engine.assemble({
      sessionId: "s1",
      messages: [{ role: "user", content: "test query" }],
      tokenBudget: 2000,
    });

    expect(result.systemPromptAddition).toContain(
      "Source: memory/procedural/no-tier-key.md",
    );
  });
});

// ---------------------------------------------------------------------------
// 028-G: searchWithSessionMemory — session memory search integration
// ---------------------------------------------------------------------------

describe("TappsBrainEngine — searchWithSessionMemory (028-G)", () => {
  beforeEach(() => {
    vi.mocked(McpClient).mockImplementation(() => makeMockClient());
    vi.mocked(hasMemoryMd).mockReturnValue(false);
    vi.mocked(isFirstRun).mockReturnValue(false);
  });

  afterEach(() => {
    vi.resetAllMocks();
    _g.__tappsIndexMcpSingletons?.clear();
  });

  // -------------------------------------------------------------------------
  // scope "memory" — default behaviour
  // -------------------------------------------------------------------------

  it('scope "memory" calls memory_recall and returns results with source "memory"', async () => {
    const mockCallTool = vi.fn().mockResolvedValue(
      JSON.stringify({
        memories: [
          { key: "fact-1", value: "Important fact", tier: "architectural", confidence: 0.9 },
        ],
      }),
    );
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const results = await engine.searchWithSessionMemory({
      query: "important",
      scope: "memory",
    });

    expect(mockCallTool).toHaveBeenCalledWith("memory_recall", expect.objectContaining({ message: "important" }));
    expect(mockCallTool).not.toHaveBeenCalledWith("memory_search_sessions", expect.anything());
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({
      key: "fact-1",
      value: "Important fact",
      tier: "architectural",
      confidence: 0.9,
      source: "memory",
    });
  });

  it('defaults to scope "memory" when scope is not specified', async () => {
    const mockCallTool = vi.fn().mockResolvedValue(
      JSON.stringify({ memories: [{ key: "k", value: "v" }] }),
    );
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    await engine.searchWithSessionMemory({ query: "test" });

    expect(mockCallTool).toHaveBeenCalledWith("memory_recall", expect.anything());
    expect(mockCallTool).not.toHaveBeenCalledWith("memory_search_sessions", expect.anything());
  });

  // -------------------------------------------------------------------------
  // scope "session" — session index only
  // -------------------------------------------------------------------------

  it('scope "session" calls memory_search_sessions and returns results with source "session"', async () => {
    const mockCallTool = vi.fn().mockResolvedValue(
      JSON.stringify({
        sessions: [
          { session_id: "sess-abc", chunk: "User asked about X", score: 0.75 },
        ],
      }),
    );
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const results = await engine.searchWithSessionMemory({
      query: "asked about X",
      scope: "session",
    });

    expect(mockCallTool).toHaveBeenCalledWith("memory_search_sessions", expect.objectContaining({ query: "asked about X" }));
    expect(mockCallTool).not.toHaveBeenCalledWith("memory_recall", expect.anything());
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({
      key: "sess-abc",
      value: "User asked about X",
      confidence: 0.75,
      source: "session",
    });
  });

  it('scope "session" falls back to key "session" when session_id is missing', async () => {
    const mockCallTool = vi.fn().mockResolvedValue(
      JSON.stringify({ sessions: [{ chunk: "some chunk" }] }),
    );
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const results = await engine.searchWithSessionMemory({ query: "q", scope: "session" });

    expect(results[0]).toMatchObject({ key: "session", value: "some chunk", source: "session" });
  });

  // -------------------------------------------------------------------------
  // scope "all" — merged results
  // -------------------------------------------------------------------------

  it('scope "all" queries both stores and merges results, memory first', async () => {
    const mockCallTool = vi.fn().mockImplementation((tool: string) => {
      if (tool === "memory_recall") {
        return Promise.resolve(
          JSON.stringify({
            memories: [{ key: "long-term", value: "LT fact", tier: "pattern" }],
          }),
        );
      }
      if (tool === "memory_search_sessions") {
        return Promise.resolve(
          JSON.stringify({
            sessions: [{ session_id: "sess-1", chunk: "Session chunk", score: 0.6 }],
          }),
        );
      }
      return Promise.resolve("{}");
    });
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const results = await engine.searchWithSessionMemory({ query: "test", scope: "all" });

    expect(mockCallTool).toHaveBeenCalledWith("memory_recall", expect.anything());
    expect(mockCallTool).toHaveBeenCalledWith("memory_search_sessions", expect.anything());

    // Memory results come first
    expect(results[0]).toMatchObject({ key: "long-term", source: "memory" });
    expect(results[1]).toMatchObject({ key: "sess-1", source: "session" });
  });

  it('scope "all" merges correctly when one store returns empty results', async () => {
    const mockCallTool = vi.fn().mockImplementation((tool: string) => {
      if (tool === "memory_recall") {
        return Promise.resolve(JSON.stringify({ memories: [] }));
      }
      if (tool === "memory_search_sessions") {
        return Promise.resolve(
          JSON.stringify({ sessions: [{ session_id: "s", chunk: "only session" }] }),
        );
      }
      return Promise.resolve("{}");
    });
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    const results = await engine.searchWithSessionMemory({ query: "q", scope: "all" });

    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({ source: "session" });
  });

  // -------------------------------------------------------------------------
  // limit parameter
  // -------------------------------------------------------------------------

  it("passes limit to both MCP tools", async () => {
    const mockCallTool = vi.fn().mockResolvedValue(JSON.stringify({ memories: [], sessions: [] }));
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await engine.bootstrap();

    await engine.searchWithSessionMemory({ query: "q", scope: "all", limit: 5 });

    expect(mockCallTool).toHaveBeenCalledWith("memory_recall", expect.objectContaining({ limit: 5 }));
    expect(mockCallTool).toHaveBeenCalledWith("memory_search_sessions", expect.objectContaining({ limit: 5 }));
  });

  // -------------------------------------------------------------------------
  // graceful fallbacks
  // -------------------------------------------------------------------------

  it("returns empty array when bootstrap failed", async () => {
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ start: vi.fn().mockRejectedValue(new Error("startup fail")) }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace");
    await expect(engine.bootstrap()).rejects.toThrow("startup fail");

    const results = await engine.searchWithSessionMemory({ query: "test", scope: "all" });
    expect(results).toEqual([]);
  });

  it("logs warning and returns partial results when memory_recall throws in scope all", async () => {
    const mockLogger = makeMockLogger();
    const mockCallTool = vi.fn().mockImplementation((tool: string) => {
      if (tool === "memory_recall") {
        return Promise.reject(new Error("recall error"));
      }
      return Promise.resolve(
        JSON.stringify({ sessions: [{ session_id: "s", chunk: "session only" }] }),
      );
    });
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace", mockLogger);
    await engine.bootstrap();

    const results = await engine.searchWithSessionMemory({ query: "q", scope: "all" });

    expect(mockLogger.warn).toHaveBeenCalledWith(
      "[tapps-brain] searchWithSessionMemory (memory):",
      expect.any(Error),
    );
    // Session results still returned despite memory failure
    expect(results).toHaveLength(1);
    expect(results[0].source).toBe("session");
  });

  it("logs warning and returns partial results when memory_search_sessions throws in scope all", async () => {
    const mockLogger = makeMockLogger();
    const mockCallTool = vi.fn().mockImplementation((tool: string) => {
      if (tool === "memory_recall") {
        return Promise.resolve(
          JSON.stringify({ memories: [{ key: "k", value: "v" }] }),
        );
      }
      return Promise.reject(new Error("session error"));
    });
    vi.mocked(McpClient).mockImplementationOnce(() =>
      makeMockClient({ callTool: mockCallTool }),
    );

    const engine = new TappsBrainEngine({}, "/tmp/workspace", mockLogger);
    await engine.bootstrap();

    const results = await engine.searchWithSessionMemory({ query: "q", scope: "all" });

    expect(mockLogger.warn).toHaveBeenCalledWith(
      "[tapps-brain] searchWithSessionMemory (session):",
      expect.any(Error),
    );
    // Memory results still returned despite session failure
    expect(results).toHaveLength(1);
    expect(results[0].source).toBe("memory");
  });
});


// ---------------------------------------------------------------------------
// Issue #9 — tool name conflict regression
// ---------------------------------------------------------------------------

describe("Issue #9 — tapps_memory_search and tapps_memory_get do not conflict with builtins", () => {
  it("default export registers tapps_memory_search, not memory_search", async () => {
    /**
     * Verifies that the plugin registers tools named tapps_memory_search and
     * tapps_memory_get (not memory_search / memory_get) to avoid collisions
     * with OpenClaw built-in memory tools.
     */
    const registeredTools: string[] = [];
    const mockApi = {
      pluginConfig: {},
      config: {},
      id: "test-agent",
      logger: makeMockLogger(),
      runtime: {
        agent: {
          resolveAgentWorkspaceDir: () => "/tmp/workspace",
        },
      },
      registerTool: (tool: { name: string }) => {
        registeredTools.push(tool.name);
      },
      registerContextEngine: vi.fn(),
    };

    // Import default export to call register()
    const pluginModule = await import("../src/index.js");
    const entry = pluginModule.default;

    // register() bootstraps async in the background — we only need the sync part
    vi.mocked(McpClient).mockImplementationOnce(() => makeMockClient());
    (entry as { register: (api: typeof mockApi) => void }).register(mockApi as unknown as Parameters<typeof entry.register>[0]);

    // Must have the renamed tools
    expect(registeredTools).toContain("tapps_memory_search");
    expect(registeredTools).toContain("tapps_memory_get");

    // Must NOT have the conflicting names
    expect(registeredTools).not.toContain("memory_search");
    expect(registeredTools).not.toContain("memory_get");
  });
});
