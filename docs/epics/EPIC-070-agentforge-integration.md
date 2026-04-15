# Epic 70: AgentForge Integration — Remote-First Brain as a Shared Service

<!-- docsmcp:start:metadata -->
**Status:** Proposed
**Priority:** P0 - Critical
**Estimated LOE:** ~4-6 weeks (1 developer)
**Dependencies:** EPIC-069 (multi-tenant project registration)

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that tapps-brain can be deployed once as a dockerized shared service and consumed by many AgentForge workers, Claude Code sessions, OpenClaw agents, and third-party AGENT.md-driven agents over the network — without every consumer reimplementing its own transport, resilience, tenancy, and client adapter. Today the only viable integration path is the embedded Python library; the MCP server is stdio-only (one subprocess per client) and the HTTP adapter has no memory CRUD. This epic closes those gaps so tapps-brain becomes a first-class multi-agent framework instead of a library that happens to ship an MCP stub.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Make tapps-brain deployable as a single shared service (docker container) that any number of agents and application workers can consume over the network, with a stable typed client, full memory CRUD, multi-agent identity at call time, per-tenant auth, bulk + idempotent writes, and OTel-correlated observability.

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

AgentForge currently embeds tapps-brain as a sync Python library. BrainBridge (~925 LOC) wraps it with a circuit breaker, bounded write queue, and per-agent pool because the library is sync and per-process. To move to "one brain, many agents" — the pitch in docs/TAPPS_BRAIN.md — the upstream surface must support a network-shared deployment. Without these changes, AgentForge cannot adopt MCP-first without either (a) spawning N docker-run subprocesses per worker or (b) writing and maintaining a downstream HTTP wrapper that duplicates MCP. Both are worse than fixing it upstream once.

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] A single dockerized tapps-brain instance serves N AgentForge workers + M external agents concurrently over HTTP/MCP
- [ ] Memory CRUD is available over HTTP with the same tenant resolver as /admin/projects
- [ ] FastMCP server exposes a streamable-HTTP transport in addition to stdio
- [ ] Official TappsBrainClient (sync + async) ships in the wheel with method parity against AgentBrain
- [ ] Per-call agent_id / scope / group lets one connection multiplex agents (removes need for downstream BrainPool)
- [ ] Per-tenant auth tokens map to project_id via project_profiles so a leaked token cannot cross tenants
- [ ] Bulk operations and idempotency keys available on all write endpoints
- [ ] Error taxonomy distinguishes retry-safe (503/429) from retry-never (403/400) with stable codes
- [ ] OTel trace context propagates across HTTP and MCP paths
- [ ] Prometheus metrics carry project_id + agent_id labels
- [ ] AgentForge BrainBridge port reference implementation lands in examples/ proving the client surface is sufficient
- [ ] Operator tools (gc_run
- [ ] consolidation_merge) are separable from standard memory tools so AGENT.md grants are safe by default

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

### 70.1 -- Streamable-HTTP MCP transport

**Points:** 5

Add streamable-HTTP transport to the FastMCP server alongside stdio so one container can serve many clients over the network. stdio path must remain unchanged.

**Tasks:**
- [ ] Implement streamable-http mcp transport
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Streamable-HTTP MCP transport is implemented, tests pass, and documentation is updated.

---

### 70.2 -- Transport-agnostic service layer

**Points:** 5

Factor MemoryStore tool logic into a shared service module so both MCP tools and future HTTP routes call one code path. No behavior change; pure refactor.

**Tasks:**
- [ ] Implement transport-agnostic service layer
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Transport-agnostic service layer is implemented, tests pass, and documentation is updated.

---

### 70.3 -- Memory CRUD on HttpAdapter

**Points:** 8

Add POST /v1/remember, GET /v1/recall, POST /v1/reinforce, DELETE /v1/entries/{id}, POST /v1/hive/search, POST /v1/relay/{export,import}. Reuse EPIC-069 tenant resolver and existing bearer auth. Structured errors match the new taxonomy.

**Tasks:**
- [ ] Implement memory crud on httpadapter
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Memory CRUD on HttpAdapter is implemented, tests pass, and documentation is updated.

---

### 70.4 -- Error taxonomy + retry-ability semantics

**Points:** 3

Define stable error codes (brain_degraded=503, brain_rate_limited=429, project_not_registered=403, invalid_request=400, idempotency_conflict=409) across HTTP and MCP. Document retry-safety for each. Update existing EPIC-069 403/-32002 errors to the taxonomy.

**Tasks:**
- [ ] Implement error taxonomy + retry-ability semantics
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Error taxonomy + retry-ability semantics is implemented, tests pass, and documentation is updated.

---

### 70.5 -- Idempotency keys for writes

**Points:** 3

POST /v1/remember, /v1/reinforce, /v1/ingest accept X-Idempotency-Key; duplicate keys within 24h return the original response. Small idempotency_keys Postgres table with TTL cleanup.

**Tasks:**
- [ ] Implement idempotency keys for writes
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Idempotency keys for writes is implemented, tests pass, and documentation is updated.

---

### 70.6 -- Bulk operations

**Points:** 5

Add save_many / recall_many / reinforce_many over HTTP and MCP. Single-project transaction per request. Caps on batch size (default 100). Used by AgentForge learning loop.

**Tasks:**
- [ ] Implement bulk operations
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Bulk operations is implemented, tests pass, and documentation is updated.

---

### 70.7 -- Per-call identity (agent_id / scope / group)

**Points:** 5

Let every operation accept agent_id, scope, and memory_group on the call instead of at connection time. Required for one-connection-many-agents. _meta.agent_id / X-Tapps-Agent precedence mirrors project resolver.

**Tasks:**
- [ ] Implement per-call identity (agent_id / scope / group)
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Per-call identity (agent_id / scope / group) is implemented, tests pass, and documentation is updated.

---

### 70.8 -- Per-tenant auth tokens

**Points:** 5

Extend project_profiles with hashed_token; auth middleware resolves Authorization bearer to project_id via hash match. Feature-flagged via TAPPS_BRAIN_PER_TENANT_AUTH=1. CLI: tapps-brain project rotate-token.

**Tasks:**
- [ ] Implement per-tenant auth tokens
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Per-tenant auth tokens is implemented, tests pass, and documentation is updated.

---

### 70.9 -- Operator-tool separation

**Points:** 3

Move gc_run, consolidation_merge, memory_import/export, migration tools into a separate FastMCP instance under tapps_brain.mcp_server.operator. Default MCP server no longer exposes them even with TAPPS_BRAIN_OPERATOR_TOOLS=1.

**Tasks:**
- [ ] Implement operator-tool separation
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Operator-tool separation is implemented, tests pass, and documentation is updated.

---

### 70.10 -- Native async parity

**Points:** 5

Extend AsyncMemoryStore to cover every sync AgentBrain method (reinforce, hive_search, relay export/import, consolidate, gc_run). Expose via TappsBrainClient async impl. Eliminates asyncio.to_thread wrapping downstream.

**Tasks:**
- [ ] Implement native async parity
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Native async parity is implemented, tests pass, and documentation is updated.

---

### 70.11 -- Official TappsBrainClient (sync + async)

**Points:** 8

Ship tapps_brain.client.{TappsBrainClient, AsyncTappsBrainClient} mirroring AgentBrain method signatures. Two backends selected by URL scheme: http(s):// uses HTTP adapter, mcp+stdio:// spawns subprocess, mcp+http:// uses streamable-HTTP. One Protocol, three transports. Pooled httpx.AsyncClient under the hood.

**Tasks:**
- [ ] Implement official tappsbrainclient (sync + async)
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Official TappsBrainClient (sync + async) is implemented, tests pass, and documentation is updated.

---

### 70.12 -- OTel + Prometheus label enrichment

**Points:** 3

HTTP middleware extracts traceparent; both paths emit spans with project_id + agent_id + tool + status attributes. Prometheus counters and histograms gain project_id and agent_id labels (bounded cardinality — cap agent_id at 100 distinct values per scrape, overflow to 'other').

**Tasks:**
- [ ] Implement otel + prometheus label enrichment
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** OTel + Prometheus label enrichment is implemented, tests pass, and documentation is updated.

---

### 70.13 -- AgentForge BrainBridge port — reference implementation

**Points:** 8

Port AgentForge's BrainBridge to use TappsBrainClient in examples/agentforge_bridge/. Proves the client surface is sufficient. Resilience layer (circuit breaker, bounded write queue) stays but wraps client calls. Target: < 250 LOC vs current ~925. Published as documentation, not as a runtime dep.

**Tasks:**
- [ ] Implement agentforge brainbridge port — reference implementation
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** AgentForge BrainBridge port — reference implementation is implemented, tests pass, and documentation is updated.

---

### 70.14 -- Compatibility test suite

**Points:** 3

CI job that pins to AgentForge's current embedded-library usage patterns and asserts zero regressions. Runs the embedded AgentBrain path against a live Postgres in the test matrix.

**Tasks:**
- [ ] Implement compatibility test suite
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Compatibility test suite is implemented, tests pass, and documentation is updated.

---

### 70.15 -- Docker + docs: one binary, both transports

**Points:** 3

Unify tapps-brain serve so a single container can expose HTTP + MCP-streamable-HTTP concurrently on distinct ports. Update docker/docker-compose.hive.yaml and docs/guides to show the shared-service deployment pattern and AGENT.md wiring example.

**Tasks:**
- [ ] Implement docker + docs: one binary, both transports
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Docker + docs: one binary, both transports is implemented, tests pass, and documentation is updated.

---

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Factor MemoryStore operations into a transport-agnostic service layer so MCP tools and HTTP routes share one code path
- Reuse EPIC-069 project_resolver (_meta > X-Tapps-Project > env) on the HTTP path
- Streamable-HTTP transport: FastMCP.run(transport='streamable-http'); verify against mcp>=1.2
- Ship TappsBrainClient in tapps_brain.client with two implementations (HTTPClient + MCPClient) behind one Protocol
- Async parity: AsyncMemoryStore already exists — extend to the full operation set and expose via the client
- Idempotency: store last N idempotency keys per project_id in a small Postgres table with 24h TTL
- Operator tools: split into tapps_brain.mcp_server.operator submodule
- gate via TAPPS_BRAIN_OPERATOR_TOOLS=1 (already exists) — make it a separate FastMCP instance so grants are clean
- Auth: extend project_profiles with hashed_token column; resolver matches Authorization bearer against hash
- OTel: HTTP middleware extracts traceparent header; MCP already handles params._meta.traceparent (3.4.0)

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- Replacing the embedded AgentBrain Python API (stays supported for single-process users)
- Building a hosted SaaS control plane
- Adding a second storage backend beyond Postgres
- Designing a new memory data model — schema stays 3.5.x compatible
- Building AgentForge-specific features in tapps-brain core

<!-- docsmcp:end:non-goals -->

<!-- docsmcp:start:success-metrics -->
## Success Metrics

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| P50 memory_recall latency over HTTP < 50ms (local docker network) / < 150ms (cross-host) | - | - | - |
| AgentForge BrainBridge LOC drops from ~925 to < 250 after port | - | - | - |
| Zero breaking changes to existing embedded AgentBrain users (backward-compat tests pass) | - | - | - |
| One tapps-brain container serves ≥ 8 concurrent AgentForge workers with no subprocess fan-out | - | - | - |
| Per-tenant auth token leak confined to one project_id (verified by integration test) | - | - | - |
| AGENT.md adoption — at least 3 AgentForge agents declare the brain MCP server and pass their personas through live | - | - | - |

<!-- docsmcp:end:success-metrics -->

<!-- docsmcp:start:stakeholders -->
## Stakeholders

| Role | Person | Responsibility |
|------|--------|----------------|
| tapps-brain maintainers | - | - |
| AgentForge team (primary consumer) | - | - |
| OpenClaw agent authors | - | - |
| Claude Code / Cursor MCP users | - | - |
| Future third-party AGENT.md adopters | - | - |

<!-- docsmcp:end:stakeholders -->

<!-- docsmcp:start:references -->
## References

- docs/TAPPS_BRAIN.md (AgentForge side) — capabilities reference
- EPIC-069 — multi-tenant project registration (prerequisite)
- ADR-007 — Postgres-only persistence
- ADR-010 — tenancy precedence
- MCP spec — streamable-HTTP transport
- AgentForge backend/memory/brain.py — reference BrainBridge implementation to port

<!-- docsmcp:end:references -->

<!-- docsmcp:start:implementation-order -->
## Implementation Order

1. Story 70.1: Streamable-HTTP MCP transport
2. Story 70.2: Transport-agnostic service layer
3. Story 70.3: Memory CRUD on HttpAdapter
4. Story 70.4: Error taxonomy + retry-ability semantics
5. Story 70.5: Idempotency keys for writes
6. Story 70.6: Bulk operations
7. Story 70.7: Per-call identity (agent_id / scope / group)
8. Story 70.8: Per-tenant auth tokens
9. Story 70.9: Operator-tool separation
10. Story 70.10: Native async parity
11. Story 70.11: Official TappsBrainClient (sync + async)
12. Story 70.12: OTel + Prometheus label enrichment
13. Story 70.13: AgentForge BrainBridge port — reference implementation
14. Story 70.14: Compatibility test suite
15. Story 70.15: Docker + docs: one binary, both transports

<!-- docsmcp:end:implementation-order -->

<!-- docsmcp:start:risk-assessment -->
## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Streamable-HTTP MCP transport is newer in the MCP spec and FastMCP support may lag — mitigate by pinning mcp SDK version and testing against Claude Code early | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| HTTP memory CRUD duplicates MCP tool logic — mitigate by factoring a shared handler layer both transports call into | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| Bulk operations over HTTP need transaction boundaries — mitigate by scoping to single-project writes per request | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| Per-tenant auth requires schema migration (token → project_id) — mitigate by shipping behind TAPPS_BRAIN_PER_TENANT_AUTH=1 feature flag | High | High | Warning: Mitigation required - no automated recommendation available |
| Backward compatibility — existing AgentBrain users must not break; enforce via the 3.5.x compat suite in CI | Medium | Medium | Warning: Mitigation required - no automated recommendation available |

<!-- docsmcp:end:risk-assessment -->

<!-- docsmcp:start:files-affected -->
## Files Affected

| File | Story | Action |
|---|---|---|
| Files will be determined during story refinement | - | - |

<!-- docsmcp:end:files-affected -->

<!-- docsmcp:start:performance-targets -->
## Performance Targets

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| Test coverage | baseline | >= 80% | pytest --cov |
| Acceptance criteria pass rate | 0% | 100% | CI pipeline |
| Story completion rate | 0% | 100% | Sprint tracking |

<!-- docsmcp:end:performance-targets -->
