# Next session — agent handoff prompt

Copy everything below the line into a new chat (or Ralph task) as the **user message**.

---

**Project:** tapps-brain (PostgreSQL-backed memory for AI assistants; sync Python core; pgvector HNSW + tsvector hybrid search; unified `AgentBrain` API; `AsyncMemoryStore` async wrapper (`aio.py`); Docker deployment support). All durable stores are PostgreSQL — ADR-007 removed SQLite entirely.

**Start by reading:** `CLAUDE.md`, `docs/planning/open-issues-roadmap.md`, `docs/planning/STATUS.md`, then the epic you implement. Canonical product queue is the **open-issues roadmap**, not `.ralph/fix_plan.md`, unless you are explicitly running Ralph.

**Already on `main` — do not redo:**

- **EPIC-048 — all 6 stories done (2026-04-09):**
  - **048.6** (visual PNG capture) — `capture_png()` in `visual_snapshot.py`; CLI `tapps-brain visual capture --json ... --output ... [--theme dark]`; `[visual]` optional extra (`playwright>=1.45`); `playwright install chromium` required; manual checklist in `docs/guides/visual-snapshot.md`.
  - **048.5** (doc validation) — `StrictValidationError`, `validate_batch(strict=True)`, `store.validate_entries(strict=True)`, `scripts/run_doc_validation.py --strict`; `docs/guides/doc-validation-lookup-engine.md`.
  - **048.3** (markdown round-trip) — `MEMORY_MD_SCHEMA_VERSION = 1` in front matter; import skips block; round-trip test passes.
  - **048.4** (eval CI) — `scripts/run_eval_golden.py`; `eval-golden` CI job; JSON artifact upload.
  - **048.2** (relations) — `detect_relation_cycles()`, `MAX_EDGES_PER_KEY=20`, `store.get_relations_batch()`, MCP `memory_relations_get_batch`.
  - **048.1** (session memory) — `GCConfig.session_index_ttl_days`, `session_summary_save(max_chars=)`.

- **EPIC-042:** Stories **042.1–042.8** = **done** (rerank observability, pgvector ops doc, `HybridFusionConfig`, `embedding_model_id`, etc.). Epic-level eval/hygiene backlog-gated per PLANNING.md trigger (b).
- **EPIC-044:** All 7 stories **done** — RAG safety, Bloom dedup, conflicts (core + offline export), consolidation merge undo, GC dry-run/metrics/archive, seeding seed_version, per-group caps. Optional NLI/async conflict wiring gated per trigger (c).
- **EPIC-050:** All 3 stories **done** — sync API philosophy doc, lock timeout + `threading.Lock` discipline, WAL checkpoint + opt-in read connection. Lock-scope reduction deferred per ADR-004.
- **#66 async wrapper** — **done** (shipped post-EPIC-050); `src/tapps_brain/aio.py` `AsyncMemoryStore` wraps all public `MemoryStore` methods via `asyncio.to_thread()`; context manager + auto-proxy fallback; 27+ tests in `tests/unit/test_aio.py`; import as `from tapps_brain.aio import AsyncMemoryStore`. GitHub #66 closed.
- **#70 temporal query filtering** — **done**; `MemoryStore._parse_relative_time()` expands `7d`/`2w`/`1m` shorthands; `store.search(since=, until=, time_field=)` wired to SQL pre-filter; MCP `memory_search` gains those params; 11 tests in `TestMemoryStoreTemporalSearch`. GitHub #70 closed.
- **#71 consolidation threshold** — **done**; `ConsolidationProfileConfig.threshold` (profile.py) wired through `store.py` → `auto_consolidation.py`; `personal-assistant.yaml` ships `consolidation.threshold: 0.65`. GitHub #71 closed.
- **EPIC-051:** **done** — §10 checklist decisions ADR-001–006 in `adr/`.
- **EPIC-052:** **done** — 2026-Q2 code review sweep, 6 fixes in v2.0.4.
- **EPIC-053:** **done** (v3.1.0) — `MemoryStore(agent_id=)` isolates by `(project_id, agent_id)` row key in Postgres; auto-registration in Hive agent registry; `source_agent` auto-fill on save; CLI/MCP `--agent-id` + `TAPPS_BRAIN_AGENT_ID` env var; `maintenance split-by-agent` migration.
- **EPIC-054:** **done** (v3.1.0) — `HiveBackend` / `FederationBackend` / `AgentRegistryBackend` protocols in `_protocols.py`; `SqliteHiveBackend` / `SqliteFederationBackend` adapters; `create_hive_backend()` / `create_federation_backend()` factories with `TAPPS_BRAIN_HIVE_DSN` / `TAPPS_BRAIN_FEDERATION_DSN` env vars; `PropagationEngine` typed to `HiveBackend`.
- **EPIC-055:** **done** (v3.1.0) — `PostgresHiveBackend` + `PostgresConnectionManager` (`psycopg` sync pool); `pgvector` 384-dim ANN + `tsvector` GIN FTS + `LISTEN/NOTIFY`; SQL migrations in `src/tapps_brain/migrations/hive/` and `migrations/federation/`; `PostgresFederationBackend`; conformance tests (`TAPPS_TEST_POSTGRES_DSN` skipped if unset); CLI `maintenance migrate-hive` / `hive-schema-status`.
- **EPIC-056:** **done** (v3.1.0) — `MemoryStore(groups=[…], expert_domains=[…])` declarative membership; auto group create/join; expert auto-publish on `architectural`/`pattern` tiers; `save(agent_scope="group")` routing; cross-project group resolution; profile YAML `hive.groups` / `hive.expert_domains` / `hive.recall_weights`.
- **EPIC-057:** **done** (v3.1.0) — `AgentBrain` in `src/tapps_brain/agent_brain.py`; `remember()` / `recall()` / `forget()` / `learn_from_success()` / `learn_from_failure()` / `set_task_context()`; context manager `__enter__`/`__exit__`; simplified `brain_*` MCP tools; top-level CLI `tapps-brain remember / recall / forget / status / who-am-i`; `docs/guides/llm-brain-guide.md` + `docs/guides/agent-integration.md`.
- **EPIC-058:** **done** (v3.1.0) — `docker/docker-compose.hive.yaml` (pgvector/pgvector:pg17 + secrets), `docker/init-hive.sql`, `docker/Dockerfile.migrate`, `docker/README.md`; `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` auto-migration with `pg_advisory_lock`; Hive health fields (`hive_connected`, `hive_latency_ms`, pool stats, schema version); `maintenance backup-hive` / `restore-hive`; `docs/guides/hive-deployment.md` + `docs/guides/hive-operations.md`.
- **EPIC-070 — all 7 stories done (2026-04-14, commit f182700):**
  - **70.1** — Service layer extracted: `src/tapps_brain/services/` (10 modules); `mcp_server.py` reduced from 2807 → 1452 lines.
  - **70.2** — FastMCP `stateless_http=True, json_response=True`; `mcp>=1.25` in `http` extra.
  - **70.3** — `http_adapter.py` rewritten as FastAPI; `BaseHTTPRequestHandler` removed.
  - **70.4** — `/mcp` mounted via `streamable_http_app()`; tenant middleware (`X-Project-Id`, `X-Agent-Id`, Bearer, Origin allowlist).
  - **70.5** — `tests/test_http_mcp_parity.py` parity test via `httpx.ASGITransport`.
  - **70.6** — `docker/Dockerfile.http` runs `uvicorn :8080 --workers 1`; compose updated.
  - **70.7** — `examples/agentforge-client.py` + `docs/guides/remote-mcp-integration.md`.
- **EPIC-066 — stories 66.1–66.5 and 66.14 resolved (2026-04-14):**
  - Root causes fixed: hive migrations 001+002 applied; error shape assertions relaxed.
  - 0 failures against live Postgres (was 46); 2777 passed (live Postgres mode).

**Backlog-by-default (execute only if a trigger fires):**
- **Extra save-path observability** beyond ADR-006 — trigger (a): save-latency incident.
- **EPIC-042 eval/GitHub hygiene** — trigger (b): milestone or stakeholder requires epic closure.
- **In-product NLI/async conflict wiring** — trigger (c): explicit product requirement (never on sync `save`).

**Open work (pick when product needs it):**

| Issue/Epic | What | Notes |
|------|------|-------|
| **EPIC-066** | Postgres-Only Persistence Plane — production readiness | **In Progress** — 0 unit failures against live Postgres; remaining: stories 66.6 (CI workflow), 66.7 (pool tuning), 66.8 (auto-migrate), 66.9 (parity doc + benchmark), 66.10 (pg_tde runbook), 66.11 (backup runbook), 66.12 (docs drift sweep), 66.13 (Postgres integration tests) |
| **EPIC-048** | Optional auxiliary improvements | Complete (2026-04-09) — all 6 stories done |
| **EPIC-032** | OTel GenAI semantic conventions | Low priority; defer unless stakeholder asks |
| Row 22 | MemoryStore modularization | Design-first only; long-term refactor |

**Next priority: EPIC-066 remaining stories 66.6–66.13**

Story order to execute:
1. **66.6** — CI workflow with ephemeral Postgres service container (GitHub Actions `pgvector/pgvector:pg17` service)
2. **66.7** — Connection pool tuning + `/health` JSON pool fields (`TAPPS_BRAIN_PG_POOL_*` env vars)
3. **66.8** — Auto-migrate on startup gate (`TAPPS_BRAIN_AUTO_MIGRATE=1`)
4. **66.9** — Behavioural parity doc + load smoke benchmark (50 concurrent agents, p95 latency)
5. **66.10** — pg_tde operator runbook (`docs/guides/postgres-tde.md`)
6. **66.11** — Postgres backup and restore runbook (`docs/guides/postgres-backup.md`)
7. **66.12** — Engineering docs drift sweep (zero SQLite name hits in docs/engineering + docs/guides)
8. **66.13** — Postgres integration tests replacing deleted SQLite-coupled tests

**Your task — pick ONE primary slice,** run the epic's verification command, then update `docs/planning/epics/…`, `open-issues-roadmap.md` (changelog + last updated), `STATUS.md` if queue changes, and refresh **this file's** "Already on main" if you ship something.

**Quality bar:** `ruff check` / `ruff format` on touched paths; `mypy --strict src/tapps_brain/` on touched modules; full gate in `CLAUDE.md` if you touch core widely. Tests: `.venv/bin/python -m pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-fail-under=95`.

**Test suite status (2026-04-14):**
- InMemory mode: **2940 passed**, 0 failures
- Live Postgres mode: **2777 passed**, 0 failures

---

*File purpose: paste-the-prompt handoff. Last synced: 2026-04-14 — **v3.5.x**: EPIC-070 complete (all 7 stories, commit f182700) — Streamable HTTP + service layer; EPIC-066 in progress (0 Postgres unit failures, stories 66.6–66.13 remain).*
