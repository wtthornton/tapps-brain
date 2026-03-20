# Ralph Fix Plan — tapps-brain

Aligned with the repo as of **2026-03-20**. For full story text, see `docs/planning/epics/EPIC-*.md`.

## High Priority (Critical/High epics)

### EPIC-008: MCP Server (Critical) — remaining work

**Already in tree:** `src/tapps_brain/mcp_server.py` (FastMCP, `--project-dir`, `python -m tapps_brain.mcp_server`); tools: `memory_save`, `memory_get`, `memory_delete`, `memory_search`, `memory_list`, `memory_recall`, `memory_reinforce`, `memory_ingest`, `memory_supersede`, `memory_history`; resources: `memory://stats`, `memory://health`, `memory://entries/{key}`, `memory://metrics`; `tests/unit/test_mcp_server.py`; `[project.optional-dependencies] mcp` and `mcp` in `[dev]`.

- [x] Add **`tapps-brain-mcp` console script** in `pyproject.toml` pointing at `tapps_brain.mcp_server:main`.
- [x] **MCP prompts** (`@mcp.prompt`): recall / store-summary / remember workflows (STORY-008.6).
- [x] **Federation & maintenance tools** (STORY-008.5): federation status/subscribe/unsubscribe/publish, consolidate, GC, export/import.
- [x] **MCP integration tests** — protocol-level coverage (e.g. `tests/integration/test_mcp_integration.py`): initialize, tools/list, tools/call, resources, prompts when added.
- [ ] **User-facing MCP docs** with runnable client examples (e.g. `docs/guides/mcp.md` or README section; update `docs/planning/epics/EPIC-008.md` as needed).

### EPIC-006: Knowledge Graph (High) — remaining work

**Partially in tree:** SQLite `relations` table and `Persistence.save_relation` / `list_relations`; `relations.py` extraction + `RetrievalEngine` query expansion from persisted relations when enabled. **`MemoryStore` does not persist relations on save/ingest**; no `find_related` / `query_relations` on the store; recall graph boost and supersede/consolidation relation transfer per epic are not done.

- [ ] Auto-extract and persist relations from **`MemoryStore.save`** and **`ingest_context`** (and cold/load story as in EPIC-006).
- [ ] **Graph query API** on the store: `find_related`, `query_relations` (and helpers like `get_relations` if specified in epic).
- [ ] **Recall scoring boost** via graph (`RecallConfig`, connected entities).
- [ ] **Relation transfer** on supersede and consolidation (merge/dedupe per epic).
- [ ] **Graph lifecycle integration tests** (real SQLite) as in STORY-006.6.

### EPIC-009: Multi-Interface Distribution (High) — remaining work

**Current:** Core `pyproject.toml` depends on **typer** for all installs; MCP is an optional extra; no `project.scripts`; no `py.typed` under `src/tapps_brain/`.

- [ ] Reorganize **extras** (`[cli]`, `[mcp]`, `[all]`) and move typer/MCP out of default deps per STORY-009.1; graceful errors when CLI/MCP invoked without extras.
- [ ] Declare **entry points** (`tapps-brain`, `tapps-brain-mcp`) and verify **`uvx`/PyPI** story (STORY-009.3+).
- [ ] **Library surface:** curated `__all__`, **`py.typed`**, optional MCP Registry **`server.json`**.
- [ ] **Docs:** client config examples for major MCP hosts (overlap with EPIC-008 doc task — do once, link from both epics).

## Medium Priority

### EPIC-007: Observability (Medium) — remaining work

**Already in tree:** `src/tapps_brain/metrics.py` (`MetricsCollector`, `MetricsSnapshot`, `StoreHealthReport`); `MemoryStore.get_metrics()`, `MemoryStore.health()`; CLI `tapps-brain maintenance health` and `tapps-brain store metrics`; `src/tapps_brain/audit.py` with **`AuditReader`** (JSONL query). **Counters/histograms are not wired** from store operation paths (collector exists but is not populated by `save`/`search`/etc. in `store.py`). No **`store.audit()`** convenience. No OpenTelemetry exporter module.

- [ ] **Instrument** core store paths (save, get, search, recall, supersede, consolidate, GC) — counters and latency histograms (STORY-007.2).
- [ ] Expose **`store.audit(...)`** (or equivalent) delegating to `AuditReader` (STORY-007.3).
- [ ] Optional **OpenTelemetry** exporter behind optional dep + feature flag (STORY-007.5).
- [ ] **Observability integration tests** if not already satisfied by unit coverage (STORY-007.6).

## Completed

- [x] Project enabled for Ralph
- [x] EPIC-001: Test suite quality — A+ (done)
- [x] EPIC-002: Integration wiring (done)
- [x] EPIC-003: Auto-recall orchestrator (done)
- [x] EPIC-004: Bi-temporal fact versioning (done)
- [x] EPIC-005: CLI tool (done)

## Notes

- **EPIC-008** — Core MCP tools/resources are done; Ralph should focus on packaging entrypoint, prompts, federation/maintenance tools, integration tests, and docs — not re-implementing CRUD/recall handlers.
- **EPIC-006** — Do not assume “greenfield”; persistence and retrieval expansion exist — wire store lifecycle and graph APIs next.
- Always cross-check **`docs/planning/epics/`** and **`docs/planning/STATUS.md`** before starting a task.
- Maintain **95%** test coverage; run full lint / type / test suite before committing.
