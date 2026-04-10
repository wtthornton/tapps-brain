# Engineering Documentation Baseline

This folder is the code-aligned engineering reference for tapps-brain runtime behavior.

A generated map of all documentation (including planning epics) lives at [`DOCUMENTATION_INDEX.md`](../DOCUMENTATION_INDEX.md).

If planning docs or guides disagree with this folder, treat this folder as the implementation ground truth and open a doc-fix issue.

## Documents

- `system-architecture.md` - components, boundaries, and runtime interfaces
- `call-flows.md` - save, recall, Hive, federation, and maintenance execution flows
- `data-stores-and-schema.md` - SQLite stores, schema timeline, indexes, FTS, triggers
- `optional-features-matrix.md` - extras, feature flags, profile toggles, fallbacks
- `code-inventory-and-doc-gaps.md` - module inventory and documentation risk audit

## Scope

This baseline is generated from current code paths in:

- `src/tapps_brain/`
- `docs/guides/` (for operator-facing behavior checks)

It is intentionally implementation-first, not roadmap-first.

## Follow-up work

Prioritized issues to file on GitHub: [`docs/planning/engineering-doc-phase2-follow-up-issues.md`](../planning/engineering-doc-phase2-follow-up-issues.md).
