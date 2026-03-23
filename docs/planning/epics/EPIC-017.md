---
id: EPIC-017
title: "Code Review — Storage & Data Model"
status: done
priority: medium
created: 2026-03-22
target_date: 2026-04-30
tags: [review, storage, data-model, quality]
---

# EPIC-017: Code Review — Storage & Data Model

## Context

With all 16 feature epics complete and BUG-001/BUG-002 fixes queued, the codebase is ready for systematic code review. This epic covers the core storage layer (`store.py`, `persistence.py`, `models.py`) and supporting storage files (`__init__.py`, `_protocols.py`, `_feature_flags.py`, `audit.py`, `session_index.py`, `integrity.py`).

### Why Now

The storage layer is the foundation of the entire system. Bugs here affect every interface (library, CLI, MCP). A thorough review before any further feature work ensures the foundation is solid.

## Success Criteria

- [x] `store.py` reviewed (both halves: core CRUD and advanced features)
- [x] `persistence.py` reviewed (SQLite layer, migrations, FTS5)
- [x] `models.py` reviewed (Pydantic data models)
- [x] `__init__.py` reviewed (public API surface)
- [x] `_protocols.py` + `_feature_flags.py` reviewed
- [x] `audit.py` + `session_index.py` reviewed
- [x] `integrity.py` reviewed
- [x] All issues found are fixed with tests

## Review Checklist (per file)

1. **Correctness:** logic bugs, off-by-one, race conditions
2. **Security:** injection, unsanitized input, credential leaks
3. **Performance:** unnecessary copies, N+1 queries, missing indexes
4. **Dead code:** unreachable branches, unused imports/vars
5. **Error handling:** swallowed exceptions, missing validation
6. **Type safety:** Any casts, missing None checks
7. **Style:** naming, complexity, docstring accuracy

## Stories

See `.ralph/fix_plan.md` tasks 017-A through 017-H for the full breakdown.

## Priority Order

| Order | Story | Rationale |
|-------|-------|-----------|
| 1 | 017-A | Largest file, core CRUD — highest risk |
| 2 | 017-B | Advanced features depend on core being clean |
| 3 | 017-C | SQLite layer — data integrity critical |
| 4 | 017-D | Data models — validation correctness |
| 5-8 | 017-E through 017-H | Independent supporting files |
