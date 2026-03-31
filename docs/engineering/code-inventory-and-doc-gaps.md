# Code Inventory and Documentation Gaps

## Inventory by subsystem

- **Core orchestration**
  - `store.py`, `retrieval.py`, `injection.py`, `recall.py`
- **Persistence and schema**
  - `persistence.py`, `sqlite_vec_index.py`
- **Sharing layers**
  - `hive.py`, `federation.py`, `memory_relay.py`
- **Quality loop**
  - `feedback.py`, `diagnostics.py`, `flywheel.py`
- **Interfaces**
  - `cli.py`, `mcp_server.py`
- **Profiles and behavior config**
  - `profile.py`, `profiles/*.yaml`

## Known documentation risk areas

- **Hive defaults drift**
  - Some docs showed empty Hive tier defaults; code defaults are non-empty.
- **Hive attach vs profile rules**
  - Resolved for operator docs: see `HiveConfig` docstring and “Who attaches Hive?” in `docs/guides/hive.md` (Phase 2 / #56).
- **Federation `hub_path`**
  - Resolved in code: `federated_hub_db_path()` and default `FederatedStore()` path (#55).
- **Optional feature discoverability**
  - OTel and visual snapshot operator notes live in `docs/guides/observability.md` and `docs/guides/visual-snapshot.md` (#58, #59).

## Dead/stale code workflow (required process)

This baseline does not delete code. Use this workflow:

1. Mark candidate module/path in this file.
2. Confirm no CLI/MCP/library runtime references.
3. Confirm test coverage usage intent.
4. Decide: document, deprecate, or remove.
5. Track with an issue and owner.

## Candidate follow-up audit list

Tracked as **GitHub issues #55–#62** in:

- [`docs/planning/engineering-doc-phase2-follow-up-issues.md`](../planning/engineering-doc-phase2-follow-up-issues.md)

Summary (Phase 2 implementation status, 2026-03-31):

- [x] **#55 / ED-P0-01** — `hub_path` honored (`federated_hub_db_path`, CLI status JSON).
- [x] **#56 / ED-P0-02** — Hive attach story + `HiveConfig` docstring.
- [x] **#57 / ED-P1-01** — Engineering baseline linked from README, CLAUDE.md, `project.mdc`.
- [x] **#58 / ED-P1-02** — `docs/guides/observability.md`; README `[otel]` footnote; EPIC-032 pointer.
- [x] **#59 / ED-P1-03** — `docs/guides/visual-snapshot.md`; README nav link.
- [x] **#60 / ED-P1-04** — Manifest includes resources; docs + OpenClaw check read counts from manifest.
- [x] **#61 / ED-P2-01** — `mem0-review/` scope note in `docs/engineering/README.md`.
- [x] **#62 / ED-P2-02** — Import/static check: `otel_exporter` has no CLI/MCP/store wiring (documented); no new orphan modules filed beyond explicit test-only helpers. Re-run when adding entry points.

- [x] Hive guide/profile defaults reconciled (2026-03-31 baseline).
- [x] Federation guide aligned with `hub_path` behavior (Phase 2).
