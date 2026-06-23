# Story 78.4 — nginx visual proxy hardening + upstream error UX

**Points:** 3 | **Epic:** EPIC-078 | **Priority:** P1

## What

Align nginx proxy timeouts with snapshot SLO; return structured JSON error bodies for upstream failures; add `proxy_next_upstream` tuning.

## Where

- `docker/nginx-visual.conf:13-24`
- `docker/README.md` — timeout documentation

## Why

Current `proxy_read_timeout 10s` causes **504** before cold snapshot completes. Generic nginx HTML error pages give the UI no structured detail for triage.

## Tasks

- [ ] Raise `proxy_read_timeout` and `proxy_connect_timeout` to **30s** (match `brain_smoke_live.sh` urllib timeout)
- [ ] Add custom `error_page 502 503 504` returning JSON `{"error":"upstream_timeout","upstream":"tapps-brain-http"}` with `Content-Type: application/json`
- [ ] Add `proxy_intercept_errors on` only if JSON body preserved for dashboard fetch
- [ ] Document timeout contract in `docker/README.md` and EPIC-078

## Acceptance Criteria

- [ ] Cold snapshot up to 25s completes without nginx 504
- [ ] Upstream down → JSON error body (not HTML) at `/snapshot`
- [ ] `make hive-smoke` still passes
