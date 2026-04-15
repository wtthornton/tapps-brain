# Story 68.5 -- Retrieval page — mode, latency histogram, vector stats

<!-- docsmcp:start:user-story -->

> **As a** brain-visual developer, **I want** a dedicated Retrieval page showing effective mode, query stats, P50/P95/P99 latency callouts, and vector index details, **so that** I can diagnose why queries are slow or why the wrong retrieval mode is active without leaving the dashboard

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that operators and developers tuning retrieval performance have a dedicated page where BM25/hybrid/vector configuration, query counts, and latency percentiles are presented with enough vertical space to be readable — rather than compressed into a single panel that competes with Hive Hub for scroll attention.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Move retrieval and vector stats panels to data-page=retrieval. Add explicit P50/P95/P99 callout tiles above the latency histogram. Add a configuration panel showing mode toggle display (BM25 / hybrid / vector with sqlite-vec) using real retrieval_effective_mode field. Add vector index stats: embedding model name, dimension count, sqlite-vec row count. Add a query stats panel: total query count, hit/miss ratio if available. Expand the latency histogram to at least 300px height. All labels use plain-language copy per the 'no fake RAG' microcopy principle from brain-visual-implementation-plan.md.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `examples/brain-visual/index.html`
- `examples/brain-visual/brain-visual-help.js`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Move existing retrieval and vector stats section markup into data-page=retrieval section element (`examples/brain-visual/index.html`)
- [x] Add P50/P95/P99 latency callout KPI tiles above the histogram, populated from snapshot.retrieval.latency_p50_ms, latency_p95_ms, latency_p99_ms (check field names against visual_snapshot.py) (`examples/brain-visual/index.html`)
- [x] Add config panel: mode indicator using snapshot.retrieval.retrieval_effective_mode field; display BM25 / Hybrid / Vector with sqlite-vec labels; add a ? help pill (`examples/brain-visual/index.html`)
- [x] Add vector stats tile: sqlite_vec_rows, sqlite_vec_enabled, embedding model name if available from snapshot (`examples/brain-visual/index.html`)
- [x] Add query stats tile: total_query_count and cache_hit_ratio if available in snapshot retrieval slice (`examples/brain-visual/index.html`)
- [x] Increase latency histogram .bar-chart min-height to 300px (`examples/brain-visual/index.html`)
- [x] Audit all retrieval microcopy for RAG hype — replace any generic 'RAG enabled' with specific mode strings per implementation plan microcopy principles; update help articles in brain-visual-help.js (`examples/brain-visual/brain-visual-help.js`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Retrieval page accessible at #retrieval; existing retrieval content renders correctly from demo JSON
- [ ] P50/P95/P99 latency callout tiles visible above histogram when snapshot contains latency data; tiles show '--' with help tooltip when data absent
- [ ] Config panel shows correct retrieval_effective_mode value from snapshot (not hardcoded string)
- [ ] Vector stats tile shows sqlite_vec_rows and sqlite_vec_enabled values
- [ ] Latency histogram min-height is ≥ 300px
- [ ] No generic 'RAG enabled' copy remains — all retrieval labels reference actual mode field values or use specific technology names (BM25
- [ ] sqlite-vec)
- [ ] All new tiles have help pills with working help drawer entries

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Retrieval page — mode, latency histogram, vector stats code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] No regressions introduced
- [ ] ralph-reviewer run on retrieval page markup and JS changes; no Critical issues open
- [ ] All ACs verified at `http://localhost:8090` with demo JSON

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_retrieval_page_accessible_at_retrieval_existing_retrieval_content` -- Retrieval page accessible at #retrieval; existing retrieval content renders correctly from demo JSON
2. `test_ac2_p50p95p99_latency_callout_tiles_visible_above_histogram_snapshot` -- P50/P95/P99 latency callout tiles visible above histogram when snapshot contains latency data; tiles show '--' with help tooltip when data absent
3. `test_ac3_config_panel_shows_correct_retrievaleffectivemode_value_from_snapshot` -- Config panel shows correct retrieval_effective_mode value from snapshot (not hardcoded string)
4. `test_ac4_vector_stats_tile_shows_sqlitevecrows_sqlitevecenabled_values` -- Vector stats tile shows sqlite_vec_rows and sqlite_vec_enabled values
5. `test_ac5_latency_histogram_minheight_300px` -- Latency histogram min-height is ≥ 300px
6. `test_ac6_no_generic_rag_enabled_copy_remains_all_retrieval_labels_reference` -- No generic 'RAG enabled' copy remains — all retrieval labels reference actual mode field values or use specific technology names (BM25
7. `test_ac7_sqlitevec` -- sqlite-vec)
8. `test_ac8_all_new_tiles_help_pills_working_help_drawer_entries` -- All new tiles have help pills with working help drawer entries

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Check visual_snapshot.py RetrievalHealthSlice for exact field names before wiring JS render path — latency fields may be named differently or absent in v2 schema
- sqlite_vec_rows and sqlite_vec_enabled are already in VisualSnapshot from Phase A work — confirmed available
- P50/P95/P99 tiles should use the same .tile CSS class as existing KPI tiles for visual consistency
- **Dev workflow:** start the tapps-brain HTTP adapter (`tapps-brain mcp start --http` or `docker compose up tapps-brain-mcp`), then `cd examples/brain-visual && python3 -m http.server 8090`; the page polls `/snapshot` live — `retrieval_effective_mode` and `retrieval_summary` are present in the live snapshot

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-068.1 (router)

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [ ] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
