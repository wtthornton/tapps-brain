# Story 65.1 -- GET /snapshot live endpoint on HttpAdapter

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** a live HTTP endpoint that returns the current system snapshot as JSON, **so that** the dashboard and any monitoring tool can fetch real system state without running the CLI export command

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the HttpAdapter becomes the live data source for the visual dashboard. Currently build_visual_snapshot() is only callable from the CLI. By wiring it to GET /snapshot we make real system state available over HTTP to any consumer — the dashboard, Prometheus alerting rules, or OpenClaw health probes — without operator intervention.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Add GET /snapshot to HttpAdapter. The endpoint accepts an optional store reference injected at adapter construction time (HttpAdapter(..., store=store)). It calls build_visual_snapshot(store, privacy='standard') with a 15-second TTL cache to prevent hammering SQLite on rapid polls. Returns Content-Type: application/json with the full VisualSnapshot dict. Auth follows the same bearer-token gate as /info. Add the route to the OpenAPI 3.1 spec in the same module. Add CORS header Access-Control-Allow-Origin: * so the nginx-served dashboard at port 8088 can call the HttpAdapter at port 8080 without a proxy.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/http_adapter.py`
- `src/tapps_brain/visual_snapshot.py`
- `tests/unit/test_http_adapter.py`
- `docker/docker-compose.hive.yaml`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Add optional store: MemoryStore | None parameter to HttpAdapter.__init__ and store as self._store (`src/tapps_brain/http_adapter.py`)
- [ ] Add GET /snapshot route handler _handle_snapshot() that checks self._store, calls build_visual_snapshot() with TTL cache, serialises via model_dump(), returns 200 JSON or 503 if no store configured (`src/tapps_brain/http_adapter.py`)
- [ ] Add 15s TTL cache (_snapshot_cache: VisualSnapshot | None, _snapshot_cache_at: float) refreshed in _handle_snapshot() (`src/tapps_brain/http_adapter.py`)
- [ ] Add Access-Control-Allow-Origin: * response header to _handle_snapshot() and OPTIONS preflight handler (`src/tapps_brain/http_adapter.py`)
- [ ] Add /snapshot entry to OpenAPI 3.1 spec dict in module — description, response schema ref, auth note (`src/tapps_brain/http_adapter.py`)
- [ ] Add /snapshot to protected route set (requires bearer token when auth_token is set, same as /info) (`src/tapps_brain/http_adapter.py`)
- [ ] Add unit tests: /snapshot returns 200 with valid JSON when store injected; 503 when store is None; TTL cache prevents double call within 15s; CORS header present (`tests/unit/test_http_adapter.py`)
- [ ] Update docker/docker-compose.hive.yaml to pass store DSN env var so HttpAdapter can be wired in the migrate/MCP container startup (`docker/docker-compose.hive.yaml`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] GET /snapshot returns HTTP 200 with Content-Type application/json and a valid VisualSnapshot-shaped body when store is injected
- [ ] GET /snapshot returns HTTP 503 with error JSON when adapter was constructed without a store
- [ ] Response includes Access-Control-Allow-Origin: * header
- [ ] Two requests within 15s return identical cached body (verified by checking generated_at field)
- [ ] Third request after 15s returns a fresh body with updated generated_at
- [ ] /snapshot appears in GET /openapi.json response schema
- [ ] Bearer token gate: returns 401/403 when token configured and request has no/wrong token
- [ ] p99 response time under 200ms with cache warm (pytest-benchmark assertion)

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] GET /snapshot live endpoint on HttpAdapter code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. Happy path: GET /snapshot with store injected → 200 + valid JSON body
2. No store: GET /snapshot without store → 503 + {error: no store configured}
3. Cache: two requests within 15s → identical generated_at
4. Auth: token set + no header → 401; wrong token → 403; correct token → 200
5. CORS: OPTIONS /snapshot → Access-Control-Allow-Origin: * present
6. OpenAPI: GET /openapi.json → /snapshot entry present in paths

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- build_visual_snapshot() calls store.list_all() which is O(n) — TTL cache is mandatory before this story is mergeable
- HttpAdapter runs in a daemon thread; store access must be read-only (no writes from this path)
- build_visual_snapshot() is defined in visual_snapshot.py — import at call site to avoid circular imports at module load
- VisualSnapshot.model_dump() produces the correct JSON shape — do not hand-roll serialisation

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- List stories or external dependencies that must complete first...

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [x] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
