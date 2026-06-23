# Story 78.13 — Complete EPIC-065.7: Retrieval live metrics panel

**Points:** 5 | **Epic:** EPIC-078 | **Priority:** P1

## What

Replace static retrieval pipeline diagram with live panel: `retrieval_effective_mode`, `retrieval_metrics` (BM25/vector/RRF counts, mean latency), pgvector row count, P50/P95/P99 when histogram present.

## Where

- `examples/brain-visual/index.html` — `#retrieval` page
- `src/tapps_brain/visual_snapshot.py` — `_collect_retrieval_metrics`

## Acceptance Criteria (from EPIC-065.7)

- [x] Retrieval mode badge matches CLI `health_check` output for same store
- [x] Query count tiles update on each poll
- [x] Latency histogram renders when snapshot includes buckets; N/A otherwise
- [x] EPIC-065.7 marked complete; EPIC-065 epic can close
