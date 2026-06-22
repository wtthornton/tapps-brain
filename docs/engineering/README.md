# Engineering Documentation Baseline

This folder is the code-aligned engineering reference for tapps-brain runtime behavior.

A generated map of all documentation (including planning epics) lives at [`DOCUMENTATION_INDEX.md`](../DOCUMENTATION_INDEX.md).

If planning docs or guides disagree with this folder, treat this folder as the implementation ground truth and open a doc-fix issue.

## Documents

- [`diagrams.md`](diagrams.md) — **C4 context/container/component, ER, class hierarchy, sequence flows for `brain_recall` / `brain_remember` / `brain_record_event` / Hive propagation, hexagonal layering.** Code-aligned, Mermaid-rendered. Start here for visual reference.
- [`architecture-report.html`](architecture-report.html) — full HTML architecture report with SVG flows, per-package descriptions, dependency graph, API surface. Open in a browser.
- [`system-architecture.md`](system-architecture.md) — components, boundaries, and runtime interfaces (narrative)
- [`call-flows.md`](call-flows.md) — save, recall, Hive, federation, and maintenance execution flows (text walkthrough)
- [`data-stores-and-schema.md`](data-stores-and-schema.md) — Postgres store schemas, private migration history (001–024), indexes, FTS
- [`experience-events.md`](experience-events.md) — atomic single-TX KG write API ([`ExperienceEventRecorder`](../../src/tapps_brain/experience.py))
- [`partition-retention.md`](partition-retention.md) — partition + retention strategy for audit/session tables (pg_partman optional)
- [`optional-features-matrix.md`](optional-features-matrix.md) — extras, feature flags, profile toggles, fallbacks
- [`threat-model.md`](threat-model.md) — STRIDE per public surface
- [`async-performance.md`](async-performance.md) — async backend benchmarks and interpretation guide
- [`features-and-technologies.md`](features-and-technologies.md) — industry-mapping of capabilities to libraries
- [`code-inventory-and-doc-gaps.md`](code-inventory-and-doc-gaps.md) — module inventory and documentation risk audit
- [`v3-behavioral-parity.md`](v3-behavioral-parity.md) — what changed in v3 vs v2 (Postgres private memory, etc.)

Also see the interactive viewer at [`docs/architecture.html`](../architecture.html) — pan, zoom, and toggle between 6 diagram types in one page.

## Architectural Decision Records (ADRs)

ADRs live in [`docs/planning/adr/`](../planning/adr/). Key decisions relevant to the
engineering layer:

- [ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md) — Postgres-only persistence plane (private memory, Hive, Federation — no SQLite fallback).
- [ADR-008](../planning/adr/ADR-008-no-http-without-mcp-library-parity.md) — No new
  public HTTP routes without MCP + library parity. Defines the HTTP surface guardrails
  and CODEOWNERS enforcement strategy.

## Scope

This baseline is generated from current code paths in:

- `src/tapps_brain/`
- `docs/guides/` (for operator-facing behavior checks)

It is intentionally implementation-first, not roadmap-first.

## Follow-up work

Prioritized issues to file on GitHub: [`docs/planning/engineering-doc-phase2-follow-up-issues.md`](../planning/engineering-doc-phase2-follow-up-issues.md).
