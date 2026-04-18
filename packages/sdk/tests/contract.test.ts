/**
 * Contract tests — assert TS SDK ↔ Python SDK parity on core endpoints.
 *
 * These tests require a live tapps-brain server. Set the following env vars
 * before running:
 *
 *   TAPPS_BRAIN_CONTRACT=1          # opt-in to run these tests
 *   TAPPS_BRAIN_URL=http://localhost:8080
 *   TAPPS_BRAIN_AUTH_TOKEN=<token>  # if the server requires auth
 *   TAPPS_BRAIN_PROJECT=contract-test-ts
 *
 * Run with:
 *   npm run test:contract
 *
 * In CI, run automatically when `TAPPS_BRAIN_URL` is set (see ts-sdk.yml).
 */

import { describe, it, expect, beforeAll } from "vitest";
import { TappsBrainClient } from "../src/client.js";
import { NotFoundError } from "../src/errors.js";

const RUN = Boolean(process.env["TAPPS_BRAIN_CONTRACT"]);

describe.skipIf(!RUN)("contract: TS SDK ↔ Python SDK parity", () => {
  let client: TappsBrainClient;
  const testKey = `ts-sdk-contract-${Date.now()}`;

  beforeAll(() => {
    client = new TappsBrainClient({
      url: process.env["TAPPS_BRAIN_URL"] ?? "http://localhost:8080",
      projectId: process.env["TAPPS_BRAIN_PROJECT"] ?? "contract-test-ts",
      agentId: "ts-sdk-contract-agent",
      authToken: process.env["TAPPS_BRAIN_AUTH_TOKEN"],
      maxRetries: 1,
    });
  });

  it("remember returns a non-empty key string", async () => {
    const key = await client.remember("TypeScript SDK contract test fact", {
      tier: "context",
    });
    expect(key).toBeTruthy();
    expect(typeof key).toBe("string");
  });

  it("memorySave + memoryGet round-trip", async () => {
    await client.memorySave(testKey, "contract test value", {
      tier: "context",
      tags: ["ts-sdk-test"],
    });

    const entry = await client.memoryGet(testKey);
    expect(entry["key"]).toBe(testKey);
    expect(entry["value"]).toBe("contract test value");
  });

  it("memorySearch finds the saved entry", async () => {
    const results = await client.memorySearch("contract test value");
    const found = results.find((r) => r.key === testKey);
    expect(found).toBeDefined();
  });

  it("recall returns an array (possibly empty)", async () => {
    const memories = await client.recall("TypeScript SDK contract test");
    expect(Array.isArray(memories)).toBe(true);
  });

  it("forget archives the entry", async () => {
    const forgotten = await client.forget(testKey);
    expect(forgotten).toBe(true);
  });

  it("status returns agent state object", async () => {
    const st = await client.status();
    expect(st).toBeDefined();
    expect(typeof st).toBe("object");
  });

  it("health returns a status object", async () => {
    const h = await client.health();
    expect(h).toBeDefined();
  });

  it("learnSuccess returns a key", async () => {
    const key = await client.learnSuccess("TypeScript SDK contract test passed");
    expect(key).toBeTruthy();
  });

  it("memoryGet for missing key raises NotFoundError or returns error", async () => {
    const missingKey = `ts-sdk-missing-${Date.now()}`;
    try {
      const entry = await client.memoryGet(missingKey);
      // Some implementations return an error field instead of HTTP 404
      expect(entry["error"] ?? entry["key"]).toBeDefined();
    } catch (err) {
      expect(err).toBeInstanceOf(NotFoundError);
    }
  });
});
