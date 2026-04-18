/**
 * @tapps-brain/sdk — TypeScript SDK for tapps-brain persistent agent memory.
 *
 * The primary export is {@link TappsBrainClient}. All types and error classes
 * are also re-exported for convenience.
 *
 * @example
 * ```typescript
 * import { TappsBrainClient } from "@tapps-brain/sdk";
 *
 * const brain = new TappsBrainClient({
 *   url: "http://brain.internal:8080",
 *   projectId: "my-project",
 *   agentId: "my-agent",
 *   authToken: process.env.TAPPS_BRAIN_AUTH_TOKEN,
 * });
 *
 * // Save a fact
 * const key = await brain.remember("Prefer ruff over pylint for linting", {
 *   tier: "pattern",
 * });
 *
 * // Recall relevant memories
 * const memories = await brain.recall("linting conventions");
 * for (const m of memories) {
 *   console.log(`[${m.tier}] ${m.key}: ${m.value}`);
 * }
 *
 * // Record outcomes for future reinforcement
 * await brain.learnSuccess("Fixed the ruff lint issue");
 *
 * await brain.close();
 * ```
 *
 * @module @tapps-brain/sdk
 */

export { TappsBrainClient } from "./client.js";

export type {
  TappsBrainClientOptions,
  MemoryEntry,
  RecallResult,
  MemoryTier,
  MemorySource,
  AgentScope,
  RememberOptions,
  RecallOptions,
  ForgetOptions,
  LearnSuccessOptions,
  LearnFailureOptions,
  MemorySaveOptions,
  MemorySearchOptions,
  MemoryRecallOptions,
  MemoryReinforceOptions,
  MemorySaveManyOptions,
  MemoryRecallManyOptions,
} from "./types.js";

export {
  TappsBrainError,
  AuthError,
  ProjectNotFoundError,
  NotFoundError,
  IdempotencyConflictError,
  RateLimitError,
  InvalidRequestError,
  BrainDegradedError,
  InternalError,
} from "./errors.js";
