# Core Call Flows

This document maps the dominant runtime call paths as implemented now.

## 1) Memory save flow

### CLI entry

- `tapps-brain memory save` -> `memory_save_cmd` in `cli.py` -> `store.save`

### MCP entry

- `memory_save` tool in `mcp_server.py` -> `store.save`

### Store pipeline (simplified)

1. Validate scope/source/tier/agent scope and normalize tier/group values.
2. Safety checks and sanitization (`safety.py`).
3. Dedup fast path and reinforcement when matching normalized content exists.
4. Optional contradiction/invalidation handling.
5. Build/merge `MemoryEntry`, compute integrity hash, enforce max-entry cap.
6. Persist write-through to SQLite via `MemoryPersistence.save`.
7. Optional Hive propagation via `PropagationEngine`.
8. Relation extraction/persistence.
9. Optional auto-consolidation trigger.

## 2) Recall flow

### CLI entry

- `tapps-brain recall` -> `store.recall` in `store.py`

### MCP entry

- `memory_recall` tool in `mcp_server.py` -> `store.recall`

### Recall pipeline (simplified)

1. `MemoryStore.recall` routes to `RecallOrchestrator.recall`.
2. `RecallOrchestrator` calls `inject_memories`.
3. `inject_memories` uses `MemoryRetriever.search`.
4. Retriever executes BM25/FTS and optional vector-hybrid search.
5. Composite scoring and filtering applied.
6. Injection formatting applies safety and token budget.
7. Optional Hive merge (local + `universal` + profile namespace), re-sort.
8. Optional post-filters (scope/tier/branch/group/dedupe).
9. Return `RecallResult` with metadata and diagnostics.

## 3) Hive propagation flow

### Write side

- Save path carries `agent_scope` (`private`, `domain`, `hive`).
- `PropagationEngine.propagate` resolves effective scope using profile rules:
  - `private_tiers` force private
  - `auto_propagate_tiers` can promote private -> domain
- Destination namespace:
  - `hive` -> `universal`
  - `domain` -> profile namespace

### Read side

- Recall queries local store first.
- If Hive attached, recall queries Hive namespaces and merges weighted results.

## 4) Federation flow

- Federation is explicit and sync-oriented.
- Projects publish selected entries to federated hub.
- Projects pull synced entries from subscriptions.
- No automatic background cross-project propagation in core path.

## 5) Maintenance flow

### Migrate

- `maintenance migrate` opens store; migrations run on store open.

### Consolidation

- `maintenance consolidate` -> consolidation scan/merge routines.

### GC

- `maintenance gc` -> stale candidate detection -> archive -> delete.

### Health and diagnostics

- `maintenance health` -> `store.health`
- diagnostics/flywheel commands -> deterministic quality loop services
