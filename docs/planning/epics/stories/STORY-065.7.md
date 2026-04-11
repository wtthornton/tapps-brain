# Story 65.7 -- Retrieval pipeline live metrics panel

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** the retrieval section to show actual query counts and hit rates for BM25 and vector search since the process started, **so that** I can verify that hybrid search is actually firing both legs, see if one leg is consistently returning zero candidates, and catch retrieval regressions without running manual queries

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that retrieval health is verifiable not just labelled. The current retrieval section shows a mode badge (bm25_only or hybrid) derived from a static configuration check — it says "hybrid is configured" but cannot say "hybrid actually returned vector candidates in the last 100 queries." By reading OTel span counters accumulated in-process, we can show total queries, BM25 candidate count, vector candidate count, RRF fusion invocations, and mean latency. This turns the retrieval section from a configuration label into a performance monitor.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Add a RetrievalMetrics dataclass to visual_snapshot.py collecting in-process OTel counter values: total_queries, bm25_hits, vector_hits, rrf_fusions, mean_latency_ms (float). Collect from the OTel in-process meter (otel_tracer.py) if available; fall back to zeros if OTel SDK is not installed. Add retrieval_metrics: RetrievalMetrics to VisualSnapshot. In the dashboard, replace the removed step-flow diagram area with a Retrieval Metrics panel: 5 stat tiles arranged horizontally — Queries / BM25 Hits / Vector Hits / RRF Fusions / Avg Latency. Keep the existing mode badge and retrieval_summary text above the new tiles.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/visual_snapshot.py`
- `src/tapps_brain/otel_tracer.py`
- `tests/unit/test_visual_snapshot.py`
- `examples/brain-visual/index.html`
- `examples/brain-visual/brain-visual-help.js`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Add RetrievalMetrics dataclass: total_queries int, bm25_hits int, vector_hits int, rrf_fusions int, mean_latency_ms float (`src/tapps_brain/visual_snapshot.py`)
- [ ] Add _collect_retrieval_metrics() helper that reads OTel counter values from the in-process meter — instrument names: tapps_brain.recall.total, tapps_brain.bm25.candidates, tapps_brain.vector.candidates, tapps_brain.rrf.fusions, tapps_brain.recall.latency_ms (`src/tapps_brain/visual_snapshot.py`)
- [ ] Fall back to RetrievalMetrics(0,0,0,0,0.0) gracefully if OTel SDK not installed or no observations yet (`src/tapps_brain/visual_snapshot.py`)
- [ ] Verify OTel instruments exist in otel_tracer.py — add any missing counters/histograms for bm25_hits, vector_hits, rrf_fusions (`src/tapps_brain/otel_tracer.py`)
- [ ] Add retrieval_metrics: RetrievalMetrics to VisualSnapshot model (`src/tapps_brain/visual_snapshot.py`)
- [ ] Add Retrieval Metrics stat tiles to index.html retrieval section: 5 tiles below the existing mode badge, ids: rm-queries, rm-bm25, rm-vector, rm-rrf, rm-latency (`examples/brain-visual/index.html`)
- [ ] Add renderRetrievalMetrics(metrics) JS function populating the 5 tiles; format mean_latency_ms to 1 decimal place with 'ms' suffix (`examples/brain-visual/index.html`)
- [ ] Add retrieval_metrics help entry to brain-visual-help.js explaining what each counter means and the zero-on-restart caveat (`examples/brain-visual/brain-visual-help.js`)
- [ ] Add unit tests for _collect_retrieval_metrics with OTel SDK present and absent (`tests/unit/test_visual_snapshot.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] retrieval_metrics in /snapshot payload with all 5 fields present
- [ ] total_queries increments by 1 after each store.recall() or store.search() call
- [ ] bm25_hits reflects cumulative BM25 candidate count across all queries
- [ ] vector_hits reflects cumulative vector candidate count (0 when mode is bm25_only)
- [ ] rrf_fusions increments only when both legs returned candidates
- [ ] mean_latency_ms is a running mean of recall latency in milliseconds
- [ ] All metrics are 0 when no queries have been run since process start
- [ ] _collect_retrieval_metrics returns zeros without exception when OTel SDK is not installed
- [ ] Retrieval Metrics panel renders 5 tiles with correct values from live /snapshot
- [ ] Mode badge still shows above the metrics tiles unchanged

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Retrieval pipeline live metrics panel code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. One recall: total_queries=1
2. bm25_hits>0
3. mean_latency_ms>0
4. BM25-only mode: vector_hits=0
5. rrf_fusions=0 after 10 recalls
6. Hybrid mode: vector_hits>0
7. rrf_fusions>0 after 10 recalls (requires sentence-transformers installed)
8. OTel SDK absent: all fields 0
9. no exception
10. Latency: mean_latency_ms within 2x of measured wall time for a test recall

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- OTel counters reset on container restart — this is unavoidable with in-process accumulators; document clearly in help text that metrics show counts since last restart
- mean_latency_ms requires a histogram instrument not a counter — use UpDownCounter or Histogram depending on what otel_tracer.py already has
- If OTel API is installed but SDK is not
- Counter.add() is a no-op and read-back returns 0 — this is the correct fallback behaviour
- Do not add a persistent metrics store in this story — process-lifetime counters only

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-065.1
- STORY-065.3

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
