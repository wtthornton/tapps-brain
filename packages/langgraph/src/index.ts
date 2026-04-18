/**
 * @tapps-brain/langgraph — LangGraph BaseStore adapter for tapps-brain.
 *
 * Drop-in `BaseStore` compatible store backed by tapps-brain's
 * Postgres-persistent agent memory.
 *
 * @example
 * ```typescript
 * import { TappsBrainStore } from "@tapps-brain/langgraph";
 * import { StateGraph } from "@langchain/langgraph";
 *
 * const store = new TappsBrainStore({
 *   url: "http://brain.internal:8080",
 *   projectId: "my-langgraph-app",
 *   agentId: "graph-runner",
 *   authToken: process.env.TAPPS_BRAIN_AUTH_TOKEN,
 * });
 *
 * // Put an item
 * await store.put(["memories", "alice"], "task-notes", {
 *   content: "Alice prefers concise answers",
 * });
 *
 * // Get it back
 * const item = await store.get(["memories", "alice"], "task-notes");
 * console.log(item?.value.content); // "Alice prefers concise answers"
 *
 * // Search
 * const results = await store.search(["memories"], { query: "alice preferences" });
 *
 * // Use with a LangGraph StateGraph
 * const builder = new StateGraph(stateAnnotation);
 * builder.addNode("agent", callModel, { store });
 * ```
 *
 * @module @tapps-brain/langgraph
 */

export { TappsBrainStore } from "./store.js";

export type {
  Item,
  SearchItem,
  MatchCondition,
  GetOperation,
  SearchOperation,
  PutOperation,
  ListNamespacesOperation,
  Operation,
  OperationResult,
  OperationResults,
  TappsBrainStoreOptions,
} from "./types.js";
