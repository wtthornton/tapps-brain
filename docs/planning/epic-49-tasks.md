# Epic #49 — actionable child issues (multi-scope memory)

**Parent:** GitHub **#49** (multi-group memory scopes).  
**Design:** [`design-issue-49-multi-scope-memory.md`](design-issue-49-multi-scope-memory.md).

Use this file to **file child issues on GitHub**, then paste the real issue numbers into [`open-issues-roadmap.md`](open-issues-roadmap.md) under epic #49.

## Dependency order

```text
49-A (schema + model) ──► 49-B (retrieval) ──► 49-C (MCP/CLI)
        │                        │
        └────────────────────────┴──► 49-D (docs, parallel anytime after A is clear)

49-E (relay/federation group field) — optional; after A (and B if import affects ranking)
```

| ID (use until filed) | Title slug                         | Blocks / notes        |
|---------------------|------------------------------------|-----------------------|
| **49-A**            | Project-local memory `group` field | Blocks B, C           |
| **49-B**            | Retrieval filter by `group`      | Blocks C (MCP passes filters through store) |
| **49-C**            | MCP + CLI `group` parameters       | After B               |
| **49-D**            | Scope alignment doc                | Parallel with B/C     |
| **49-E**            | Relay / federation `group`       | Optional; after A     |

---

## 49-A — Schema + model: optional project-local `group`

**Suggested GitHub title:** `feat(#49): add optional project-local memory group (schema + model)`

**Suggested labels:** `enhancement`, `epic-49` (or link to parent #49 only)

### Summary

Add an optional **project-local** string field on memories (working name: **`group`**) to partition the project DB (e.g. `team-a`, `feature-x`) without conflating with Hive **namespaces** or profile **layers**. Empty / unset means **ungrouped — visible as today’s global-within-project default**.

### Acceptance criteria

- [ ] **`MemoryEntry`** (and any consolidation/archive types that mirror row shape) include optional `group: str | None` (or `""` default — pick one convention and document it; prefer matching existing optional string patterns like `branch`).
- [ ] **SQLite:** new column on `memories` and matching columns on `archived_memories` (and any other tables that duplicate the full row) via a **new schema migration** in `persistence.py` (follow existing `schema_version` / `_migrate_vN_to_vN+1` pattern).
- [ ] **Round-trip:** load → save → reload preserves `group`; existing DBs migrate with default “no group” behavior identical to pre-change semantics.
- [ ] **FTS5:** if product requires searching by group name, extend `memories_fts` + triggers to include the column **or** document that group is filter-only (SQL predicate) for v1 — **decide in implementation and reflect in 49-B**.
- [ ] **Indexes:** add index on `memories(group)` if retrieval will filter by it (recommended).
- [ ] **Tests:** unit tests for migration + model serialization; persistence integration tests for new column.
- [ ] **Determinism / limits:** enforce a sensible max length (e.g. align with `key` / tag limits policy) and normalization rules (trim, reject control chars if that’s project standard).

### Out of scope

- Hive namespace changes, implicit sync group ↔ Hive, renaming Hive “namespace” to “group”.
- MCP/CLI exposure (49-C) and recall filtering behavior (49-B) except what’s required to compile and persist.

### References

- `src/tapps_brain/models.py` — `MemoryEntry`
- `src/tapps_brain/persistence.py` — `memories` / `archived_memories`, migrations, FTS triggers

---

## 49-B — Retrieval: filter (and rank contract) by `group`

**Suggested GitHub title:** `feat(#49): recall/search filter by project memory group`

**Depends on:** 49-A merged

### Summary

**Recall and search** accept an optional **group filter**. Default = **all groups** (backward compatible). Define interaction with existing **scope**, **tags**, and **tier** filters.

### Acceptance criteria

- [ ] **API:** `MemoryStore` / `MemoryRetriever` (and any public recall entrypoints used by MCP) accept optional `group: str | None` meaning:
  - `None` / omitted: no group filter (current behavior for “whole project”).
  - explicit string: restrict to that group only.
- [ ] **Consistency:** documented behavior when `group` is set together with `scope`, `branch`, etc. (no contradictory filters without a clear error or precedence rule).
- [ ] **BM25 / hybrid / vector paths:** group filter applied in the same stage as other SQL-level filters so counts and ranking stay coherent.
- [ ] **Tests:** retrieval tests covering ungrouped-only, single group, empty result set, and default “all groups” on migrated legacy rows.

### Out of scope

- New ranking signal based on group (unless explicitly specified later).
- MCP parameter names (49-C).

### References

- `src/tapps_brain/retrieval.py`, `src/tapps_brain/recall.py`, `src/tapps_brain/store.py`

---

## 49-C — MCP + CLI: `group` on save, search, recall; optional list-groups

**Suggested GitHub title:** `feat(#49): expose memory group on MCP and CLI`

**Depends on:** 49-B (and 49-A)

### Summary

Expose **`group`** on **save**, **search/recall**, and any **batch** tools that create or query entries. Optionally add **list distinct groups** for UX (CLI + MCP) if low cost.

### Acceptance criteria

- [ ] **Save path:** `MemoryStore.save` / ingest paths accept optional `group`; MCP `memory_save` (and equivalents) document + implement the same parameter.
- [ ] **Query path:** search / recall MCP tools and CLI commands accept optional `--group` / `group=` aligned with 49-B semantics.
- [ ] **OpenClaw plugin:** if the plugin wraps these tools, pass through `group` where applicable (follow patterns from recent #46/#48 work).
- [ ] **Docs:** `docs/guides/` updates for operators; mention distinction vs Hive namespace and profile tier (or link 49-D).
- [ ] **Tests:** MCP handler tests + CLI tests for new flags; update tool-count / doc consistency scripts if this repo gates on them.

### Out of scope

- Hive push automatically setting group (explicit user/agent mapping only, per design note).

### References

- `src/tapps_brain/mcp_server.py`, CLI entrypoints under `src/tapps_brain/`, `openclaw-plugin/`

---

## 49-D — Documentation: Hive namespace vs project group vs profile layer

**Suggested GitHub title:** `docs(#49): scope model — Hive namespace vs project group vs profile layer`

**Can start:** as soon as 49-A field name and semantics are frozen; **ship** alongside or shortly after 49-C.

### Summary

Single **operator-facing** table (and short prose) mapping:

- **Project-local `group`** — partition within one project DB.
- **Hive namespace** — cross-agent shared store (`hive.db`).
- **Profile layer / tier** — decay and classification, not storage partition.

### Acceptance criteria

- [ ] New or updated section in an existing guide (prefer `docs/guides/` over duplicating epic prose).
- [ ] Explicit “when to use which” bullets + anti-patterns (no silent sync group ↔ Hive).
- [ ] Linked from roadmap / design note / MCP operator doc as appropriate.

### Out of scope

- Global rename of Hive terminology.

---

## 49-E — Optional: relay / federation / export carry `group`

**Suggested GitHub title:** `feat(#49): optional group field in memory relay / federation export`

**Depends on:** 49-A; coordinate with 49-B if import replays through retrieval.

### Summary

If **relay** (`memory_relay`) or **federation** export/import round-trips entries, optionally include **`group`** so portable bundles preserve partition labels.

### Acceptance criteria

- [ ] Format version bump or backward-compatible optional field (document migration for consumers).
- [ ] Import: set `group` on stored entries; unknown fields ignored on old readers if applicable.
- [ ] Tests: export → import preserves group.

### Out of scope

- Required only when a concrete consumer asks for it; can remain **unfiled** until then.

---

## Filing checklist (maintainer)

1. Create GitHub issues from **49-A → 49-B → 49-C**; **49-D** in parallel once **49-A** is filed (or when field name is decided).
2. In each child issue body, paste the **Acceptance criteria** checkboxes from above; set **parent / tracked-by #49** if your repo supports sub-issues.
3. Replace placeholders **49-A** … in this file or roadmap with real numbers (e.g. `#1234`).
4. Close **#49** when all **required** children (A–D) are done; leave **49-E** open as a follow-up or file later.
