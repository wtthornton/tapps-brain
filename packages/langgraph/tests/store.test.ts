/**
 * Unit tests for TappsBrainStore (LangGraph adapter).
 *
 * Mocks the underlying TappsBrainClient so no live server is required.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { TappsBrainStore } from "../src/store.js";
import type { TappsBrainClient } from "@tapps-brain/sdk";

// ---------------------------------------------------------------------------
// Mock factory
// ---------------------------------------------------------------------------

function makeMockClient(): TappsBrainClient {
  return {
    memorySave: vi.fn().mockResolvedValue({ saved: true, key: "k" }),
    memoryGet: vi.fn().mockResolvedValue({
      key: "ns1/ns2/item-key",
      value: JSON.stringify({ text: "stored value" }),
      tier: "context",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }),
    memorySearch: vi.fn().mockResolvedValue([]),
    forget: vi.fn().mockResolvedValue(true),
    close: vi.fn(),
  } as unknown as TappsBrainClient;
}

// We need to inject the mock client into the store. Patch the TappsBrainClient
// constructor via module mock — simpler: subclass for testing.
class TestStore extends TappsBrainStore {
  constructor(client: TappsBrainClient) {
    super({ url: "http://brain.test:8080", projectId: "test", agentId: "agent" });
    // Replace the private client with the mock via property assignment
    (this as unknown as Record<string, unknown>)["_client"] = client;
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

let mockClient: TappsBrainClient;
let store: TestStore;

beforeEach(() => {
  mockClient = makeMockClient();
  store = new TestStore(mockClient);
});

// ---------------------------------------------------------------------------
// put / get / delete
// ---------------------------------------------------------------------------

describe("put", () => {
  it("serialises the value to JSON and saves via memorySave", async () => {
    await store.put(["ns1", "ns2"], "item-key", { text: "hello" });

    expect(mockClient.memorySave).toHaveBeenCalledOnce();
    const [key, value] = (
      mockClient.memorySave as ReturnType<typeof vi.fn>
    ).mock.calls[0] as [string, string];
    expect(key).toBe("ns1/ns2/item-key");
    expect(JSON.parse(value)).toEqual({ text: "hello" });
  });

  it("calls forget when value is null (delete semantics)", async () => {
    await store.put(["ns1"], "item-key", null);

    expect(mockClient.forget).toHaveBeenCalledWith("ns1/item-key");
    expect(mockClient.memorySave).not.toHaveBeenCalled();
  });
});

describe("get", () => {
  it("returns an Item with the correct namespace and key", async () => {
    const item = await store.get(["ns1", "ns2"], "item-key");

    expect(item).not.toBeNull();
    expect(item!.key).toBe("item-key");
    expect(item!.namespace).toEqual(["ns1", "ns2"]);
    expect(item!.value).toEqual({ text: "stored value" });
    expect(item!.createdAt).toBeInstanceOf(Date);
    expect(item!.updatedAt).toBeInstanceOf(Date);
  });

  it("returns null when the entry has an error field", async () => {
    (mockClient.memoryGet as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      error: "not_found",
    });

    const item = await store.get(["ns1"], "missing");
    expect(item).toBeNull();
  });

  it("returns null when memoryGet throws", async () => {
    (mockClient.memoryGet as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("network error"),
    );

    const item = await store.get(["ns1"], "broken");
    expect(item).toBeNull();
  });
});

describe("delete", () => {
  it("calls forget with the composed key", async () => {
    await store.delete(["memories", "alice"], "prefs");

    expect(mockClient.forget).toHaveBeenCalledWith("memories/alice/prefs");
  });
});

// ---------------------------------------------------------------------------
// search
// ---------------------------------------------------------------------------

describe("search", () => {
  it("returns matching items filtered by namespace prefix", async () => {
    (mockClient.memorySearch as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      {
        key: "memories/alice/prefs",
        value: JSON.stringify({ color: "blue" }),
        tier: "context",
        score: 0.95,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
      {
        key: "other/ns/item",
        value: JSON.stringify({}),
        tier: "context",
        score: 0.3,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ]);

    const results = await store.search(["memories"], { query: "alice" });

    expect(results).toHaveLength(1);
    expect(results[0]!.key).toBe("prefs");
    expect(results[0]!.namespace).toEqual(["memories", "alice"]);
    expect(results[0]!.score).toBe(0.95);
    expect(results[0]!.value).toEqual({ color: "blue" });
  });

  it("returns empty array when no entries match prefix", async () => {
    (mockClient.memorySearch as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      { key: "other/ns/item", value: "{}", tier: "context", score: 0.1 },
    ]);

    const results = await store.search(["memories"], { query: "x" });
    expect(results).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// listNamespaces
// ---------------------------------------------------------------------------

describe("listNamespaces", () => {
  it("returns distinct namespaces from all stored keys", async () => {
    (mockClient.memorySearch as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      { key: "memories/alice/prefs" },
      { key: "memories/alice/tasks" },
      { key: "memories/bob/prefs" },
      { key: "checkpoints/run-1" },
    ]);

    const namespaces = await store.listNamespaces();
    // Should deduplicate ["memories","alice"] and include all unique prefixes
    const nsStrings = namespaces.map((ns) => ns.join("/"));
    expect(nsStrings).toContain("memories/alice");
    expect(nsStrings).toContain("memories/bob");
    expect(nsStrings).toContain("checkpoints");
    // ["memories","alice"] appears twice in entries but only once in result
    const aliceCount = nsStrings.filter((s) => s === "memories/alice").length;
    expect(aliceCount).toBe(1);
  });

  it("respects maxDepth option", async () => {
    (mockClient.memorySearch as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      { key: "a/b/c/d/item" },
    ]);

    const namespaces = await store.listNamespaces({ maxDepth: 2 });
    expect(namespaces[0]).toEqual(["a", "b"]);
  });
});

// ---------------------------------------------------------------------------
// batch
// ---------------------------------------------------------------------------

describe("batch", () => {
  it("dispatches Get, Search, Put, and ListNamespaces operations", async () => {
    (mockClient.memorySearch as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    const results = await store.batch([
      { namespace: ["ns1", "ns2"], key: "item-key" },       // GetOperation
      { namespacePrefix: ["ns1"], query: "hello" },          // SearchOperation
      { namespace: ["ns1"], key: "new-item", value: { x: 1 } }, // PutOperation
      {},                                                      // ListNamespacesOperation
    ] as const);

    expect(results).toHaveLength(4);
    // Get result
    expect(results[0]).not.toBeNull();
    // Search result
    expect(Array.isArray(results[1])).toBe(true);
    // Put result
    expect(results[2]).toBeUndefined();
    // ListNamespaces result
    expect(Array.isArray(results[3])).toBe(true);
  });
});
