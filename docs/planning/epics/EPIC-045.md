---
id: EPIC-045
title: "Multi-tenant, sharing, and sync models — research and upgrades"
status: planned
priority: medium
created: 2026-03-31
tags: [hive, federation, agent_scope, memory_group, sync]
---

# EPIC-045: Multi-tenant, sharing, and sync models

## Context

Maps to **§4** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Overlaps shipped **#51**, **#52** — this epic is **forward improvement**, not duplicate delivery.

## Success criteria

- [ ] No regression to **scope semantics** documented in `memory-scopes.md` / `hive-vs-federation.md`.

## Stories

**§4 table order:** **045.1** Hive → **045.2** federation hub → **045.3** `agent_scope` / `group:<name>` → **045.4** `memory_group` → **045.5** change notification.

### STORY-045.1: Cross-agent shared memory (Hive)

**Status:** planned | **Effort:** L | **Depends on:** none  
**Context refs:** `src/tapps_brain/hive.py`, `docs/guides/hive.md`, `tests/unit/test_hive.py`, `tests/unit/test_hive_memory_group.py`, `tests/unit/test_hive_groups.py`, `tests/integration/test_hive_integration.py`, `tests/integration/test_hive_mcp_roundtrip.py`  
**Verification:** `pytest tests/unit/test_hive.py tests/unit/test_hive_memory_group.py tests/unit/test_hive_groups.py tests/integration/test_hive_integration.py tests/integration/test_hive_mcp_roundtrip.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **CRDT / OT** for text memories is heavy; **namespace + conflict_policy** is pragmatic for v1.
- **Quota per namespace** to prevent one agent flooding `universal`.

#### Implementation themes

- [ ] **Quota** and **rate** limits on Hive writes (profile or server-wide).
- [ ] **Backup/restore** Hive DB operator section.
- [ ] Observability: **propagate denied** metrics by reason (`hive.propagate.group_denied`, etc.).

---

### STORY-045.2: Cross-project hub (Federation)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/federation.py`, `docs/guides/federation.md`, `tests/unit/test_federation.py`, `tests/integration/test_federation_integration.py`  
**Verification:** `pytest tests/unit/test_federation.py tests/integration/test_federation_integration.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **Selective sync** (by tier, tag, memory_group) reduces noise vs full hub mirror.
- **Signing** published payloads for **integrity** between projects (optional).

#### Implementation themes

- [ ] **Filter DSL** for publish (deterministic parser).
- [ ] **Conflict** story when subscriber edits federated rows locally.

---

### STORY-045.3: Agent / scope routing (`agent_scope`, `group:<name>`)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/agent_scope.py`, `src/tapps_brain/recall.py`, `src/tapps_brain/hive.py` (`PropagationEngine`), `tests/unit/test_agent_scope.py`, `tests/unit/test_recall.py`  
**Verification:** `pytest tests/unit/test_agent_scope.py tests/unit/test_recall.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **ABAC-style** policies (attribute-based) may replace string scopes long-term — design **compat** layer.

#### Implementation themes

- [ ] Doc diagram: **one query → namespace list** resolution order.
- [ ] Fuzz tests for **normalization** (`group:` edge cases).

---

### STORY-045.4: Project-local partitioning (`memory_group`)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/memory_group.py`, `src/tapps_brain/store.py` (group filters), `tests/unit/test_memory_group_feature.py`  
**Verification:** `pytest tests/unit/test_memory_group_feature.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **Composite** keys (group + branch) for monorepo workflows.

#### Implementation themes

- [ ] **Default group** in profile for MCP/CLI saves (opt-in).
- [ ] **list_groups** performance on huge stores (index audit).

---

### STORY-045.5: Change notification (polling / revision)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/hive.py` (`hive_write_notify`), `src/tapps_brain/mcp_server.py` (`hive_write_revision`, `hive_wait_write`), `src/tapps_brain/cli.py` (`hive watch`), `tests/unit/test_mcp_server.py`, `tests/unit/test_cli.py`  
**Verification:** `pytest tests/unit/test_mcp_server.py tests/unit/test_cli.py -k "hive_write_revision or hive_wait_write or hive_watch" -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **WebSocket** or **FS watch** sidecar for lower latency than poll — optional architecture.
- **Debouncing** client loops to avoid hot spin.

#### Implementation themes

- [ ] **Backoff** policy documented for `hive_wait_write`.
- [ ] Spike: **inotify**/ReadDirectoryChangesW integration doc for Windows vs Unix.

## Priority order

**045.3**, **045.4** (correctness) → **045.1**, **045.2** → **045.5**.
