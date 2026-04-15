# Story 70.7 -- Per-call identity (agent_id / scope / group)

<!-- docsmcp:start:user-story -->

> **As a** multi-agent application, **I want** to pass agent_id, scope, and memory_group on each call, **so that** one pooled connection can multiplex many agents without per-agent client instances

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M | **Status:** In Progress

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that AgentForge can delete its BrainPool entirely. Today agent_scope and memory_group are bound at AgentBrain construction, forcing one instance per agent. For a shared-service model, identity must live on the request, not the connection.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Per-call identity (agent_id / scope / group)** will enable **multi-agent application** to **to pass agent_id, scope, and memory_group on each call**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/service.py`
- `src/tapps_brain/mcp_server.py`
- `src/tapps_brain/http_adapter.py`
- `src/tapps_brain/agent_scope.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement per-call identity (agent_id / scope / group) (`src/tapps_brain/service.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Every memory operation accepts agent_id
- [ ] scope
- [ ] memory_group as optional per-call parameters
- [ ] Precedence: request param > _meta.agent_id / X-Tapps-Agent header > env default
- [ ] Service layer threads identity through to Postgres RLS session vars (SET LOCAL app.agent_id)
- [ ] Logs and OTel spans carry the per-call values not connection-level defaults
- [ ] MCP tools accept agent_id / scope / group as named params on every memory tool
- [ ] HTTP endpoints accept the same via body fields or X-Tapps-Agent / X-Tapps-Scope / X-Tapps-Group headers
- [ ] Backward compat: calls without per-call identity behave exactly as before (env-level defaults apply)

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Per-call identity (agent_id / scope / group) code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_every_memory_operation_accepts_agentid` -- Every memory operation accepts agent_id
2. `test_ac2_scope` -- scope
3. `test_ac3_memorygroup_as_optional_percall_parameters` -- memory_group as optional per-call parameters
4. `test_ac4_precedence_request_param_metaagentid_xtappsagent_header_env_default` -- Precedence: request param > _meta.agent_id / X-Tapps-Agent header > env default
5. `test_ac5_service_layer_threads_identity_through_postgres_rls_session_vars_set` -- Service layer threads identity through to Postgres RLS session vars (SET LOCAL app.agent_id)
6. `test_ac6_logs_otel_spans_carry_percall_values_not_connectionlevel_defaults` -- Logs and OTel spans carry the per-call values not connection-level defaults
7. `test_ac7_mcp_tools_accept_agentid_scope_group_as_named_params_on_every_memory` -- MCP tools accept agent_id / scope / group as named params on every memory tool
8. `test_ac8_http_endpoints_accept_same_via_body_fields_or_xtappsagent_xtappsscope` -- HTTP endpoints accept the same via body fields or X-Tapps-Agent / X-Tapps-Scope / X-Tapps-Group headers
9. `test_ac9_backward_compat_calls_without_percall_identity_behave_exactly_as_before` -- Backward compat: calls without per-call identity behave exactly as before (env-level defaults apply)

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Do not break AgentBrain constructor — default identity still sets per-instance values
- per-call overrides them
- RLS session var handling uses PostgresConnectionManager.project_context() pattern from EPIC-069
- Validate scope against MemoryScope enum on every call

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-070.2

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
