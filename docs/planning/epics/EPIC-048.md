---
id: EPIC-048
title: "Optional / auxiliary capabilities — research and upgrades"
status: planned
priority: low
created: 2026-03-31
tags: [sessions, relations, markdown, evaluation, doc-validation, visual]
---

# EPIC-048: Optional / auxiliary capabilities

## Context

Maps to **§7** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md).

## Success criteria

- [ ] Auxiliary features remain **opt-in** or clearly **non-core** in docs.

## Stories

**§7 table order:** **048.1** session index → **048.2** relations → **048.3** markdown → **048.4** evaluation harness → **048.5** doc validation → **048.6** visual snapshot.

### STORY-048.1: Session memory (index + FTS + summaries)

**Status:** done (2026-04-09) | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/session_index.py`, `src/tapps_brain/session_summary.py`, `src/tapps_brain/gc.py` (`GCConfig.session_index_ttl_days`, `GCResult.session_chunks_deleted`), `src/tapps_brain/store.py` (`gc()` prunes session index), `src/tapps_brain/cli.py` (`maintenance gc-config --session-index-ttl-days`), `tests/unit/test_session_index.py`, `tests/unit/test_session_summary.py`, `tests/integration/test_session_index_integration.py`  
**Verification:** `pytest tests/unit/test_session_index.py tests/unit/test_session_summary.py tests/integration/test_session_index_integration.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **Hierarchical** summaries (rolling + daily) reduce storage vs raw logs.
- **Embedding** session summaries for **semantic session search** — optional vector path.

#### Implementation themes

- [x] **Retention** policy for session index rows aligned with GC — `GCConfig.session_index_ttl_days` (default 90); `store.gc()` calls `cleanup_sessions(ttl_days=...)` on live runs; `GCResult.session_chunks_deleted`; `maintenance gc-config --session-index-ttl-days`.
- [x] Token **budget** for `session end` summary generation — `session_summary_save(max_chars=)` truncates at word boundary and appends `" …"`; `truncated=True` returned when applied.

---

### STORY-048.2: Graph-like links (relations)

**Status:** done (2026-04-09) | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/relations.py` (`detect_relation_cycles`, `RelationEntry.MAX_EDGES_PER_KEY`), `src/tapps_brain/store.py` (`get_relations_batch`, cycle warning + edge cap in `save()`), `src/tapps_brain/mcp_server.py` (`memory_relations_get_batch`), `tests/unit/test_relations.py`, `tests/integration/test_graph_integration.py`  
**Verification:** `pytest tests/unit/test_relations.py tests/integration/test_graph_integration.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **OpenCypher** / **GQL** full query is out of scope; **bounded traversals** (1–2 hop) may suffice.
- **PageRank** already in codebase inventory — wire to **optional** recall boost epic if desired.

#### Implementation themes

- [x] MCP: `memory_relations_get_batch(keys_json)` batch API — returns `{results: {key: [...]}, total_count: N}`; store `get_relations_batch(keys)`.
- [x] **Cycle** detection — `detect_relation_cycles()` finds self-loops and direct reversals; warnings logged at save time; `RelationEntry.MAX_EDGES_PER_KEY = 20` caps edges per key.

---

### STORY-048.3: Markdown round-trip (import / sync)

**Status:** done (2026-04-09) | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/markdown_import.py`, `src/tapps_brain/markdown_sync.py`, `tests/unit/test_markdown_import.py`, `tests/integration/test_markdown_sync_integration.py`  
**Verification:** `pytest tests/unit/test_markdown_import.py tests/integration/test_markdown_sync_integration.py -v --tb=short -m "not benchmark"`

#### Implementation themes

- [x] **Round-trip** test: memory → md → memory (lossless for subset of fields).
- [x] **Front matter** schema version — `MEMORY_MD_SCHEMA_VERSION = 1` embedded as YAML front matter at the top of every exported `MEMORY.md`; parser skips the block on import.

---

### STORY-048.4: Evaluation harness (BEIR-style)

**Status:** done (2026-04-09) | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/evaluation.py`, `tests/unit/test_evaluation.py`, `scripts/run_eval_golden.py`, `.github/workflows/ci.yml` (`eval-golden` job)  
**Verification:** `pytest tests/unit/test_evaluation.py -v --tb=short -m "not benchmark"`; benchmarks optional via `pytest tests/benchmarks/ -m benchmark` when tuning

#### Research notes (2026-forward)

- **nDCG**, **MRR**, **Recall@k** standard definitions — publish **formulas** in doc appendix.
- **Synthetic** vs **real** query sets — ship **tiny public** fixture for CI.

#### Implementation themes

- [x] CI job (`eval-golden`) running **small** lexical golden set on every PR for retrieval regressions — `scripts/run_eval_golden.py`, uploads `eval-report.json` artifact.
- [x] Export results as **JSON** artifact for dashboards (`actions/upload-artifact@v4`).

---

### STORY-048.5: Doc validation (pluggable lookup)

**Status:** done (2026-04-09) | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/doc_validation.py` (`LookupEngineLike`, `StrictValidationError`), `tests/unit/test_doc_validation.py`, `tests/integration/test_doc_validation_integration.py`, `docs/guides/doc-validation-lookup-engine.md`, `scripts/run_doc_validation.py`  
**Verification:** `pytest tests/unit/test_doc_validation.py tests/integration/test_doc_validation_integration.py -v --tb=short -m "not benchmark"`

#### Implementation themes

- [x] Example **third-party** lookup engine in `docs/guides/` — `docs/guides/doc-validation-lookup-engine.md` (stub, HTTP, Context7-style, wiring, caching).
- [x] **Strict** mode for CI on markdown repos — `StrictValidationError` raised by `validate_batch(strict=True)` / `store.validate_entries(strict=True)`; `scripts/run_doc_validation.py --strict` exits 1 on flagged entries.

---

### STORY-048.6: Visual snapshot (operator)

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** `docs/guides/visual-snapshot.md`, `tests/unit/test_visual_snapshot.py`  
**Verification:** `pytest tests/unit/test_visual_snapshot.py -v --tb=short -m "not benchmark"`; manual checklist steps in `docs/guides/visual-snapshot.md`

#### Implementation themes

- [ ] Automate **PNG** capture in headless mode if feasible (optional dep).

## Priority order

**048.4** (protects core retrieval) → **048.1**, **048.2** → **048.3** → **048.5**, **048.6**.
