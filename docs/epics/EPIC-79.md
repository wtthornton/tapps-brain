# Epic 79: Async Postgres Backend Error Propagation

<!-- docsmcp:start:metadata -->
**Status:** Proposed
**Priority:** Medium
**Estimated LOE:** M

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that async private-memory callers receive accurate failure signals instead of silent empty results when the database is unavailable or misconfigured.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Replace broad except Exception swallowing in AsyncPostgresPrivateBackend with typed, logged, and propagated errors on critical read/write paths.

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

Code review found 12+ bare except Exception handlers in async_postgres_private.py that log a warning and return empty defaults ([] or 0). knn_search, audit_query, gc_archive, and flywheel_meta paths can mask outages — agents see empty recall with no quality_warning.

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] - [ ] knn_search and search paths propagate or wrap DB errors instead of returning []
- [ ] Audit and GC archive failures surface to MemoryStore/aio callers
- [ ] Integration tests assert error propagation when Postgres is unreachable
- [ ] No new bare except Exception without explicit justification comment

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

### 79.1 -- async_postgres_private.py: propagate knn_search DB failures

**Points:** TBD

Describe what this story delivers...

**Tasks:**
- [ ] Implement async_postgres_private.py: propagate knn_search db failures
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** async_postgres_private.py: propagate knn_search DB failures is implemented, tests pass, and documentation is updated.

---

### 79.2 -- async_postgres_private.py: propagate audit/gc archive failures

**Points:** TBD

Describe what this story delivers...

**Tasks:**
- [ ] Implement async_postgres_private.py: propagate audit/gc archive failures
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** async_postgres_private.py: propagate audit/gc archive failures is implemented, tests pass, and documentation is updated.

---

### 79.3 -- tests/integration: async backend error propagation

**Points:** TBD

Describe what this story delivers...

**Tasks:**
- [ ] Implement tests/integration: async backend error propagation
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** tests/integration: async backend error propagation is implemented, tests pass, and documentation is updated.

---

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Document architecture decisions for **Async Postgres Backend Error Propagation**...

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- Rewriting the entire async backend; changing sync PostgresPrivateBackend error semantics.

<!-- docsmcp:end:non-goals -->
