# Engineering Documentation Task - System Ground Truth

Status: in_progress  
Priority: high  
Created: 2026-03-31  
Owner: engineering

## Problem statement

Current documentation does not fully represent the implemented system. The codebase likely contains:

- linked and unlinked execution paths,
- dead or stale code paths,
- behavior gated by feature flags or optional extras,
- subsystem interactions that are not documented end-to-end.

This creates risk for architecture decisions, onboarding, incident response, and future migrations.

## Objective

Produce a complete, engineering-grade documentation baseline that reflects actual behavior in code.

## Deliverables

- Canonical system architecture document (top-level components and boundaries).
- Subsystem documentation for storage, retrieval, Hive, federation, MCP, CLI, diagnostics, and flywheel.
- Call flow documentation for major user and system paths.
- Database schema documentation for all local stores and shared stores.
- Feature-flag and optional-dependency behavior matrix.
- Code inventory report: active, deprecated, dead/suspect, and behind-flag code paths.
- Source-of-truth index linking docs to owning modules/files.

## Required documentation set

### 1) System and subsystem architecture

- Context diagram (external interfaces and actors).
- Container-level architecture (CLI, MCP server, core library, persistence layer, Hive/federation stores).
- Subsystem boundaries and contracts (inputs, outputs, invariants, failure modes).

### 2) Call flows

- Memory save flow (CLI, MCP, API-style usage).
- Recall flow (retrieval, ranking, filtering, injection, diagnostics).
- Hive propagation and recall merge flow.
- Federation publish/sync/subscribe flow.
- Maintenance flows (migrate, consolidate, GC, health).

Each flow should include:

- entry points,
- called modules and critical functions,
- persistence touch points,
- error/edge-case branches.

### 3) Database schema and migration map

- Project-local DB schema (tables, indexes, triggers, FTS, constraints).
- Hive DB schema (namespaces, feedback, groups/members, write notifications).
- Federated DB schema summary.
- Schema version timeline and migration map (v1 to current).
- Table/column ownership by subsystem.

### 4) Feature flags and optional behavior matrix

- Optional extras and resulting behavior changes.
- Runtime checks that alter execution path.
- Profile-driven behavior that changes persistence, propagation, or retrieval.

### 5) Code inventory and doc coverage audit

- Module list with status:
  - active and documented,
  - active but undocumented,
  - candidate dead code,
  - deprecated or migration-only paths.
- Explicit list of paths requiring follow-up cleanup or formal deprecation notes.

## Out of scope (for this task)

- Refactoring or deleting code.
- Behavior changes.
- Backend migration implementation.

This task is documentation and inventory only.

## Acceptance criteria

- All core subsystems have architecture + flow docs with file-level references.
- DB schemas are documented from code, not inferred from stale notes.
- Feature-flag/optional path matrix is complete and reviewed.
- Every major public path (CLI + MCP + library) is mapped to core call flow.
- A backlog of doc gaps and suspected dead code is created with actionable next steps.

## Suggested execution plan

1. Build module inventory from `src/tapps_brain`.
2. Map public entry points (CLI commands, MCP tools, store APIs).
3. Trace call graphs for critical paths.
4. Extract schema/migration details from persistence code.
5. Produce draft docs and cross-link to source files.
6. Review with engineering and convert doc gaps into tracked issues.

## Tracking checklist

- [x] Architecture docs drafted
- [x] Call flows documented for major paths
- [x] DB schema and migration map documented
- [x] Feature-flag/optional behavior matrix documented
- [x] Code inventory and dead-code candidates documented
- [x] Documentation cross-link index published
- [x] Follow-up issues created from findings — GitHub **#55–#62** ([phase 2 issue pack](engineering-doc-phase2-follow-up-issues.md))

## Baseline artifacts (2026-03-31)

- `docs/engineering/README.md`
- `docs/engineering/system-architecture.md`
- `docs/engineering/call-flows.md`
- `docs/engineering/data-stores-and-schema.md`
- `docs/engineering/optional-features-matrix.md`
- `docs/engineering/code-inventory-and-doc-gaps.md`

## Phase 2 — follow-up issues (2026-03-31)

- [`docs/planning/engineering-doc-phase2-follow-up-issues.md`](engineering-doc-phase2-follow-up-issues.md) — prioritized P0/P1/P2 items, copy-paste issue bodies, tracking table.

## Notes from meeting close (2026-03-31)

- Meeting paused intentionally.
- Immediate next priority: engineering documentation baseline before major architectural changes.
