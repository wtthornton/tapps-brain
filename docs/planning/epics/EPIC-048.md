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

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/session_index.py`, `src/tapps_brain/session_summary.py`, `src/tapps_brain/cli.py` / `src/tapps_brain/mcp_server.py` (session end), `tests/unit/test_session_index.py`, `tests/unit/test_session_summary.py`, `tests/integration/test_session_index_integration.py`  
**Verification:** `pytest tests/unit/test_session_index.py tests/unit/test_session_summary.py tests/integration/test_session_index_integration.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **Hierarchical** summaries (rolling + daily) reduce storage vs raw logs.
- **Embedding** session summaries for **semantic session search** — optional vector path.

#### Implementation themes

- [ ] **Retention** policy for session index rows aligned with GC.
- [ ] Token **budget** for `session end` summary generation (deterministic templates already).

---

### STORY-048.2: Graph-like links (relations)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/relations.py`, `src/tapps_brain/store.py` (`extract_relations` on save), `tests/unit/test_relations.py`, `tests/integration/test_graph_integration.py`  
**Verification:** `pytest tests/unit/test_relations.py tests/integration/test_graph_integration.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **OpenCypher** / **GQL** full query is out of scope; **bounded traversals** (1–2 hop) may suffice.
- **PageRank** already in codebase inventory — wire to **optional** recall boost epic if desired.

#### Implementation themes

- [ ] MCP: **relations_get** batch API.
- [ ] **Cycle** detection and max edge count per key.

---

### STORY-048.3: Markdown round-trip (import / sync)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/markdown_import.py`, `src/tapps_brain/markdown_sync.py`, `tests/unit/test_markdown_import.py`, `tests/integration/test_markdown_sync_integration.py`  
**Verification:** `pytest tests/unit/test_markdown_import.py tests/integration/test_markdown_sync_integration.py -v --tb=short -m "not benchmark"`

#### Implementation themes

- [ ] **Round-trip** test: memory → md → memory (lossless for subset of fields).
- [ ] **Front matter** schema version.

---

### STORY-048.4: Evaluation harness (BEIR-style)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/evaluation.py`, `tests/unit/test_evaluation.py`  
**Verification:** `pytest tests/unit/test_evaluation.py -v --tb=short -m "not benchmark"`; benchmarks optional via `pytest tests/benchmarks/ -m benchmark` when tuning

#### Research notes (2026-forward)

- **nDCG**, **MRR**, **Recall@k** standard definitions — publish **formulas** in doc appendix.
- **Synthetic** vs **real** query sets — ship **tiny public** fixture for CI.

#### Implementation themes

- [ ] CI job (optional) running **small** golden set on PR for retrieval regressions.
- [ ] Export results as **JSON** artifact for dashboards.

---

### STORY-048.5: Doc validation (pluggable lookup)

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/doc_validation.py` (`LookupEngineLike`), `tests/unit/test_doc_validation.py`, `tests/integration/test_doc_validation_integration.py`  
**Verification:** `pytest tests/unit/test_doc_validation.py tests/integration/test_doc_validation_integration.py -v --tb=short -m "not benchmark"`

#### Implementation themes

- [ ] Example **third-party** lookup engine in `docs/guides/`.
- [ ] **Strict** mode for CI on markdown repos.

---

### STORY-048.6: Visual snapshot (operator)

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** `docs/guides/visual-snapshot.md`, `tests/unit/test_visual_snapshot.py`  
**Verification:** `pytest tests/unit/test_visual_snapshot.py -v --tb=short -m "not benchmark"`; manual checklist steps in `docs/guides/visual-snapshot.md`

#### Implementation themes

- [ ] Automate **PNG** capture in headless mode if feasible (optional dep).

## Priority order

**048.4** (protects core retrieval) → **048.1**, **048.2** → **048.3** → **048.5**, **048.6**.
