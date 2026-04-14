# Story 70.3 -- Replace stdlib http_adapter with FastAPI app

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** FastAPI replace the stdlib BaseHTTPRequestHandler with byte-identical wire contracts on existing probe and admin routes, **so that** we have a production-grade ASGI surface that can host the Streamable HTTP MCP mount and support modern middleware

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 8 | **Size:** L

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that non-MCP consumers (monitoring, CI, tenant admin) keep working exactly as before while the process gains ASGI capabilities required for mounting the FastMCP Streamable HTTP app and running modern middleware (OTel, rate limiting, CORS, Origin validation).

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Replace stdlib http_adapter with FastAPI app** will enable **tapps-brain operator** to **FastAPI replace the stdlib BaseHTTPRequestHandler with byte-identical wire contracts on existing probe and admin routes**...

See [Epic 70](../EPIC-070.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/http_adapter.py`
- `src/tapps_brain/rate_limiter.py`
- `src/tapps_brain/otel_tracer.py`
- `tests/http/test_wire_parity.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Scaffold FastAPI app factory with lifespan context (`src/tapps_brain/http_adapter.py`)
- [ ] Port /health, /ready, /metrics, /info, /snapshot, /openapi.json (`src/tapps_brain/http_adapter.py`)
- [ ] Port /admin/projects CRUD + approve endpoints (`src/tapps_brain/http_adapter.py`)
- [ ] Wire auth dependencies for bearer + admin token (`src/tapps_brain/http_adapter.py`)
- [ ] Wire rate limiter and OTel middleware (`src/tapps_brain/http_adapter.py`)
- [ ] Contract tests: diff FastAPI responses against stdlib baseline (`tests/http/test_wire_parity.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] FastAPI app exposes /health
- [ ] /ready
- [ ] /metrics
- [ ] /info
- [ ] /snapshot
- [ ] /openapi.json with identical response bodies and status codes to the current implementation
- [ ] GET/POST/DELETE on /admin/projects and /admin/projects/{id}(/approve) preserved byte-for-byte
- [ ] Dual-token auth enforced (TAPPS_BRAIN_AUTH_TOKEN on data; TAPPS_BRAIN_ADMIN_TOKEN on /admin/*)
- [ ] rate_limiter.py applied as FastAPI middleware
- [ ] otel_tracer.py wired as ASGI middleware producing the same spans as today
- [ ] BaseHTTPRequestHandler fully removed from http_adapter.py

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 70](../EPIC-070.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_fastapi_app_exposes_health` -- FastAPI app exposes /health
2. `test_ac2_ready` -- /ready
3. `test_ac3_metrics` -- /metrics
4. `test_ac4_info` -- /info
5. `test_ac5_snapshot` -- /snapshot
6. `test_ac6_openapijson_identical_response_bodies_status_codes_current` -- /openapi.json with identical response bodies and status codes to the current implementation
7. `test_ac7_getpostdelete_on_adminprojects_adminprojectsidapprove_preserved` -- GET/POST/DELETE on /admin/projects and /admin/projects/{id}(/approve) preserved byte-for-byte
8. `test_ac8_dualtoken_auth_enforced_tappsbrainauthtoken_on_data` -- Dual-token auth enforced (TAPPS_BRAIN_AUTH_TOKEN on data; TAPPS_BRAIN_ADMIN_TOKEN on /admin/*)
9. `test_ac9_ratelimiterpy_applied_as_fastapi_middleware` -- rate_limiter.py applied as FastAPI middleware
10. `test_ac10_oteltracerpy_wired_as_asgi_middleware_producing_same_spans_as_today` -- otel_tracer.py wired as ASGI middleware producing the same spans as today
11. `test_ac11_basehttprequesthandler_fully_removed_from_httpadapterpy` -- BaseHTTPRequestHandler fully removed from http_adapter.py

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Document implementation hints, API contracts, data formats...

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
- [ ] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
