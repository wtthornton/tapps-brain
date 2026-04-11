# Ralph Fix Plan — tapps-brain

**Scope:** Greenfield v3 (EPIC-059–063), then optional EPIC-032.

**Not packaged:** This file and all of `.ralph/` are **Ralph / dev-loop only** — not part of the PyPI wheel or OpenClaw artifacts. **Canonical delivery status** lives in `docs/planning/open-issues-roadmap.md`. See [Open issues roadmap vs Ralph tooling](../docs/planning/PLANNING.md#open-issues-roadmap-vs-ralph-tooling).

**Task sizing:** Each `- [ ]` is ONE Ralph loop (~15 min) unless marked `[BATCH-N: SMALL]`.  
**QA strategy:** Run full QA **only** at lines marked `🔒 QA GATE`. Everything else → `TESTS_STATUS: DEFERRED`.

## Completed Epics

<details><summary>All prior work (EPIC-001–058, BUG-001/002, HOUSEKEEPING, QUALITY, READY, OR-1–11)</summary>

- EPIC-001–016, BUG-001/002, EPIC-017–025, EPIC-026–031, EPIC-033–038, EPIC-040 (22 tasks), EPIC-041–058
- HOUSEKEEPING-001/002, QUALITY-001, READY-034/035/036: all [x]
- OR-1–11: all [x] (GitHub #21 store stale + #20 profile migrate closed 2026-03-28)

</details>

---

## Next Tasks

---

## EPIC-059: Greenfield v3 — Postgres-Only Persistence Plane

**Priority: CRITICAL — foundation for ALL v3 epics**  
**Read first:** `docs/planning/epics/EPIC-059.md`, `docs/planning/adr/ADR-007-postgres-only-no-sqlite.md`

> **Pre-existing work:** `backends.py` factories already reject non-Postgres DSN. `SqliteHiveBackend`/`SqliteFederationBackend` removed from factories. Postgres modules (`postgres_hive.py`, `postgres_federation.py`, `postgres_connection.py`, `postgres_migrations.py`) exist. Migrations: `hive/001_initial.sql`, `federation/001_initial.sql`. MCP server already uses env DSN. **BUT:** `hive.py` (SQLite HiveStore, ~1200 lines) still in-tree. Docs still reference SQLite paths.

### Phase A: Factory + SQLite removal (059.1–059.3) <!-- id: 059-phase-a -->

- [x] **059.1** Postgres-only factory contracts — verify/harden `create_hive_backend` / `create_federation_backend` accept only `postgres://` / `postgresql://`; typed exception on invalid/missing; focused unit tests in `tests/unit/test_backend_factory.py`. [BATCH: SMALL — factories largely done; verify + add missing edge-case tests] <!-- resolved: src/tapps_brain/backends.py, tests/unit/test_backend_factory.py -->
- [x] **059.2** Remove SQLite shared-store code + dead tests — Run `mcp__tapps-mcp tapps_impact_analysis` on each file before deleting. Remove: **`hive.py`** (`HiveStore`, `AgentRegistry` SQLite, `PropagationEngine` if SQLite-only — ~1200 lines), **`federation.py`** (`FederatedStore` SQLite hub). Update callers (`cli.py`, `mcp_server.py`, `store.py`, `health_check.py`, `visual_snapshot.py`) to use Postgres backends only. Clean **`__init__.py`**: remove `FederatedStore`, `FederationConfig` re-exports; audit `__all__` for any other SQLite symbols. Delete/rewrite tests: `tests/unit/test_hive.py`, `test_hive_groups.py`, `test_hive_memory_group.py`, `test_federation.py`, `tests/integration/test_hive_integration.py`, `test_hive_mcp_roundtrip.py`, `test_federation_integration.py`. [LARGE — cross-module deletion with blast radius] <!-- resolved: src/tapps_brain/hive.py, src/tapps_brain/federation.py, src/tapps_brain/__init__.py, tests/ -->
- [x] **059.3** No silent SQLite in runtime + v3 doc sweep — **Code:** audit MCP/CLI for remaining `HiveStore()` / `FederatedStore()` / `memory.db` construction; startup fails in strict mode without DSN. **Docs (explicit list — grep for `hive.db`, `federated.db`, `memory.db`, `SqliteHive`, `SqliteFederation`, `SQLite` in each):** `README.md`, `CLAUDE.md`, `docs/engineering/system-architecture.md`, `docs/engineering/data-stores-and-schema.md`, `docs/engineering/features-and-technologies.md`, `docs/guides/hive.md`, `docs/guides/hive-deployment.md`, `docs/guides/hive-vs-federation.md`, `docs/guides/federation.md`, `docs/guides/memory-scopes.md`, `docs/guides/sqlcipher.md`, `docs/guides/sqlite-database-locked.md`, `docs/guides/observability.md`, `docs/guides/openclaw.md`, `docs/planning/STATUS.md`, `docs/planning/DESIGN-CONFIGURABLE-MEMORY-PROFILES.md`, `docs/DOCUMENTATION_INDEX.md`. Link ADR-007 from architecture overview; cross-link ADR-004 → ADR-007 narrowing. Run `mcp__docs-mcp docs_check_cross_refs` after doc edits to catch broken links. [LARGE — ~20 files to audit/edit] <!-- resolved: see file list above -->

### Phase B: Private memory on Postgres (059.4–059.6) <!-- id: 059-phase-b -->

- [x] **059.4** Private memory — schema + migrations — Postgres tables keyed by `(project_id, agent_id)`; forward-only `migrations/private/001_initial.sql`; clean apply on empty DB; revision tracking. [LARGE — new schema design] <!-- resolved: src/tapps_brain/migrations/, src/tapps_brain/persistence.py -->
- [x] **059.5** Private memory — indexes + store wiring — recall/BM25-adjacent indexes; `MemoryStore` reads/writes private rows through Postgres backend; no `.tapps-brain/agents/<id>/memory.db` in v3 layout. [LARGE — core wiring] <!-- resolved: src/tapps_brain/store.py, src/tapps_brain/persistence.py -->
- [x] **059.6** Behavioral parity doc + load smoke — short markdown: what matches v2 (decay, consolidation, safety) vs what changed; benchmark script for N concurrent agents; p95 or "informational." <!-- resolved: docs/engineering/, scripts/ or tests/benchmarks/ -->

### Phase C: Config, onboarding, CI (059.7–059.8) <!-- id: 059-phase-c -->

- [x] **059.7** DSN table + pool tuning + health fields — single env-var table (README or `docs/guides/`); `TAPPS_BRAIN_DATABASE_URL` or split Hive/Federation DSNs — one story; pool config (max conn, idle, timeout) env-configurable with defaults; health/readiness JSON exposes pool saturation + last migration version; malformed URL → clear error; unit tests. <!-- resolved: src/tapps_brain/postgres_connection.py, src/tapps_brain/health_check.py, README.md -->
- [x] **059.8** Compose + Makefile + CI + onboarding — repo-root `docker-compose.yml` (or profile) for Postgres + pgvector; Makefile/justfile targets (`brain-up`, `brain-down`, `brain-test`); CI test job on ephemeral Postgres (service container or Testcontainers); "clone → compose → pytest ≤ 15 min" docs in AGENTS.md/README. <!-- resolved: docker-compose.yml, Makefile, .github/workflows/ci.yml, AGENTS.md -->

- [x] **059.CLEAN** Dead code + doc drift check — Run `mcp__tapps-mcp tapps_dead_code` on `src/tapps_brain/`; run `mcp__docs-mcp docs_check_drift`; run `mcp__tapps-mcp tapps_dependency_graph` to verify no circular imports after hive.py/federation.py removal; fix any remaining SQLite references found. Run `mcp__tapps-mcp tapps_checklist` with `task_type: "epic"`. [SMALL — automated sweeps] <!-- resolved: src/tapps_brain/, docs/ -->

🔒 **QA GATE — EPIC-059 complete.** Run: `pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95 && ruff check src/ tests/ && ruff format --check src/ tests/ && mypy --strict src/tapps_brain/`

---

## EPIC-060: Greenfield v3 — Agent-First Core & Minimal Runtime API

**Priority: CRITICAL — depends on EPIC-059**  
**Read first:** `docs/planning/epics/EPIC-060.md`

> **Pre-existing work:** `agent_brain.py` exists with full facade (remember/recall/forget/learn_from_*). `health_check.py` exists (models, not HTTP). No `http_adapter.py` yet.

### Phase D: API contract + HTTP adapter (060.1–060.6) <!-- id: 060-phase-d -->

- [x] **060.1** Agent integration page — API surface — new/refreshed doc listing `AgentBrain` public methods + env vars table; cross-links from README/AGENTS.md. [SMALL — docs only] <!-- resolved: docs/guides/agent-integration.md -->
- [x] **060.2** Agent integration page — exceptions + breaking changes — typed exception taxonomy (config vs transient vs validation); v3 breaking changes subsection; optional mypy re-export check. [SMALL — docs + spot-check code] <!-- resolved: docs/guides/agent-integration.md, src/tapps_brain/agent_brain.py -->
- [x] **060.3** HTTP adapter — liveness, readiness, metrics — `/health` (no DB), `/ready` (DB ping + migration version), `/metrics` (Prometheus or OTel); contract tests (status codes for healthy vs DB-down). [MEDIUM — new module] <!-- resolved: src/tapps_brain/http_adapter.py (new) -->
- [x] **060.4** HTTP adapter — optional routes + auth + OpenAPI — ≤ 2 extra routes; auth middleware; OpenAPI spec ≤ 1 page; fuzz 401/403 on protected routes. [MEDIUM] <!-- resolved: src/tapps_brain/http_adapter.py -->
- [x] **060.5+060.6** ADR (no HTTP without MCP+library parity) + guardrails (CODEOWNERS or CI for HTTP tree) — commit ADR; link from engineering docs; CODEOWNERS entry or CI script; documented in contributing. [BATCH-2: SMALL — two XS tasks merged] <!-- resolved: docs/planning/adr/, .github/CODEOWNERS -->

### Phase E: Host guide (060.7–060.8) <!-- id: 060-phase-e -->

- [x] **060.7+060.8** Host guide rewrite + diagram — rewrite `agentforge-integration.md` for v3 Postgres DSN flow; step-by-step; non-goals; mermaid diagram (Agent → AgentBrain → Postgres). [BATCH-2: SMALL — two S tasks, same file] <!-- resolved: docs/guides/agentforge-integration.md -->

- [x] **060.CLEAN** Doc cross-refs + quality — Run `mcp__docs-mcp docs_validate_epic` on `EPIC-060.md`; run `mcp__docs-mcp docs_check_cross_refs` on `docs/guides/`; verify new docs (agent-integration, agentforge, ADR) pass `mcp__docs-mcp docs_check_style`. Run `mcp__tapps-mcp tapps_quality_gate` on `http_adapter.py` (if new). [SMALL — automated checks] <!-- resolved: docs/, src/tapps_brain/ -->

🔒 **QA GATE — EPIC-060 complete.** Full QA suite.

---

## EPIC-061: Greenfield v3 — Observability-First Product

**Priority: CRITICAL — depends on EPIC-059**  
**Read first:** `docs/planning/epics/EPIC-061.md`

> **Pre-existing work:** `otel_exporter.py` exists (bridges MetricsSnapshot → OTel metrics; returns None without SDK). `health_check.py` has models. No HTTP probes, no trace spans on remember/recall yet.

### Phase F: Traces + metrics + probes (061.1–061.5) <!-- id: 061-phase-f -->

- [x] **061.1** Traces — remember/recall/hive hot paths — tracer spans with names from `docs/engineering/system-architecture.md`; `service.name`/`service.version` from env; unit tests with `InMemorySpanExporter`. [MEDIUM] <!-- resolved: src/tapps_brain/otel_tracer.py (new), src/tapps_brain/store.py, tests/unit/test_otel_tracer.py -->
- [x] **061.2** Metrics — duration, errors, pool, bounded labels — histograms/counters for ops; **no** raw text/keys as labels (document allowed set); wired to metrics export. [MEDIUM] <!-- resolved: src/tapps_brain/otel_exporter.py, src/tapps_brain/metrics.py, src/tapps_brain/store.py, tests/unit/test_otel_exporter.py -->
- [x] **061.3** Trace context — HTTP adapter + OTel review — W3C `traceparent` through EPIC-060 HTTP adapter; OTel SDK pattern review note; integration test (request with trace header → child span). [SMALL — depends on 060.3 existing] <!-- resolved: src/tapps_brain/http_adapter.py, src/tapps_brain/otel_exporter.py -->
- [x] **061.4+061.5** Probes — liveness + readiness — `/health` returns 200 without DB; `/ready` with DB ping + migration version or `degraded` JSON; 503 vs 500 documented; K8s probe docs. [BATCH-2: SMALL — two XS/S tasks, same `health_check.py`] <!-- resolved: src/tapps_brain/health_check.py -->

### Phase G: Redaction + runbook (061.6–061.8) <!-- id: 061-phase-g -->

- [x] **061.6** Policy doc — allowed vs forbidden telemetry — markdown policy: allowed span attributes; forbidden (memory body, secrets, PII); PR template review slot. [SMALL — docs only] <!-- resolved: docs/operations/ or docs/engineering/ -->
- [x] **061.7** Enforcement — log handler + metric views — log formatter strips/hashes memory bodies; OTel Views drop high-cardinality labels; static/unit test: forbidden strings never appear. [MEDIUM] <!-- resolved: src/tapps_brain/otel_exporter.py -->
- [x] **061.8** Operator runbook + example alerts — `docs/operations/` runbook ≤ 2 pages; optional Prometheus rules / Grafana JSON as non-normative examples. [SMALL — docs only] <!-- resolved: docs/operations/ -->

- [x] **061.CLEAN** OTel code quality + doc validation — Run `mcp__tapps-mcp tapps_quality_gate` on `otel_exporter.py`; run `mcp__docs-mcp docs_check_style` on new runbook and policy doc; verify `docs/engineering/system-architecture.md` span names match code. [SMALL — automated checks] <!-- resolved: src/tapps_brain/otel_exporter.py, docs/operations/ -->

🔒 **QA GATE — EPIC-061 complete.** Full QA suite.

---

## EPIC-063: Greenfield v3 — Trust Boundaries & Postgres Enforcement

**Priority: HIGH — depends on EPIC-059; STORY-063.5 depends on EPIC-060 STORY-060.2**  
**Read first:** `docs/planning/epics/EPIC-063.md`

> **Why before EPIC-062:** EPIC-063 DB roles (063.1) and threat model (063.8) have no dependency on EPIC-060 HTTP routes or EPIC-062 MCP wiring. Running security stories early catches schema issues before MCP wires up. EPIC-062 needs 060's stable exceptions (062.1 depends on 059.1 + env table 059.7), so 063 naturally slots in first.

### Phase H: DB roles + threat model (063.1–063.2, 063.8) <!-- id: 063-phase-h -->

- [x] **063.8** Threat model — STRIDE one-pager — STRIDE bullets (spoofing, tampering, repudiation, info disclosure, DoS, elevation) with mitigation references; explicit v3.0 out-of-scope. [SMALL — docs, no code deps] <!-- resolved: docs/engineering/threat-model.md -->
- [x] **063.1+063.2** DB roles — migration SQL + runbooks — create `tapps_runtime` (DML), `tapps_migrator` (DDL), optional `tapps_readonly`; GRANT/REVOKE idempotent; migration README; runbook snippet (runtime vs migrator DSN); no DSN in logs; env/secret injection. [BATCH-2: SMALL — two S tasks, same migration + runbook] <!-- resolved: src/tapps_brain/migrations/, docs/operations/ -->

### Phase I: RLS spike (063.3–063.4) <!-- id: 063-phase-i -->

- [x] **063.3** RLS spike — policy on one table — ENABLE RLS on hive entries; policy on `project_id`/`org_id`; connection sets session var; integration test (two projects can't read each other). Run `mcp__tapps-mcp tapps_security_scan` on migration SQL. [MEDIUM — requires Postgres] <!-- resolved: src/tapps_brain/migrations/, tests/integration/ -->
- [x] **063.4** RLS spike — performance + ship/defer decision — benchmark % overhead before/after; ADR update: ship RLS in GA vs defer with compensating controls; document if defer. [MEDIUM] <!-- resolved: docs/planning/adr/, scripts/ -->

### Phase J: Scope audit (063.5–063.7) <!-- id: 063-phase-j -->

- [x] **063.5+063.6** Scope audit — matrix doc + code checklist — `agent_scope`/group/hive → allowed namespaces/ops matrix; linked from hive.md; checklist table: path → scope rule → reviewer; gaps filed as GitHub issues. [BATCH-2: SMALL — two S/M tasks, tightly coupled] <!-- resolved: docs/guides/scope-audit.md -->
- [x] **063.7** Scope audit — negative tests — wrong `agent_id` cannot write cross-tenant; wrong group rejected on propagate; peer review sign-off. [MEDIUM] <!-- resolved: tests/integration/ or tests/unit/ -->

- [x] **063.CLEAN** Security scan + doc validation — Run `mcp__tapps-mcp tapps_security_scan` on `src/tapps_brain/postgres_hive.py`, `postgres_connection.py`, migrations; run `mcp__docs-mcp docs_validate_epic` on `EPIC-063.md`; run `mcp__docs-mcp docs_check_cross_refs` on `docs/guides/hive.md` (scope matrix should be linked). [SMALL — automated checks] <!-- resolved: src/tapps_brain/, docs/ -->

🔒 **QA GATE — EPIC-063 complete.** Full QA suite.

---

## EPIC-062: Greenfield v3 — MCP-Primary Integration & Environment Contract

**Priority: HIGH — depends on EPIC-059, EPIC-060**  
**Read first:** `docs/planning/epics/EPIC-062.md`

> **Why last among v3:** 062 wires MCP to the same Postgres backend + env contract as AgentBrain. It needs stable factories (059), exceptions (060.2), and env table (059.7) first. Running 063 before 062 catches security issues before MCP is public.

### Phase K: MCP wiring (062.1–062.4) <!-- id: 062-phase-k -->

- [x] **062.1** MCP — Hive backend from unified DSN — `_get_store` uses `create_hive_backend` from env; no `HiveStore()` SQLite; unit test (env set → Postgres; unset + strict → error). [SMALL — MCP already uses env; verify + harden] <!-- resolved: src/tapps_brain/mcp_server.py -->
- [x] **062.2** MCP — strict vs non-strict startup — `TAPPS_BRAIN_STRICT=1` → missing DSN exits with clear message (stderr + non-zero); non-strict documented as not-for-prod (or removed). [SMALL] <!-- resolved: src/tapps_brain/mcp_server.py -->
- [x] **062.3** MCP — freeze core tool list — document core agent tool set in PR; regenerate `docs/generated/mcp-tools-manifest.json`; OpenClaw consistency. [SMALL — inventory + manifest] <!-- resolved: src/tapps_brain/mcp_server.py, docs/generated/ -->
- [x] **062.4** MCP — operator tools behind flag — advanced/maintenance tools behind `--enable-operator-tools` or profile flag; default session excludes them; doc paragraph for operators. [MEDIUM] <!-- resolved: src/tapps_brain/mcp_server.py -->

### Phase L: Env contract + CI (062.5–062.8) <!-- id: 062-phase-l -->

- [x] **062.5+062.6** Env contract — markdown table + links — single table: variable | meaning | example | required (prod) | required (dev); linked from README, AGENTS.md, agentforge-integration.md; `.env.example` matches. [BATCH-2: SMALL — two XS/S tasks, same deliverable] <!-- resolved: docs/guides/, README.md, AGENTS.md, .env.example -->
- [x] **062.7+062.8** CI — docs_validate_epic + regression test — CI step on changed EPIC-059–063 files; blocks merge; documented check that broken frontmatter → CI red; optional smoke script. [BATCH-2: SMALL — two XS/S tasks, same workflow] <!-- resolved: .github/workflows/, scripts/ -->

- [x] **062.CLEAN** MCP manifest + env contract validation — Run `mcp__tapps-mcp tapps_checklist` with `task_type: "epic"`; verify `mcp-tools-manifest.json` matches actual tools; run `mcp__docs-mcp docs_check_cross_refs` on `docs/guides/mcp.md` and env contract page; verify `.env.example` keys match table. [SMALL — automated checks] <!-- resolved: docs/generated/, docs/guides/, .env.example -->

🔒 **QA GATE — EPIC-062 complete.** Full QA suite.

---

## EPIC-032: OTel GenAI Semantic Conventions (LOW — deferred until v3 core done)

**Priority: LOW — optional observability upgrade**  
**Read first:** `docs/planning/epics/EPIC-032.md`

> **Pre-existing work:** `otel_exporter.py` exists with `OTelExporter` + `create_exporter()`. No GenAI semconv attributes yet.

### Phase M: Foundation + spans (032.1–032.4) <!-- id: 032-phase-m -->

- [x] **032.1** Tracer bootstrap + null-object — `OTelConfig` (enabled, service_name); TracerProvider from env; no-op when `HAS_OTEL=False`; zero-allocation on hot path. [SMALL] <!-- resolved: src/tapps_brain/otel_exporter.py -->
- [x] **032.2** MCP tool call spans — span name `{mcp.method.name} {gen_ai.tool.name}`; SERVER kind; semconv v1.35.0 attributes; unit tests with mocked SDK. [MEDIUM] <!-- resolved: src/tapps_brain/otel_exporter.py, src/tapps_brain/mcp_server.py -->
- [x] **032.3** Retrieval document events + W3C traceparent — structured events per recall result (`id` + `score`); `params._meta.traceparent` extraction; unit tests. [MEDIUM] <!-- resolved: src/tapps_brain/otel_exporter.py -->
- [ ] **032.4** Non-retrieval spans (save, delete, reinforce, etc.) — `gen_ai.operation.name = "execute_tool"`; unit tests per op type. [SMALL — parallel after 032.1] <!-- resolved: src/tapps_brain/otel_exporter.py -->

### Phase N: Metrics + events + privacy (032.5–032.9) <!-- id: 032-phase-n -->

- [ ] **032.5** Standard GenAI + MCP metrics — `gen_ai.client.operation.duration`, `mcp.server.operation.duration`, token usage histogram; unit tests. [SMALL] <!-- resolved: src/tapps_brain/otel_exporter.py, src/tapps_brain/metrics.py -->
- [ ] **032.6** Custom `tapps_brain.*` metrics + export hook — entry count, consolidation/GC gauges; cardinality: never `entry_key`/`query`/`session_id` as labels; export on snapshot. [SMALL] <!-- resolved: src/tapps_brain/otel_exporter.py -->
- [ ] **032.7+032.8** Feedback + diagnostics events as OTel Events — `tapps_brain.feedback.*` + `tapps_brain.diagnostics.*` events; graceful skip when modules absent; unit tests. [BATCH-2: SMALL — mirror pattern, same test structure] <!-- resolved: src/tapps_brain/otel_exporter.py -->
- [ ] **032.9** Privacy controls + OTelConfig from environment — `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` modes; tapps-brain env vars; attribute omitted (not placeholder) when disabled. [SMALL] <!-- resolved: src/tapps_brain/otel_exporter.py -->

### Phase O: Integration tests (032.10) <!-- id: 032-phase-o -->

- [ ] **032.10** End-to-end integration tests — recall spans + metrics + privacy modes + `HAS_OTEL=False`; real MemoryStore + mocked collectors; optional feedback/diagnostics events. [MEDIUM] <!-- resolved: tests/integration/test_otel_integration.py -->

🔒 **QA GATE — EPIC-032 complete.** Full QA suite. `release-ready.sh` if publishing.

---

## EPIC-064: Product surface — narrative motion, deep insight, NLT brand fidelity

**Priority: MEDIUM — parallel polish; does not block v3 (059–063)**  
**Read first:** `docs/planning/epics/EPIC-064.md` — **research:** W3C WCAG 2.3.3 / C39, MDN scroll-driven animations + `prefers-reduced-motion`, web.dev; **tooling:** `mcp__docs-mcp` (`docs_validate_epic`, `docs_check_cross_refs`, `docs_check_style`, `docs_check_drift`) + `mcp__tapps-mcp` (`tapps_checklist`, `tapps_impact_analysis`, `tapps_quality_gate`, `tapps_dead_code`, `tapps_dependency_graph` when Python surface changes). See epic § *MCP + web coverage*.

> **Why:** Greenfield epics are ops/infrastructure dense. The tapps **frontend** in-repo (`examples/brain-visual/`) must **sell** the product: story-first IA, purposeful motion, insight panels (retrieval/diagnostics/privacy), and **audited alignment** to canonical **NLT Labs style sheets + logo packs** (guide is referenced in code but lives outside the repo today). **tapps-mcp** and **docs-mcp** are sibling tooling ([tapps-mcp](https://github.com/tapps-mcp/tapps-mcp)); wire per story verification in the epic—same pattern as EPIC-059 `.CLEAN` / EPIC-062 `.CLEAN`.

### Phase P: Brand audit + narrative + motion (064.1–064.4) <!-- id: 064-phase-p -->

- [ ] **064.1** NLT Labs — style sheet & logo pack audit — locate canonical `BRAND-STYLE-GUIDE` (or successor) + logo pack; gap matrix vs `index.html` / `nlt-an-mark-sm.svg` / `VisualThemeTokens`; optional redistributable subset under `docs/design/nlt-brand/` + README link, or documented internal fetch path. Run `mcp__docs-mcp docs_check_cross_refs` + `docs_check_style` on new/edited docs. [MEDIUM — docs + inventory] <!-- resolved: docs/planning/ or docs/design/, examples/brain-visual/README.md -->
- [ ] **064.2** Narrative & IA refresh — decision-first copy, story beats order, microcopy tied to real snapshot fields (no vague “RAG” hype). [MEDIUM] <!-- resolved: examples/brain-visual/index.html -->
- [ ] **064.3** Motion system — CSS motion tokens from brand audit; transform/opacity only; `prefers-reduced-motion` gates; drawer/modal audit. [LARGE — CSS/JS] <!-- resolved: examples/brain-visual/index.html -->
- [ ] **064.4** Deep insight panels — stepped explainer for diagnostics or retrieval stack; privacy footer three-bullet pattern; help articles for new concepts. [LARGE] <!-- resolved: examples/brain-visual/, brain-visual-help.js -->

### Phase Q: Demo + discovery + clean (064.5–064.6, 064.CLEAN) <!-- id: 064-phase-q -->

- [ ] **064.5** Demo snapshot + first-run — `brain-visual.demo.json` (synthetic) + load path or “Load demo” control; README note. [SMALL] <!-- resolved: examples/brain-visual/ -->
- [ ] **064.6** README / docs index CTA — “See it” subsection + cross-links. [SMALL — docs] <!-- resolved: README.md, docs/DOCUMENTATION_INDEX.md or docs/guides/visual-snapshot.md -->

- [ ] **064.CLEAN** Doc + a11y + MCP gate — `mcp__docs-mcp docs_validate_epic` on `EPIC-064.md`; `docs_check_cross_refs` + `docs_check_style` on epic-touched `docs/` subtrees; `docs_check_drift` if `DOCUMENTATION_INDEX.md` / engineering maps changed. `mcp__tapps-mcp tapps_checklist` (`task_type: "epic"`); if Python changed: `tapps_impact_analysis` per file, `tapps_quality_gate` on `visual_snapshot.py`, and `tapps_dead_code` + `tapps_dependency_graph` when imports/public API change. Lighthouse (or equivalent) accessibility spot-check recorded. Confirm epic **Research notes** retain W3C + MDN/web.dev anchors. [SMALL] <!-- resolved: docs/planning/epics/EPIC-064.md, docs/, src/tapps_brain/ if touched -->

🔒 **QA GATE — EPIC-064 complete.** No Python coverage gate unless `visual_snapshot.py` (or other `src/tapps_brain/`) changes—then run scoped `pytest` for touched modules + full suite if behavior contract changes. Otherwise manual + **docs-mcp** + **tapps-mcp** per epic + optional Playwright smoke if added later.

---

## Deferred (not in current scope)

| Epic | Title | Notes |
|------|-------|-------|
| DEPLOY-OPENCLAW | PyPI publish + ClawHub listing | 8 tasks, distribution |
| MemoryStore modularization | Long-term refactor | Backlog; no epic number |
