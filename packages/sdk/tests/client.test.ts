/**
 * Unit tests for TappsBrainClient.
 *
 * Uses Vitest's `vi.stubGlobal` to mock the global `fetch` so no live
 * tapps-brain server is required for these tests.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { TappsBrainClient } from "../src/client.js";
import {
  AuthError,
  BrainDegradedError,
  ProjectNotFoundError,
  RateLimitError,
} from "../src/errors.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeMcpResponse(toolResult: unknown, status = 200): Response {
  const body = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    result: {
      content: [{ type: "text", text: JSON.stringify(toolResult) }],
    },
  });
  return new Response(body, {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeErrorResponse(
  status: number,
  error: string,
  message: string,
): Response {
  return new Response(JSON.stringify({ error, message }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// ---------------------------------------------------------------------------
// Test setup
// ---------------------------------------------------------------------------

let client: TappsBrainClient;

beforeEach(() => {
  client = new TappsBrainClient({
    url: "http://brain.test:8080",
    projectId: "test-project",
    agentId: "test-agent",
    authToken: "test-token",
    maxRetries: 0, // no retries in unit tests
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  client.close();
});

// ---------------------------------------------------------------------------
// Request shape verification
// ---------------------------------------------------------------------------

describe("request shape", () => {
  it("sends MCP JSON-RPC tools/call to /mcp", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      makeMcpResponse({ key: "abc-123" }),
    );
    vi.stubGlobal("fetch", fetchSpy);

    await client.remember("Use ruff for linting");

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://brain.test:8080/mcp");
    expect(init.method).toBe("POST");

    const headers = init.headers as Record<string, string>;
    expect(headers["X-Project-Id"]).toBe("test-project");
    expect(headers["X-Tapps-Agent"]).toBe("test-agent");
    expect(headers["Authorization"]).toBe("Bearer test-token");
    expect(headers["X-Idempotency-Key"]).toBeTruthy(); // auto-generated for writes

    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.jsonrpc).toBe("2.0");
    expect(body.method).toBe("tools/call");
    const params = body.params as Record<string, unknown>;
    expect(params.name).toBe("brain_remember");
    const args = params.arguments as Record<string, unknown>;
    expect(args.fact).toBe("Use ruff for linting");
    expect(args.tier).toBe("procedural");
  });

  it("does NOT send idempotency key for read-only tools", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(makeMcpResponse([]));
    vi.stubGlobal("fetch", fetchSpy);

    await client.recall("linting conventions");

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Idempotency-Key"]).toBeUndefined();
  });

  it("omits Authorization header when no authToken", async () => {
    // Temporarily clear the env var so it doesn't leak into the client under test.
    const savedToken = process.env["TAPPS_BRAIN_AUTH_TOKEN"];
    delete process.env["TAPPS_BRAIN_AUTH_TOKEN"];

    const noAuthClient = new TappsBrainClient({
      url: "http://brain.test:8080",
      projectId: "p",
      agentId: "a",
      maxRetries: 0,
    });
    const fetchSpy = vi.fn().mockResolvedValue(makeMcpResponse([]));
    vi.stubGlobal("fetch", fetchSpy);

    try {
      await noAuthClient.recall("test");

      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      const headers = init.headers as Record<string, string>;
      expect(headers["Authorization"]).toBeUndefined();
    } finally {
      if (savedToken !== undefined) {
        process.env["TAPPS_BRAIN_AUTH_TOKEN"] = savedToken;
      }
      noAuthClient.close();
    }
  });
});

// ---------------------------------------------------------------------------
// remember
// ---------------------------------------------------------------------------

describe("remember", () => {
  it("returns the key from the server response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(makeMcpResponse({ key: "use-ruff-for-linting" })),
    );

    const key = await client.remember("Use ruff for linting");
    expect(key).toBe("use-ruff-for-linting");
  });

  it("passes tier, share, shareWith options", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(makeMcpResponse({ key: "k" }));
    vi.stubGlobal("fetch", fetchSpy);

    await client.remember("fact", { tier: "architectural", share: true, shareWith: "hive" });

    const body = JSON.parse(
      (fetchSpy.mock.calls[0] as [string, RequestInit])[1].body as string,
    ) as Record<string, unknown>;
    const args = (body.params as Record<string, unknown>).arguments as Record<string, unknown>;
    expect(args.tier).toBe("architectural");
    expect(args.share).toBe(true);
    expect(args.share_with).toBe("hive");
  });
});

// ---------------------------------------------------------------------------
// recall
// ---------------------------------------------------------------------------

describe("recall", () => {
  it("returns an array of memory entries", async () => {
    const memories = [
      { key: "ruff-linting", value: "Use ruff", tier: "pattern", confidence: 0.9 },
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeMcpResponse(memories)));

    const result = await client.recall("linting");
    expect(result).toHaveLength(1);
    expect(result[0]?.key).toBe("ruff-linting");
  });

  it("returns empty array when no memories", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeMcpResponse([])));

    const result = await client.recall("nothing");
    expect(result).toEqual([]);
  });

  it("passes maxResults option", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(makeMcpResponse([]));
    vi.stubGlobal("fetch", fetchSpy);

    await client.recall("query", { maxResults: 10 });

    const body = JSON.parse(
      (fetchSpy.mock.calls[0] as [string, RequestInit])[1].body as string,
    ) as Record<string, unknown>;
    const args = (body.params as Record<string, unknown>).arguments as Record<string, unknown>;
    expect(args.max_results).toBe(10);
  });
});

// ---------------------------------------------------------------------------
// forget
// ---------------------------------------------------------------------------

describe("forget", () => {
  it("returns true when memory is archived", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(makeMcpResponse({ forgotten: true })),
    );

    const result = await client.forget("old-key");
    expect(result).toBe(true);
  });

  it("returns false when memory not found", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(makeMcpResponse({ forgotten: false })),
    );

    const result = await client.forget("missing-key");
    expect(result).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// learnSuccess / learnFailure
// ---------------------------------------------------------------------------

describe("learnSuccess", () => {
  it("returns the key on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(makeMcpResponse({ key: "task-success-abc" })),
    );

    const key = await client.learnSuccess("Deployed the feature", { taskId: "T-1" });
    expect(key).toBe("task-success-abc");
  });
});

describe("learnFailure", () => {
  it("returns the key on failure record", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(makeMcpResponse({ key: "task-failure-xyz" })),
    );

    const key = await client.learnFailure("Build failed", {
      taskId: "T-2",
      error: "TypeError: ...",
    });
    expect(key).toBe("task-failure-xyz");
  });
});

// ---------------------------------------------------------------------------
// memorySave / memoryGet / memorySearch / memoryReinforce
// ---------------------------------------------------------------------------

describe("memorySave", () => {
  it("calls memory_save with key and value", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      makeMcpResponse({ saved: true, key: "my-key" }),
    );
    vi.stubGlobal("fetch", fetchSpy);

    const result = await client.memorySave("my-key", "my-value", {
      tier: "pattern",
      tags: ["foo", "bar"],
    });

    expect(result["saved"]).toBe(true);
    const body = JSON.parse(
      (fetchSpy.mock.calls[0] as [string, RequestInit])[1].body as string,
    ) as Record<string, unknown>;
    const args = (body.params as Record<string, unknown>).arguments as Record<string, unknown>;
    expect(args.key).toBe("my-key");
    expect(args.value).toBe("my-value");
    expect(args.tier).toBe("pattern");
    expect(args.tags).toEqual(["foo", "bar"]);
  });
});

describe("memoryGet", () => {
  it("returns the entry by key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        makeMcpResponse({ key: "my-key", value: "my-value", tier: "pattern" }),
      ),
    );

    const entry = await client.memoryGet("my-key");
    expect(entry["key"]).toBe("my-key");
  });
});

describe("memorySearch", () => {
  it("returns entries matching the query", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        makeMcpResponse([{ key: "k1", value: "v1" }]),
      ),
    );

    const results = await client.memorySearch("query text");
    expect(results).toHaveLength(1);
  });
});

describe("memoryReinforce", () => {
  it("calls memory_reinforce with confidenceBoost", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      makeMcpResponse({ reinforced: true }),
    );
    vi.stubGlobal("fetch", fetchSpy);

    await client.memoryReinforce("my-key", { confidenceBoost: 0.1 });

    const body = JSON.parse(
      (fetchSpy.mock.calls[0] as [string, RequestInit])[1].body as string,
    ) as Record<string, unknown>;
    const args = (body.params as Record<string, unknown>).arguments as Record<string, unknown>;
    expect(args.key).toBe("my-key");
    expect(args.confidence_boost).toBe(0.1);
  });
});

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

describe("error handling", () => {
  it("raises AuthError on 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        makeErrorResponse(401, "UNAUTHORIZED", "Auth required"),
      ),
    );

    await expect(client.recall("test")).rejects.toBeInstanceOf(AuthError);
  });

  it("raises ProjectNotFoundError on 403 PROJECT_NOT_REGISTERED", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: "PROJECT_NOT_REGISTERED",
            message: "Not registered",
            project_id: "test-project",
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(client.recall("test")).rejects.toBeInstanceOf(
      ProjectNotFoundError,
    );
  });

  it("raises RateLimitError on 429", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        makeErrorResponse(429, "RATE_LIMITED", "Too many requests"),
      ),
    );

    await expect(client.recall("test")).rejects.toBeInstanceOf(RateLimitError);
  });

  it("raises BrainDegradedError on 503", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        makeErrorResponse(503, "BRAIN_DEGRADED", "Service unavailable"),
      ),
    );

    await expect(client.recall("test")).rejects.toBeInstanceOf(
      BrainDegradedError,
    );
  });

  it("retries on BrainDegradedError when maxRetries > 0", async () => {
    const retryClient = new TappsBrainClient({
      url: "http://brain.test:8080",
      projectId: "p",
      agentId: "a",
      maxRetries: 1,
      timeoutMs: 5000,
    });

    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(
        makeErrorResponse(503, "BRAIN_DEGRADED", "degraded"),
      )
      .mockResolvedValueOnce(makeMcpResponse([]));

    vi.stubGlobal("fetch", fetchSpy);

    const result = await retryClient.recall("test");
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(result).toEqual([]);

    retryClient.close();
  });
});

// ---------------------------------------------------------------------------
// status / health
// ---------------------------------------------------------------------------

describe("status and health", () => {
  it("calls brain_status tool", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      makeMcpResponse({ agent_id: "test-agent", memory_count: 42 }),
    );
    vi.stubGlobal("fetch", fetchSpy);

    const result = await client.status();
    expect(result["agent_id"]).toBe("test-agent");
  });

  it("calls tapps_brain_health tool", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      makeMcpResponse({ status: "ok" }),
    );
    vi.stubGlobal("fetch", fetchSpy);

    const result = await client.health();
    expect(result["status"]).toBe("ok");
  });
});
