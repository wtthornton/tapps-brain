---
id: EPIC-041
title: "Federation hub memory_group, Hive groups follow-up, operator clarity"
status: done
priority: high
created: 2026-04-02
tags: [federation, hive, memory_group, documentation, github-51, github-52, github-63, github-64]
---

# EPIC-041: Federation hub + Hive groups + operator docs

## Context

Post–**#49** (v1 project-local `memory_group`) work queued on GitHub and in [`open-issues-roadmap.md`](../open-issues-roadmap.md): federation hub should carry the publisher’s partition label (**#51** / 49-E), Hive may gain `agent_scope` `group:<name>` + membership (**#52**), and operator clarity issues **#63** / **#64**.

Design references: [`design-issue-49-multi-scope-memory.md`](../design-issue-49-multi-scope-memory.md), [`epic-49-tasks.md`](../epic-49-tasks.md) § 49-E.

## Success criteria

- [x] **#51** — Hub `federated_memories` stores `memory_group`; publish/subscribe round-trip restores it on subscriber projects (shipped 2026-04-02; GitHub **closed** 2026-03-31).
- [x] **#52** — Hive `group:<name>` semantics (membership, propagation, recall union); shipped on `main` — GitHub **#52** **reopened** 2026-04-02 to align issue checklist with delivery (close when checklist updated or verified).
- [x] **#63** — `StoreHealth.retrieval_effective_mode` + `retrieval_summary`; CLI diagnostics health + MCP JSON; `optional-features-matrix.md` pointer (shipped 2026-04-02; GitHub **closed** 2026-04-02).
- [x] **#64** — `docs/guides/hive-vs-federation.md` + links from `hive.md` / `federation.md` (shipped 2026-04-02; GitHub **closed** 2026-04-02).

## Stories

### STORY-041.1: Federation hub `memory_group` (GitHub #51, 49-E)

**Status:** done (2026-04-02 on `main`; **#51** closed on GitHub).

**Scope:** SQLite migration on `federated.db`, `FederatedStore.publish` / `search` / `get_project_entries`, `sync_from_hub` → `MemoryStore.save(memory_group=…)`; `FederatedSearchResult.memory_group`; [`docs/guides/federation.md`](../../guides/federation.md) + engineering schema note.

**Acceptance:** Round-trip project → hub → subscriber local store preserves `memory_group`; optional `memory_group` filter on hub search; tests + docs — met.

### STORY-041.2: Hive `agent_scope` `group:<name>` + membership (GitHub #52)

**Status:** done on `main` (2026-03-31); GitHub **#52** **reopened** 2026-04-02 (checklist hygiene — see issue comments).

**Scope:** `agent_scope` `group:<name>` normalization (`agent_scope.py`); `PropagationEngine` → Hive namespace *name* with `agent_is_group_member`; recall merges universal + profile + member group namespaces; MCP/CLI validation; relay import; [`docs/guides/hive.md`](../../guides/hive.md).

**Acceptance:** Met — tests + docs; distinct from project-local `memory_group` / CLI `--group`.

### STORY-041.3: Vector / hybrid discoverability (GitHub #63)

**Status:** done (2026-04-02).

**Scope:** `health_check.StoreHealth.retrieval_effective_mode` / `retrieval_summary`; CLI `diagnostics health` human output; MCP inherits JSON dump; `docs/engineering/optional-features-matrix.md` table.

### STORY-041.4: Hive vs federation guide (GitHub #64)

**Status:** done (2026-04-02).

**Scope:** `docs/guides/hive-vs-federation.md`; cross-links from `hive.md` and `federation.md`.

## Priority order

1. **041.1** / **041.2** / **041.3** / **041.4** — shipped on `main`; GitHub **#51**, **#63**, **#64** closed; **#52** reopened for checklist alignment (2026-04-02).
