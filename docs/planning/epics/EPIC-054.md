---
id: EPIC-054
title: "Hive Backend Abstraction Layer — pluggable storage for shared state"
status: planned
priority: high
created: 2026-04-08
tags: [hive, federation, abstraction, postgres, sqlite]
---

# EPIC-054: Hive Backend Abstraction Layer

## Context

`HiveStore` and `FederatedStore` are tightly coupled to SQLite. Every method directly executes SQL against a local `.db` file. This works for single-host development but breaks when agents span multiple Docker containers or hosts — SQLite WAL mode requires all readers/writers on the same local filesystem.

The target architecture keeps **local agent memory on SQLite** (fast, embedded, isolated) but moves **Hive and Federation to PostgreSQL** (network-native, concurrent, multi-host). To get there without a big-bang rewrite, this epic introduces a backend abstraction layer: protocol interfaces that both SQLite and Postgres can implement.

**Design principle:** tapps-brain callers (`MemoryStore`, `PropagationEngine`, MCP tools, CLI) never import a backend directly. They program against the protocol. The backend is selected by configuration (DSN string or path).

**Depends on:** EPIC-053 (agent identity — needed for backend routing)
**Enables:** EPIC-055 (Postgres implementation), EPIC-056 (group membership)

## Success Criteria

- [ ] `HiveBackend` protocol defined with all current `HiveStore` public methods
- [ ] `FederationBackend` protocol defined with all current `FederatedStore` public methods
- [ ] `SqliteHiveBackend` wraps existing `HiveStore` — zero behavior change
- [ ] `SqliteFederationBackend` wraps existing `FederatedStore` — zero behavior change
- [ ] `create_hive_backend(dsn_or_path)` factory returns correct backend based on input
- [ ] All existing tests pass without modification (backends are transparent)
- [ ] `PropagationEngine`, MCP server, and CLI use the protocol, not the concrete class

## Stories

### STORY-054.1: Define HiveBackend protocol

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/hive.py` (full public API), `src/tapps_brain/_protocols.py`
**Verification:** `pytest tests/unit/test_hive_backend_protocol.py -v --tb=short -m "not benchmark"`

#### Why

The `HiveStore` class has 20+ public methods covering saves, searches, groups, feedback, notifications, and agent registry. A protocol interface decouples callers from the SQLite implementation, enabling Postgres (EPIC-055) without touching any caller code.

#### Acceptance Criteria

- [ ] `HiveBackend` protocol in `_protocols.py` with all public methods from `HiveStore`:
  - `save()`, `get()`, `search()`, `patch_confidence()`, `get_confidence()`
  - `create_group()`, `add_group_member()`, `remove_group_member()`, `list_groups()`, `get_group_members()`, `get_agent_groups()`, `agent_is_group_member()`, `search_with_groups()`
  - `record_feedback_event()`, `query_feedback_events()`
  - `list_namespaces()`, `count_by_namespace()`, `count_by_agent()`
  - `get_write_notify_state()`, `wait_for_write_notify()`
  - `close()`
- [ ] Protocol is `@runtime_checkable`
- [ ] Method signatures match existing `HiveStore` exactly (return types, parameter types)
- [ ] `AgentRegistry` protocol defined separately (it may live in Postgres or YAML depending on backend)

---

### STORY-054.2: Define FederationBackend protocol

**Status:** planned
**Effort:** S
**Depends on:** none
**Context refs:** `src/tapps_brain/federation.py` (full public API), `src/tapps_brain/_protocols.py`
**Verification:** `pytest tests/unit/test_federation_backend_protocol.py -v --tb=short -m "not benchmark"`

#### Why

`FederatedStore` has a smaller surface (publish, unpublish, search, get_project_entries, get_stats) but the same SQLite coupling. Abstracting it enables cross-project federation over Postgres.

#### Acceptance Criteria

- [ ] `FederationBackend` protocol in `_protocols.py` with methods:
  - `publish()`, `unpublish()`, `search()`, `get_project_entries()`, `get_stats()`, `close()`
- [ ] Protocol is `@runtime_checkable`
- [ ] Method signatures match existing `FederatedStore`
- [ ] `sync_to_hub()` and `sync_from_hub()` module functions accept `FederationBackend` (not concrete `FederatedStore`)

---

### STORY-054.3: SqliteHiveBackend adapter

**Status:** planned
**Effort:** M
**Depends on:** STORY-054.1
**Context refs:** `src/tapps_brain/hive.py`
**Verification:** `pytest tests/unit/test_hive.py -v --tb=short -m "not benchmark"`

#### Why

The existing `HiveStore` must be wrapped (or made to satisfy) the `HiveBackend` protocol so that all current behavior is preserved. This is a refactoring story with zero behavior change.

#### Acceptance Criteria

- [ ] `SqliteHiveBackend` class satisfies `HiveBackend` protocol (verified by `isinstance` check)
- [ ] Either: `HiveStore` itself is made to satisfy the protocol (rename to `SqliteHiveBackend`), or a thin adapter wraps it
- [ ] All 100% of existing `HiveStore` tests pass against `SqliteHiveBackend`
- [ ] `AgentRegistry` wrapped in `AgentRegistryBackend` protocol adapter
- [ ] No caller code changes — `PropagationEngine` and MCP tools work as before

---

### STORY-054.4: SqliteFederationBackend adapter

**Status:** planned
**Effort:** S
**Depends on:** STORY-054.2
**Context refs:** `src/tapps_brain/federation.py`
**Verification:** `pytest tests/unit/test_federation.py -v --tb=short -m "not benchmark"`

#### Why

Same rationale as STORY-054.3 — wrap `FederatedStore` to satisfy `FederationBackend` protocol.

#### Acceptance Criteria

- [ ] `SqliteFederationBackend` satisfies `FederationBackend` protocol
- [ ] All existing federation tests pass
- [ ] `sync_to_hub()`, `sync_from_hub()`, `federated_search()` accept the protocol type

---

### STORY-054.5: Backend factory and DSN-based routing

**Status:** planned
**Effort:** M
**Depends on:** STORY-054.3, STORY-054.4
**Context refs:** `src/tapps_brain/hive.py`, `src/tapps_brain/federation.py`, `src/tapps_brain/mcp_server.py`, `src/tapps_brain/cli.py`
**Verification:** `pytest tests/unit/test_backend_factory.py -v --tb=short -m "not benchmark"`

#### Why

Callers should not decide which backend to use. A factory function inspects configuration and returns the right backend. This is the "hide complexity" entry point — agents and humans configure a DSN, tapps-brain does the rest.

#### Acceptance Criteria

- [ ] `create_hive_backend(dsn_or_path: str | None) -> HiveBackend` factory:
  - `None` or file path → `SqliteHiveBackend`
  - `postgres://...` → raises `NotImplementedError` (until EPIC-055)
- [ ] `create_federation_backend(dsn_or_path: str | None) -> FederationBackend` factory
- [ ] `TAPPS_BRAIN_HIVE_DSN` env var supported (path or postgres URI)
- [ ] `TAPPS_BRAIN_FEDERATION_DSN` env var supported
- [ ] MCP server uses factory instead of direct `HiveStore()` construction
- [ ] CLI uses factory instead of direct construction
- [ ] `PropagationEngine.propagate()` accepts `HiveBackend` (not `HiveStore`)
- [ ] Existing behavior unchanged when env vars are unset (SQLite default)

---

### STORY-054.6: Update PropagationEngine and callers to use protocols

**Status:** planned
**Effort:** M
**Depends on:** STORY-054.5
**Context refs:** `src/tapps_brain/hive.py` (`PropagationEngine`), `src/tapps_brain/store.py`, `src/tapps_brain/mcp_server.py`
**Verification:** `pytest tests/unit/test_hive.py tests/unit/test_mcp_server.py -v --tb=short -m "not benchmark"`

#### Why

`PropagationEngine.propagate()` takes a `hive_store: HiveStore` parameter. All callers pass concrete `HiveStore` instances. These must be updated to accept `HiveBackend` so Postgres backends work transparently.

#### Acceptance Criteria

- [ ] `PropagationEngine.propagate()` type signature uses `HiveBackend` not `HiveStore`
- [ ] `MemoryStore.__init__` accepts `hive_store: HiveBackend | None`
- [ ] `select_local_entries_for_hive_push()` and `push_memory_entries_to_hive()` accept `HiveBackend`
- [ ] MCP server tool handlers use `HiveBackend` type
- [ ] CLI hive commands use `HiveBackend` type
- [ ] mypy passes with updated signatures
- [ ] All tests pass — SQLite backend is transparent

## Priority Order

| Order | Story | Rationale |
|-------|-------|-----------|
| 1 | STORY-054.1, STORY-054.2 | Protocol definitions (can parallelize) |
| 2 | STORY-054.3, STORY-054.4 | SQLite adapters (can parallelize) |
| 3 | STORY-054.5 | Factory wires it all together |
| 4 | STORY-054.6 | Caller updates — makes abstraction real |
