# Next session — agent handoff prompt

Copy everything below the line into a new chat (or Ralph task) as the **user message**.

---

**Project:** tapps-brain (SQLite-backed memory for AI assistants; sync Python core; built-in sqlite-vec hybrid search; optional `[encryption]`; unified `AgentBrain` API; PostgreSQL Hive/Federation backend; Docker deployment support).

**Start by reading:** `CLAUDE.md`, `docs/planning/open-issues-roadmap.md`, `docs/planning/STATUS.md`, then the epic you implement. Canonical product queue is the **open-issues roadmap**, not `.ralph/fix_plan.md`, unless you are explicitly running Ralph.

**Already on `main` — do not redo:**

- **STORY-048.4** (eval CI golden set) — **done**; `scripts/run_eval_golden.py` runs `lexical_golden_eval_suite()` + `evaluate()` and writes `eval-report.json`; `eval-golden` CI job in `.github/workflows/ci.yml` runs on every PR and uploads the JSON artifact via `actions/upload-artifact@v4`.
- **STORY-048.2** (relations) — **done**; `detect_relation_cycles()` in `relations.py`; `RelationEntry.MAX_EDGES_PER_KEY = 20` enforced in `store.save()`; `store.get_relations_batch(keys)` + MCP `memory_relations_get_batch(keys_json)`; `SKILL.md` synced to 69 tools (added `brain_*` tools, removed stale entries); 60 tests pass.
- **STORY-048.1** (session memory) — **done**; `GCConfig.session_index_ttl_days` (default 90) added to `gc.py`; `store.gc()` calls `cleanup_sessions()` on live runs and returns `GCResult.session_chunks_deleted`; `maintenance gc-config --session-index-ttl-days` CLI flag; `session_summary_save(max_chars=)` truncates at word boundary with `" …"` and returns `truncated=True`; 46 tests pass.

- **EPIC-042:** Stories **042.1–042.8** = **done** (rerank observability, sqlite-vec ops doc, `HybridFusionConfig`, v17 `embedding_model_id`, etc.). Epic-level eval/hygiene backlog-gated per PLANNING.md trigger (b).
- **EPIC-044:** All 7 stories **done** — RAG safety, Bloom dedup, conflicts (core + offline export), consolidation merge undo, GC dry-run/metrics/archive, seeding seed_version, per-group caps. Optional NLI/async conflict wiring gated per trigger (c).
- **EPIC-050:** All 3 stories **done** — sync API philosophy doc, lock timeout + `threading.Lock` discipline, WAL checkpoint + opt-in read connection. Lock-scope reduction deferred per ADR-004.
- **#66 async wrapper** — **done** (shipped post-EPIC-050); `src/tapps_brain/aio.py` `AsyncMemoryStore` wraps all public `MemoryStore` methods via `asyncio.to_thread()`; context manager + auto-proxy fallback; 27+ tests in `tests/unit/test_aio.py`; import as `from tapps_brain.aio import AsyncMemoryStore`. GitHub #66 closed.
- **#70 temporal query filtering** — **done**; `MemoryStore._parse_relative_time()` expands `7d`/`2w`/`1m` shorthands; `store.search(since=, until=, time_field=)` wired to SQL pre-filter; MCP `memory_search` gains those params; 11 tests in `TestMemoryStoreTemporalSearch`. GitHub #70 closed.
- **#71 consolidation threshold** — **done**; `ConsolidationProfileConfig.threshold` (profile.py) wired through `store.py` → `auto_consolidation.py`; `personal-assistant.yaml` ships `consolidation.threshold: 0.65`. GitHub #71 closed.
- **EPIC-051:** **done** — §10 checklist decisions ADR-001–006 in `adr/`.
- **EPIC-052:** **done** — 2026-Q2 code review sweep, 6 fixes in v2.0.4.
- **EPIC-053:** **done** (v3.1.0) — `MemoryStore(agent_id=)` routes to `{project_dir}/.tapps-brain/agents/{id}/memory.db`; auto-registration on `HiveStore`; `source_agent` auto-fill on save; CLI/MCP `--agent-id` + `TAPPS_BRAIN_AGENT_ID` env var; `maintenance split-by-agent` migration.
- **EPIC-054:** **done** (v3.1.0) — `HiveBackend` / `FederationBackend` / `AgentRegistryBackend` protocols in `_protocols.py`; `SqliteHiveBackend` / `SqliteFederationBackend` adapters; `create_hive_backend()` / `create_federation_backend()` factories with `TAPPS_BRAIN_HIVE_DSN` / `TAPPS_BRAIN_FEDERATION_DSN` env vars; `PropagationEngine` typed to `HiveBackend`.
- **EPIC-055:** **done** (v3.1.0) — `PostgresHiveBackend` + `PostgresConnectionManager` (`psycopg` sync pool); `pgvector` 384-dim ANN + `tsvector` GIN FTS + `LISTEN/NOTIFY`; SQL migrations in `src/tapps_brain/migrations/hive/` and `migrations/federation/`; `PostgresFederationBackend`; conformance tests (`TAPPS_TEST_POSTGRES_DSN` skipped if unset); CLI `maintenance migrate-hive` / `hive-schema-status`.
- **EPIC-056:** **done** (v3.1.0) — `MemoryStore(groups=[…], expert_domains=[…])` declarative membership; auto group create/join; expert auto-publish on `architectural`/`pattern` tiers; `save(agent_scope="group")` routing; cross-project group resolution; profile YAML `hive.groups` / `hive.expert_domains` / `hive.recall_weights`.
- **EPIC-057:** **done** (v3.1.0) — `AgentBrain` in `src/tapps_brain/agent_brain.py`; `remember()` / `recall()` / `forget()` / `learn_from_success()` / `learn_from_failure()` / `set_task_context()`; context manager `__enter__`/`__exit__`; simplified `brain_*` MCP tools; top-level CLI `tapps-brain remember / recall / forget / status / who-am-i`; `docs/guides/llm-brain-guide.md` + `docs/guides/agent-integration.md`.
- **EPIC-058:** **done** (v3.1.0) — `docker/docker-compose.hive.yaml` (pgvector/pgvector:pg17 + secrets), `docker/init-hive.sql`, `docker/Dockerfile.migrate`, `docker/README.md`; `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` auto-migration with `pg_advisory_lock`; Hive health fields (`hive_connected`, `hive_latency_ms`, pool stats, schema version); `maintenance backup-hive` / `restore-hive`; `docs/guides/hive-deployment.md` + `docs/guides/hive-operations.md`.

**Backlog-by-default (execute only if a trigger fires):**
- **Extra save-path observability** beyond ADR-006 — trigger (a): save-latency incident.
- **EPIC-042 eval/GitHub hygiene** — trigger (b): milestone or stakeholder requires epic closure.
- **In-product NLI/async conflict wiring** — trigger (c): explicit product requirement (never on sync `save`).

**Open work (pick when product needs it):**

| Issue/Epic | What | Notes |
|------|------|-------|
| **EPIC-048** | Optional auxiliary improvements | **048.1 + 048.2 + 048.4 done**; remaining order: **048.3** (markdown) → 048.5 (doc validation) → 048.6 (visual snapshot) |
| **EPIC-032** | OTel GenAI semantic conventions | Low priority; defer unless stakeholder asks |
| Row 22 | MemoryStore modularization | Design-first only; long-term refactor |

**Your task — pick ONE primary slice,** run the epic's verification command, then update `docs/planning/epics/…`, `open-issues-roadmap.md` (changelog + last updated), `STATUS.md` if queue changes, and refresh **this file's** "Already on main" if you ship something.

**Quality bar:** `ruff check` / `ruff format` on touched paths; `mypy --strict src/tapps_brain/` on touched modules; full gate in `CLAUDE.md` if you touch core widely. Tests: `.venv/bin/python -m pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-fail-under=95`.

---

*File purpose: paste-the-prompt handoff. Last synced: 2026-04-09 — EPIC-053–058 complete (v3.1.0); #66/#69/#70/#71/#72 all closed; EPIC-048 in progress (048.1 + 048.2 + 048.4 done).*
