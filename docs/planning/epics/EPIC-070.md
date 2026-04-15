# Epic 70: HTTP/MCP transport parity — Streamable HTTP + service-layer refactor

<!-- docsmcp:start:metadata -->
**Status:** Complete
**Priority:** P0 - Critical
**Estimated LOE:** ~4-6 weeks (1-2 developers)
**Dependencies:** EPIC-069 (multi-tenant project_id + Postgres profile registry)

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that AgentForge and any other remote agent framework can consume tapps-brain as a first-class networked service with the full capability surface of the MCP stdio server — not a reduced REST subset. Streamable HTTP (MCP spec 2025-03-26) is the 2026 standard for remote MCP and gives us transport parity by construction rather than by discipline: one FastMCP tool registry serves both stdio (local subprocess agents) and HTTP (remote agents). The prerequisite is extracting tool bodies into a pure service layer so neither transport owns business logic.</purpose_and_intent>
<parameter name="goal">Refactor tapps-brain into a three-layer architecture — pure service layer, single FastMCP registry with dual transports (stdio + Streamable HTTP), and FastAPI for non-MCP surface (probes, admin, tenant lifecycle) — so that every MCP tool is reachable over HTTP via /mcp with identical behavior, auth, and observability. Replace the stdlib BaseHTTPRequestHandler http_adapter with FastAPI + mounted FastMCP Streamable HTTP app, with stateless_http=True and header-based tenant resolution (X-Project-Id, X-Agent-Id, Mcp-Session-Id).

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Describe how **HTTP/MCP transport parity — Streamable HTTP + service-layer refactor** will change the system. What measurable outcome proves this epic is complete?

**Tech Stack:** tapps-brain, Python >=3.12

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

Today tapps-brain's HTTP adapter exposes only 8 probe/admin routes while MCP exposes 70+ tools over stdio only. Every remote agent must docker-run a subprocess per session — unworkable for serverless, edge, or multi-tenant agent frameworks. AgentForge integration (the driver) needs a single network endpoint it can point any agent at. Building a parallel REST API duplicating every MCP tool would create two registries to keep in sync, double the test surface, and diverge under drift. Streamable HTTP collapses that into one registry, two transports. SSE is deprecated (March 2025), so continuing to invest in custom HTTP tooling bets against the spec.</motivation>
<parameter name="acceptance_criteria">Every MCP tool registered in mcp_server.py is callable over Streamable HTTP at POST /mcp with identical request/response contract, All tool bodies live in src/tapps_brain/services/*.py as pure functions taking (store, project_id, agent_id, **args) — mcp_server.py becomes a thin adapter, FastAPI replaces BaseHTTPRequestHandler in http_adapter.py; existing probe/admin routes preserved byte-for-byte on the wire, Streamable HTTP mounted at /mcp with stateless_http=True and json_response=True for horizontal scaling, Bearer auth validated per-request on /mcp; Origin header validated to prevent DNS rebinding; rate_limiter.py applied, X-Project-Id and X-Agent-Id headers resolve tenant context; legacy stdio argv-based resolution still works for local subprocess agents, Parity test diffs the FastMCP tool registry against an HTTP route manifest and fails CI if they drift, OpenAPI spec at /openapi.json documents both /mcp JSON-RPC envelope and the REST admin/probe surface, Docker image docker-tapps-brain-http:latest serves both /mcp and /admin/* from one Uvicorn worker; stdio image unchanged, No references remain to the deprecated HTTP+SSE two-endpoint transport (protocol 2024-11-05), Integration test: AgentForge-style remote client completes save → search → recall round-trip over /mcp with no stdio involvement

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] Define verifiable criteria for **HTTP/MCP transport parity — Streamable HTTP + service-layer refactor**...
- [x] All stories completed and passing tests
- [x] Documentation updated

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

### 70.1 -- Extract pure service layer from MCP tool bodies

**Points:** 13

Move all business logic out of @mcp.tool() handlers in src/tapps_brain/mcp_server.py into src/tapps_brain/services/ modules (memory_service, hive_service, flywheel_service, feedback_service, agents_service, maintenance_service, diagnostics_service). Each service function is transport-agnostic: takes (store, project_id, agent_id, **typed_args) and returns a JSON-serializable dict. mcp_server.py shrinks to ~200 lines of thin @mcp.tool() wrappers that resolve the store and delegate. No behavior change; tests must still pass.

(6 acceptance criteria)

**Tasks:**
- [x] Implement extract pure service layer from mcp tool bodies
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Extract pure service layer from MCP tool bodies is implemented, tests pass, and documentation is updated.

---

### 70.2 -- Adopt FastMCP + Streamable HTTP transport

**Points:** 5

Pin mcp>=1.25 / FastMCP ≥ 3.2. Configure a single FastMCP instance with stateless_http=True, json_response=True. Expose streamable_http_app() for ASGI mounting. Verify stdio transport unchanged via tapps-brain-mcp entrypoint. Confirm no SSE/2024-11-05 transport references remain.

(4 acceptance criteria)

**Tasks:**
- [x] Implement adopt fastmcp + streamable http transport
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Adopt FastMCP + Streamable HTTP transport is implemented, tests pass, and documentation is updated.

---

### 70.3 -- Replace stdlib http_adapter with FastAPI app

**Points:** 8

Rewrite src/tapps_brain/http_adapter.py using FastAPI + Uvicorn. Preserve wire contracts for /health, /ready, /metrics, /info, /snapshot, /openapi.json, GET+POST+DELETE on /admin/projects/*. Keep dual-token auth (TAPPS_BRAIN_AUTH_TOKEN, TAPPS_BRAIN_ADMIN_TOKEN). Reuse otel_tracer.py and rate_limiter.py via middleware. Retire BaseHTTPRequestHandler.

(6 acceptance criteria)

**Tasks:**
- [x] Implement replace stdlib http_adapter with fastapi app
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Replace stdlib http_adapter with FastAPI app is implemented, tests pass, and documentation is updated.

---

### 70.4 -- Mount FastMCP Streamable HTTP at /mcp with tenant middleware

**Points:** 5

app.mount('/mcp', mcp.streamable_http_app()) inside the FastAPI lifespan running mcp.session_manager. Middleware reads X-Project-Id, X-Agent-Id, Authorization: Bearer, and Origin; validates Origin against an allowlist (DNS-rebinding guard); rejects unauthorized requests before reaching FastMCP. Mcp-Session-Id header handled for stateful clients; stateless is default.

(5 acceptance criteria)

**Tasks:**
- [x] Implement mount fastmcp streamable http at /mcp with tenant middleware
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Mount FastMCP Streamable HTTP at /mcp with tenant middleware is implemented, tests pass, and documentation is updated.

---

### 70.5 -- Parity test: MCP tool registry vs HTTP route manifest

**Points:** 3

CI test that enumerates FastMCP tools (mcp.list_tools()) and asserts each is reachable over POST /mcp with the documented JSON-RPC envelope. Asserts request/response schemas match for a curated high-signal sample (memory_save, memory_recall, hive_search, flywheel_evaluate, agent_register). Fails build on drift.

(4 acceptance criteria)

**Tasks:**
- [x] Implement parity test: mcp tool registry vs http route manifest
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Parity test: MCP tool registry vs HTTP route manifest is implemented, tests pass, and documentation is updated.

---

### 70.6 -- Update Docker image + compose for unified HTTP/MCP surface

**Points:** 3

Update docker/Dockerfile.http to run uvicorn src.tapps_brain.http_adapter:app. docker-compose.hive.yaml exposes :8080 (data) and :8088 (admin) from one container serving both /mcp and /admin/*. Dockerfile.migrate unchanged. .mcp.json stays pointed at stdio for local-subprocess agents.

(4 acceptance criteria)

**Tasks:**
- [x] Implement update docker image + compose for unified http/mcp surface
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Update Docker image + compose for unified HTTP/MCP surface is implemented, tests pass, and documentation is updated.

---

### 70.7 -- AgentForge integration spike + migration guide

**Points:** 5

Write a remote-client example in examples/agentforge-client.py using the MCP Python SDK over Streamable HTTP against a running tapps-brain container. Document header-based tenant auth, session lifecycle, and error handling. Add docs/guides/remote-mcp-integration.md covering migration from docker-run stdio to HTTP.

(5 acceptance criteria)

**Tasks:**
- [x] Implement agentforge integration spike + migration guide
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** AgentForge integration spike + migration guide is implemented, tests pass, and documentation is updated.

---

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- MCP spec version 2025-03-26 (Streamable HTTP) is the target; HTTP+SSE 2024-11-05 is deprecated and must not be reintroduced. FastMCP mounts via Starlette-compatible ASGI — FastAPI works through Mount. session_manager.run() must be driven from the app lifespan context. stateless_http=True is required for horizontal scaling behind load balancers; any tool that needs cross-request state must persist through Postgres (consistent with ADR-010). Origin header validation is non-optional: without it
- a browser can be tricked into acting as a confused deputy via DNS rebinding. Bearer tokens belong in Authorization header
- never in query strings (per spec).

**Project Structure:** 6 packages, 61 modules, 413 public APIs

**Key Dependencies:** pydantic>=2.12.5,<3, structlog>=25.5.0,<26, pyyaml>=6.0.3,<7, numpy>=2.4.2,<3, sentence-transformers>=5.2.3,<6, psycopg[binary,pool]>=3.2,<4, opentelemetry-api>=1.20,<2

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- Building a separate REST API that mirrors MCP tool names — Streamable HTTP IS the HTTP parity
- Deleting sqlite/filesystem backend code (tracked separately — out of scope here)
- Changing the MCP tool surface
- adding new tools
- or renaming existing ones
- MCP prompt templates over HTTP — these are a client-UX primitive and legitimately stdio/MCP-only
- Re-introducing the deprecated HTTP+SSE two-endpoint transport for backwards compatibility

<!-- docsmcp:end:non-goals -->

<!-- docsmcp:start:files-affected -->
## Files Affected

| File | Lines | Recent Commits | Public Symbols |
|------|-------|----------------|----------------|
| `src/tapps_brain/mcp_server.py` | 2807 | 5 recent: ebf815f feat(EPIC-069): complete multi-tenant p... | 2 functions |
| `src/tapps_brain/http_adapter.py` | 1177 | 5 recent: ebf815f feat(EPIC-069): complete multi-tenant p... | 1 classes |
| `src/tapps_brain/rate_limiter.py` | 194 | 4 recent: 09f5cbb feat(relay): sub-agent memory relay imp... | 4 classes |
| `src/tapps_brain/otel_tracer.py` | 646 | 5 recent: 315eb42 docs: full sweep — fix 33 broken refs, ... | 14 functions |
| `src/tapps_brain/project_resolver.py` | 112 | 1 recent: bea6ef5 feat(EPIC-069): multi-tenant project re... | 1 classes, 2 functions |
| `src/tapps_brain/project_registry.py` | 247 | 2 recent: ebf815f feat(EPIC-069): complete multi-tenant p... | 3 classes |
| `docker/Dockerfile.http` | 5 | 1 recent: 2d6373a feat: EPIC-066/067 — Postgres productio... | - |
| `docker/docker-compose.hive.yaml` | 83 | 5 recent: cd8ff51 feat(EPIC-068): multi-page brain-visual... | - |
| `pyproject.toml` | 168 | 5 recent: 8b95daa release: v3.5.1 — patch 3 bugs caught b... | - |
| `.mcp.json` | 32 | 1 recent: 4e64379 fix(ralph): enable tapps-mcp permission... | - |

<!-- docsmcp:end:files-affected -->

<!-- docsmcp:start:related-epics -->
## Related Epics

- **EPIC-001.md** -- references `pyproject.toml`
- **EPIC-002.md** -- references `pyproject.toml`
- **EPIC-005.md** -- references `pyproject.toml`
- **EPIC-007.md** -- references `pyproject.toml`
- **EPIC-008.md** -- references `pyproject.toml`, `src/tapps_brain/mcp_server.py`
- **EPIC-009.md** -- references `pyproject.toml`
- **EPIC-010.md** -- references `pyproject.toml`, `src/tapps_brain/mcp_server.py`
- **EPIC-011.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-012.md** -- references `pyproject.toml`, `src/tapps_brain/mcp_server.py`
- **EPIC-014.md** -- references `pyproject.toml`
- **EPIC-026.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-027.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-028.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-029.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-030.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-031.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-032.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-034.md** -- references `pyproject.toml`
- **EPIC-036.md** -- references `pyproject.toml`
- **EPIC-042.md** -- references `pyproject.toml`
- **EPIC-043.md** -- references `pyproject.toml`, `src/tapps_brain/mcp_server.py`
- **EPIC-044.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-045.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-046.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-047.md** -- references `src/tapps_brain/mcp_server.py`, `src/tapps_brain/rate_limiter.py`
- **EPIC-048.md** -- references `pyproject.toml`, `src/tapps_brain/mcp_server.py`
- **EPIC-049.md** -- references `pyproject.toml`, `src/tapps_brain/mcp_server.py`
- **EPIC-052.md** -- references `pyproject.toml`, `src/tapps_brain/mcp_server.py`
- **EPIC-053.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-054.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-057.md** -- references `src/tapps_brain/mcp_server.py`
- **EPIC-058.md** -- references `docker/docker-compose.hive.yaml`
- **EPIC-065.md** -- references `docker/docker-compose.hive.yaml`, `src/tapps_brain/http_adapter.py`, `src/tapps_brain/otel_tracer.py`
- **EPIC-066.md** -- references `pyproject.toml`
- **EPIC-067.md** -- references `docker/Dockerfile.http`, `docker/docker-compose.hive.yaml`, `src/tapps_brain/http_adapter.py`
- **EPIC-069-next-session-prompt.md** -- references `.mcp.json`, `src/tapps_brain/mcp_server.py`, `src/tapps_brain/project_registry.py`, `src/tapps_brain/project_resolver.py`
- **EPIC-069.md** -- references `.mcp.json`

<!-- docsmcp:end:related-epics -->

<!-- docsmcp:start:success-metrics -->
## Success Metrics

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| Remote MCP round-trip (save+search+recall) p95 under 150 ms on tapps-hive-db | - | - | - |
| Zero tool-registry drift between stdio and HTTP transports (enforced by parity test) | - | - | - |
| 100% of the 70+ existing MCP tools reachable over HTTP | - | - | - |
| AgentForge integration spike completes an end-to-end memory workflow against /mcp without spawning a subprocess | - | - | - |
| Service-layer unit test coverage ≥ 85% (independent of transport tests) | - | - | - |

<!-- docsmcp:end:success-metrics -->

<!-- docsmcp:start:stakeholders -->
## Stakeholders

| Role | Person | Responsibility |
|------|--------|----------------|
| tapps-brain maintainers | - | - |
| AgentForge team (primary consumer) | - | - |
| downstream agent framework integrators | - | - |

<!-- docsmcp:end:stakeholders -->

<!-- docsmcp:start:references -->
## References

- MCP spec 2025-03-26 (Streamable HTTP)
- modelcontextprotocol/python-sdk README (FastMCP + Streamable HTTP mounting)
- ADR-010 multi-tenant project_id
- EPIC-069 Postgres profile registry
- Kirk Ryan 2026-03-11 stdio vs Streamable HTTP decision framework
- Portkey "Converting STDIO to Streamable HTTP" guide
- MCPcat StreamableHTTP production guide

<!-- docsmcp:end:references -->

<!-- docsmcp:start:implementation-order -->
## Implementation Order

1. Story 70.1: Extract pure service layer from MCP tool bodies
2. Story 70.2: Adopt FastMCP + Streamable HTTP transport
3. Story 70.3: Replace stdlib http_adapter with FastAPI app
4. Story 70.4: Mount FastMCP Streamable HTTP at /mcp with tenant middleware
5. Story 70.5: Parity test: MCP tool registry vs HTTP route manifest
6. Story 70.6: Update Docker image + compose for unified HTTP/MCP surface
7. Story 70.7: AgentForge integration spike + migration guide

<!-- docsmcp:end:implementation-order -->

<!-- docsmcp:start:risk-assessment -->
## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Service-layer extraction touches all 70+ tools; risk of subtle behavior drift — mitigate with golden-response tests captured from current stdio before refactor | Medium | Low | Warning: Mitigation required - no automated recommendation available |
| FastMCP version skew between stdio and HTTP transports could cause serialization differences — pin exact version and run parity test on every commit | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| Streamable HTTP mounting inside FastAPI requires correct lifespan wiring; misconfiguration silently breaks session management — add a smoke test that initializes and calls one tool on CI | Medium | High | Warning: Mitigation required - no automated recommendation available |
| Tenant header spoofing if Origin/Bearer validation has gaps — security review required before merge | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| Stateless mode breaks any tool that today relies on process-local caches — audit backends.py / store.py for in-memory state and push to Postgres or Redis | Medium | Medium | Warning: Mitigation required - no automated recommendation available |

<!-- docsmcp:end:risk-assessment -->

<!-- docsmcp:start:performance-targets -->
## Performance Targets

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| Test coverage | baseline | >= 80% | pytest --cov |
| Quality gate score | N/A | >= 70/100 | tapps_quality_gate |
| Story completion rate | 0% | 100% | Sprint tracking |

<!-- docsmcp:end:performance-targets -->
