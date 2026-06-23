# Story 78.6 — Snapshot build SLO Prometheus metrics

**Points:** 3 | **Epic:** EPIC-078 | **Priority:** P1

## What

Expose `tapps_brain_snapshot_build_duration_seconds` histogram and `tapps_brain_snapshot_cache_hits_total` counter on `/metrics`.

## Where

- `src/tapps_brain/http_adapter.py` — `_snapshot` handler
- `src/tapps_brain/http/metrics_collector.py` — scrape formatting
- `tests/unit/test_http_adapter.py`

## Acceptance Criteria

- [ ] Histogram buckets: 0.1, 0.5, 1, 2, 5, 10, 30 seconds
- [ ] Cache hit increments counter without histogram observe
- [ ] Metrics visible on `/metrics` scrape (auth token gate respected)
- [ ] Document alert rule example: p95 > 5s for 5m
