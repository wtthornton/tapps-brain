# Story 78.1 — Unblock HTTP event loop: async snapshot build

<!-- docsmcp:start:user-story -->
> **As a** tapps-brain operator, **I want** `/healthz` and `/snapshot` to respond while MCP tool calls are in flight, **so that** the visual dashboard and probes stay reachable under concurrent agent load.
<!-- docsmcp:end:user-story -->

**Points:** 5 | **Epic:** EPIC-078 | **Priority:** P0

## What

Move `build_visual_snapshot()` off the FastAPI event loop so sync store work cannot starve `/healthz`, `/ready`, and other routes.

## Where

- `src/tapps_brain/http_adapter.py:1607-1641` — `_snapshot` route handler
- `tests/unit/test_http_adapter.py` — concurrency regression test

## Why

Verified 2026-06-22: `tapps-brain-http` is **unhealthy** — in-container `curl /healthz` times out after 15s while logs show concurrent MCP `CallToolRequest` handling loading SentenceTransformer models. The `_snapshot` handler calls sync `build_visual_snapshot()` directly inside an `async def`, blocking the event loop during O(n) store scans.

## Tasks

- [ ] Wrap cold-cache snapshot build in `asyncio.to_thread(build_visual_snapshot, ...)`; keep TTL cache check on event loop under `cfg.snapshot_lock`
- [ ] Add unit test: while a slow snapshot build runs in thread pool, `/healthz` returns 200 within 1s (mock store with artificial delay)
- [ ] Document thread-safety requirement: snapshot path is read-only; lock scope covers cache read/write only
- [ ] Verify `/snapshot` still returns identical JSON shape and cache semantics (15s TTL)

## Acceptance Criteria

- [ ] `/healthz` responds HTTP 200 within **1s** while a concurrent `/snapshot` cold build is in progress (pytest)
- [ ] `/snapshot` cold build no longer blocks other HTTP routes in the same process (pytest with concurrent requests)
- [ ] Existing snapshot TTL cache tests still pass
- [ ] No regression in auth gate (401 without Bearer token when strict)

## Test Cases

1. Cold snapshot + parallel `/healthz` → health returns before snapshot completes
2. Warm snapshot (cache hit) → response within 200ms
3. Two concurrent cold builds → only one thread executes build (lock)
