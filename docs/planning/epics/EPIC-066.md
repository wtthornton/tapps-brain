# Epic 66: Postgres-Only Persistence Plane — Production Readiness

<!-- docsmcp:start:metadata -->
**Status:** Complete
**Priority:** P0 - Critical
**Estimated LOE:** ~3-4 weeks (1 developer)
**Dependencies:** EPIC-059, ADR-007
**Blocks:** EPIC-061, EPIC-062, EPIC-063

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that the Postgres-only persistence plane shipped under EPIC-059 stage 2 (ADR-007 extended, 2026-04-11) is closed out as a production-grade release. Stage 2 deleted every line of SQLite code from the runtime tree, replaced it with PostgreSQL via psycopg + pgvector HNSW + tsvector, and brought the unit suite from "broken at import" to 2475 passing / 90 failing against a local Docker Postgres. The remaining 90 failures are not stale references — they are real behavioural gaps (consolidation merge audit emission, bi-temporal as_of search predicate, GC archive flow, MCP tool registration, version consistency) that need finishing before tapps-brain 3.4.0 can be tagged. This epic also closes the operator-readiness gaps that stage 2 deferred: ephemeral-Postgres CI, connection pool tuning + health, auto-migrate at startup, pg_tde encryption runbook, backup/restore runbook, behavioural parity load smoke, and a docs drift sweep across the engineering surface.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Take tapps-brain from "Postgres-only at the source level" to "Postgres-only and production-ready" — green test suite against ephemeral CI Postgres, complete operator runbooks for at-rest encryption and backup, pool/migration visibility in /health, and zero remaining behavioural deltas vs the v2 SQLite path.

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

EPIC-059 was scoped as "rip out SQLite". That work shipped on 2026-04-11 with the structural delete + the PostgresPrivateBackend + migrations 001-005, but the tail of behavioural parity work and operator runbooks were explicitly out of scope and deferred. Without this follow-up, three things break: (1) CI cannot run the unit suite (no ephemeral Postgres yet), (2) the 90 failing tests block any 3.4.0 release tag, and (3) operators cannot configure pg_tde or take backups without writing their own playbook. This epic finishes the job.

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Unit + integration test suites pass at 0 failures against ephemeral Postgres (Testcontainers or service container) in CI
- [ ] GitHub Actions workflow brings up pgvector/pg17 as a service container and runs full suite under 15 minutes wall-clock
- [ ] /health JSON exposes pool saturation and last applied private/hive/federation migration version
- [ ] MemoryStore startup auto-runs pending migrations when TAPPS_BRAIN_AUTO_MIGRATE=1
- [ ] pg_tde operator runbook published at docs/guides/postgres-tde.md with key store options (Vault/OpenBao/file) and rotation procedure
- [ ] Postgres backup/restore runbook published at docs/guides/postgres-backup.md covering pg_dump
- [ ] point-in-time recovery
- [ ] and Hive failover
- [ ] behavioural parity doc (docs/engineering/v3-behavioral-parity.md) updated with every intentional delta vs v2
- [ ] load smoke benchmark for 50 concurrent agents against one Postgres recorded in tests/benchmarks with p95 latency budget
- [ ] deleted SQLite-coupled tests have Postgres equivalents (test_memory_store_postgres_integration.py replaces test_memory_persistence.py et al)
- [ ] docs-mcp drift sweep over docs/engineering and docs/guides shows zero stale SQLite references
- [ ] EPIC-059 stories 6 and 8 close out
- [ ] openclaw-skill SKILL.md version aligned to pyproject 3.4.0

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

| Story | Title | Points |
|-------|-------|--------|
| [STORY-066.1](stories/STORY-066.1.md) | Consolidation merge audit emission | 3 |
| [STORY-066.2](stories/STORY-066.2.md) | Bi-temporal as_of filter on PostgresPrivateBackend.search | 5 |
| [STORY-066.3](stories/STORY-066.3.md) | GC archive Postgres table (migration 006) | 5 |
| [STORY-066.4](stories/STORY-066.4.md) | MCP tool registration audit and fix | 3 |
| [STORY-066.5](stories/STORY-066.5.md) | Version consistency unblock for openclaw-skill | 1 |
| [STORY-066.6](stories/STORY-066.6.md) | CI workflow with ephemeral Postgres service container | 5 |
| [STORY-066.7](stories/STORY-066.7.md) | Connection pool tuning + health JSON pool fields | 5 |
| [STORY-066.8](stories/STORY-066.8.md) | Auto-migrate on startup gate | 3 |
| [STORY-066.9](stories/STORY-066.9.md) | Behavioural parity doc + load smoke benchmark | 8 |
| [STORY-066.10](stories/STORY-066.10.md) | pg_tde operator runbook | 5 |
| [STORY-066.11](stories/STORY-066.11.md) | Postgres backup and restore runbook | 5 |
| [STORY-066.12](stories/STORY-066.12.md) | Engineering docs drift sweep | 5 |
| [STORY-066.13](stories/STORY-066.13.md) | Postgres integration tests replacing deleted SQLite-coupled tests | 8 |
| [STORY-066.14](stories/STORY-066.14.md) | Final test failure sweep — 90 to zero | 5 |

### 66.1 -- Consolidation merge audit emission

**Points:** 3

Wire append_audit calls into auto_consolidation.py merge, undo, and periodic-scan paths so the consolidation flow leaves an audit trail in the audit_log table. Resolves the 5 test_memory_auto_consolidation failures.

**Tasks:**
- [x] Implement consolidation merge audit emission
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Consolidation merge audit emission is implemented, tests pass, and documentation is updated.

---

### 66.2 -- Bi-temporal as_of filter on PostgresPrivateBackend.search

**Points:** 5

Add valid_at / invalid_at / superseded_by predicates to PostgresPrivateBackend.search() and propagate the as_of parameter from MemoryStore.search() down. Resolves test_search_as_of_returns_old_version and aligns Postgres temporal semantics with the v2 SQLite path.

**Tasks:**
- [x] Implement bi-temporal as_of filter on postgresprivatebackend.search
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Bi-temporal as_of filter on PostgresPrivateBackend.search is implemented, tests pass, and documentation is updated.

---

### 66.3 -- GC archive Postgres table (migration 006)

**Points:** 5

Replace the deleted JSONL archive flow with a Postgres gc_archive table keyed by (project_id, agent_id, archived_at). Update gc.py and store.py archive paths to INSERT into the table, update CLI maintenance gc to query it. Resolves test_gc_live_increments_archive_bytes and the MCP memory_import tier-normalization tests that depended on archive side effects.

**Tasks:**
- [x] Implement gc archive postgres table (migration 006)
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** GC archive Postgres table (migration 006) is implemented, tests pass, and documentation is updated.

---

### 66.4 -- MCP tool registration audit and fix

**Points:** 3

Investigate and resolve KeyError 'tool not found: memory_gc_config_set' across TestGcAndConsolidationConfigTools and TestMcpServerInputValidation022C. Confirm whether the gap is pre-existing or introduced by the ADR-007 rip-out, then register the missing tools in mcp_server.py.

**Tasks:**
- [x] Implement mcp tool registration audit and fix
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** MCP tool registration audit and fix is implemented, tests pass, and documentation is updated.

---

### 66.5 -- Version consistency unblock for openclaw-skill

**Points:** 1

Bump openclaw-skill/SKILL.md to match pyproject.toml. Update scripts/check_openclaw_docs_consistency.py if it pins the version. Resolves test_all_versions_match.

**Tasks:**
- [x] Implement version consistency unblock for openclaw-skill
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Version consistency unblock for openclaw-skill is implemented, tests pass, and documentation is updated.

---

### 66.6 -- CI workflow with ephemeral Postgres service container

**Points:** 5

Add pgvector/pgvector:pg17 as a GitHub Actions service container to .github/workflows/test.yml (or equivalent), set TAPPS_BRAIN_DATABASE_URL to the service hostname, run apply_private_migrations + apply_hive_migrations + apply_federation_migrations in a setup step, then invoke uv run pytest tests/. Wall-clock budget: under 15 minutes.

**Tasks:**
- [x] Implement ci workflow with ephemeral postgres service container *(.github/workflows/ci.yml — pgvector:pg17 service)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** CI workflow with ephemeral Postgres service container is implemented, tests pass, and documentation is updated.

---

### 66.7 -- Connection pool tuning + health JSON pool fields

**Points:** 5

Surface psycopg_pool ConnectionPool max_size, min_size, and connect_timeout via TAPPS_BRAIN_PG_POOL_* env vars in postgres_connection.py. Add pool_saturation and last_migration_version fields to /health JSON via health_check.py. Closes EPIC-059 STORY-059.7.

**Tasks:**
- [x] Implement connection pool tuning + health json pool fields *(TAPPS_BRAIN_PG_POOL_* env vars + /health pool_saturation field)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Connection pool tuning + health JSON pool fields is implemented, tests pass, and documentation is updated.

---

### 66.8 -- Auto-migrate on startup gate

**Points:** 3

When TAPPS_BRAIN_AUTO_MIGRATE=1 is set, MemoryStore.__init__ runs apply_private_migrations(dsn) before constructing PostgresPrivateBackend. Refuses to auto-migrate when current schema version > bundled migrations (avoids downgrading multi-host deployments). Closes EPIC-059 STORY-059.3.

**Tasks:**
- [x] Implement auto-migrate on startup gate *(TAPPS_BRAIN_AUTO_MIGRATE=1 in store.py)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Auto-migrate on startup gate is implemented, tests pass, and documentation is updated.

---

### 66.9 -- Behavioural parity doc + load smoke benchmark

**Points:** 8

Update docs/engineering/v3-behavioral-parity.md with every intentional delta vs v2 SQLite (audit-on-merge, valid_at semantics, archive flow, dimensions). Add tests/benchmarks/load_smoke_postgres.py simulating 50 concurrent agents against one Postgres for 60 seconds, recording p95 latency for save/recall/hive_search. Closes EPIC-059 STORY-059.6.

**Tasks:**
- [x] Implement behavioural parity doc + load smoke benchmark *(docs/engineering/v3-behavioral-parity.md + tests/benchmarks/load_smoke_postgres.py)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Behavioural parity doc + load smoke benchmark is implemented, tests pass, and documentation is updated.

---

### 66.10 -- pg_tde operator runbook

**Points:** 5

Publish docs/guides/postgres-tde.md covering Percona pg_tde 2.1.2 install, Vault/OpenBao key provider configuration, key rotation procedure, and a fallback table mapping cloud-provider TDE equivalents (RDS, CloudSQL, Azure Database for PostgreSQL). Cross-link from ADR-007 and the security guide.

**Tasks:**
- [x] Implement pg_tde operator runbook *(docs/guides/postgres-tde.md)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** pg_tde operator runbook is implemented, tests pass, and documentation is updated.

---

### 66.11 -- Postgres backup and restore runbook

**Points:** 5

Publish docs/guides/postgres-backup.md covering pg_dump for hot backups, base backup + WAL archiving for point-in-time recovery, restoring private/hive/federation schemas independently, and Hive failover to a replica. Cross-link from docs/guides/hive-deployment.md.

**Tasks:**
- [x] Implement postgres backup and restore runbook *(docs/guides/postgres-backup.md)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Postgres backup and restore runbook is implemented, tests pass, and documentation is updated.

---

### 66.12 -- Engineering docs drift sweep

**Points:** 5

Run docs-mcp docs_check_drift over docs/engineering and docs/guides scoped to all SQLite-related public names. Fix every stale reference. Target: drift_score >= 0.95 over the engineering surface, zero hits when filtered by SQLite/SQLCipher/sqlite-vec/MemoryPersistence name list.

**Tasks:**
- [x] Implement engineering docs drift sweep *(docs/engineering/ updated; no SQLite references remain)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Engineering docs drift sweep is implemented, tests pass, and documentation is updated.

---

### 66.13 -- Postgres integration tests replacing deleted SQLite-coupled tests

**Points:** 8

Recreate behaviour coverage for the 8 deleted SQLite-coupled test files (test_memory_persistence, test_persistence_sqlite_vec, test_sqlite_vec_index, test_sqlite_vec_try_load, test_sqlcipher_util, test_sqlcipher_wiring, test_sqlite_corruption, test_memory_embeddings_persistence, test_feedback, test_store_feedback, test_session_index, test_agent_identity, test_memory_foundation_integration, test_session_index_integration) as Postgres-backed integration tests in tests/integration/. Mark with requires_postgres pytest marker so unit suite stays Docker-free.

**Tasks:**
- [x] Implement postgres integration tests replacing deleted sqlite-coupled tests *(tests/integration/ — 30+ files covering postgres backends, RLS, federation, temporal, feedback)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Postgres integration tests replacing deleted SQLite-coupled tests is implemented, tests pass, and documentation is updated.

---

### 66.14 -- Final test failure sweep — 90 to zero

**Points:** 5

After STORY-066.1 through 066.4 land, re-run uv run pytest tests/unit against ephemeral Postgres and resolve any remaining individual failures. Each remaining failure gets either a fix, a proper @pytest.mark.skip with a tracked follow-up issue, or a test deletion if the behaviour was intentionally removed under ADR-007.

**Tasks:**
- [x] Implement final test failure sweep — 90 to zero
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Final test failure sweep — 90 to zero is implemented, tests pass, and documentation is updated.

---

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Migration 006 (gc_archive) reuses the (project_id
- agent_id) tenant key pattern from migrations 001-005
- GC archive rows include the original entry JSONB plus archived_at timestamp; the existing CLI maintenance gc command queries this table instead of reading archive.jsonl
- Bi-temporal as_of filter on PostgresPrivateBackend.search() adds (valid_at <= as_of OR valid_at IS NULL) AND (invalid_at > as_of OR invalid_at IS NULL) predicates to the existing tsvector WHERE clause
- Consolidation merge audit emission lives in auto_consolidation.py — call self._persistence.append_audit at merge and undo points the same way save/delete already do in store.py
- MCP tool registration failures are pre-existing relative to ADR-007 and likely unrelated; investigate before opening fix tickets
- pg_tde requires configuring a key provider — Vault/OpenBao integration is the documented default per Percona 2.1.2 release notes
- Connection pool tuning uses psycopg_pool ConnectionPool max_size/min_size knobs already exposed in postgres_connection.py — surface them as TAPPS_BRAIN_PG_POOL_MAX/MIN env vars
- Health JSON pool fields come from psycopg_pool.ConnectionPool.get_stats()
- Auto-migrate gate uses TAPPS_BRAIN_AUTO_MIGRATE=1; refuses when current schema version > bundled migrations to avoid downgrading multi-host deployments

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- Migrating existing v2 SQLite user data (pre-GA
- no migration path)
- supporting offline/air-gapped deployments without any Postgres instance
- replacing pgvector with a different ANN library
- replacing tsvector with ParadeDB pg_search (tracked as a separate optional upgrade for when ranking quality becomes the bottleneck)

<!-- docsmcp:end:non-goals -->

<!-- docsmcp:start:success-metrics -->
## Success Metrics

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| Unit suite pass count: 2475+ → 2565+ (90 failures resolved) | - | - | - |
| Integration suite pass count against ephemeral Postgres: 0 failures | - | - | - |
| CI wall-clock for full pytest job: < 15 minutes | - | - | - |
| /health endpoint pool_saturation and last_migration fields populated and observed in production smoke | - | - | - |
| openclaw-skill SKILL.md version test passes | - | - | - |
| docs-mcp drift sweep over docs/engineering and docs/guides: 0 SQLite name matches | - | - | - |
| EPIC-059 acceptance criteria all checked off | - | - | - |

<!-- docsmcp:end:success-metrics -->

<!-- docsmcp:start:stakeholders -->
## Stakeholders

| Role | Person | Responsibility |
|------|--------|----------------|
| tapps-brain maintainers (release tagging) | - | - |
| Hive operators running pgvector/pg17 in Docker | - | - |
| MCP host integrators (Cursor / Claude Code / VS Code) | - | - |
| CI/CD reviewers | - | - |

<!-- docsmcp:end:stakeholders -->

<!-- docsmcp:start:references -->
## References

- docs/planning/adr/ADR-007-postgres-only-no-sqlite.md
- docs/planning/epics/EPIC-059.md
- docs/engineering/v3-behavioral-parity.md
- src/tapps_brain/migrations/private/
- https://docs.percona.com/pg_tde/
- https://www.postgresql.org/docs/17/backup.html
- https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual

<!-- docsmcp:end:references -->

<!-- docsmcp:start:implementation-order -->
## Implementation Order

1. Story 66.1: Consolidation merge audit emission
2. Story 66.2: Bi-temporal as_of filter on PostgresPrivateBackend.search
3. Story 66.3: GC archive Postgres table (migration 006)
4. Story 66.4: MCP tool registration audit and fix
5. Story 66.5: Version consistency unblock for openclaw-skill
6. Story 66.6: CI workflow with ephemeral Postgres service container
7. Story 66.7: Connection pool tuning + health JSON pool fields
8. Story 66.8: Auto-migrate on startup gate
9. Story 66.9: Behavioural parity doc + load smoke benchmark
10. Story 66.10: pg_tde operator runbook
11. Story 66.11: Postgres backup and restore runbook
12. Story 66.12: Engineering docs drift sweep
13. Story 66.13: Postgres integration tests replacing deleted SQLite-coupled tests
14. Story 66.14: Final test failure sweep — 90 to zero

<!-- docsmcp:end:implementation-order -->

<!-- docsmcp:start:risk-assessment -->
## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| pg_tde requires a patched Postgres distribution (Percona Distribution for PostgreSQL 17+) which may not be available on every cloud provider — mitigation: document cloud-provider TDE alternatives (RDS/CloudSQL/Azure) as equally supported paths. Load smoke at 50 concurrent agents may surface psycopg pool contention not visible in unit tests — mitigation: budget time in STORY-066.9 for pool tuning | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| fall back to "informational only" if SLO not yet defined. Auto-migrate on startup is a footgun for multi-host deployments where a stale binary could re-apply a forward migration on a newer schema — mitigation: refuse to auto-migrate when current schema version > bundled migrations. | High | High | Warning: Mitigation required - no automated recommendation available |

<!-- docsmcp:end:risk-assessment -->

<!-- docsmcp:start:files-affected -->
## Files Affected

| File | Story | Action |
|---|---|---|
| Files will be determined during story refinement | - | - |

<!-- docsmcp:end:files-affected -->

<!-- docsmcp:start:performance-targets -->
## Performance Targets

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| Test coverage | baseline | >= 80% | pytest --cov |
| Acceptance criteria pass rate | 0% | 100% | CI pipeline |
| Story completion rate | 0% | 100% | Sprint tracking |

<!-- docsmcp:end:performance-targets -->
