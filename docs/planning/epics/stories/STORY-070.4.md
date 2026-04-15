# Story 70.4 -- Mount FastMCP Streamable HTTP at /mcp with tenant middleware

<!-- docsmcp:start:user-story -->

> **As a** remote agent client, **I want** to reach every MCP tool over POST /mcp with header-based tenant and auth resolution, **so that** I can integrate tapps-brain without spawning a local subprocess per session

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that remote agents (AgentForge, serverless, edge) get the full MCP tool surface over a single network endpoint — the transport-parity payoff of the service-layer refactor.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Mount FastMCP Streamable HTTP at /mcp with tenant middleware** will enable **remote agent client** to **to reach every MCP tool over POST /mcp with header-based tenant and auth resolution**...

See [Epic 70](../EPIC-070.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/http_adapter.py`
- `src/tapps_brain/project_resolver.py`
- `tests/http/test_mcp_mount.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Wire lifespan to start mcp.session_manager (`src/tapps_brain/http_adapter.py`)
- [x] Implement tenant-header middleware with project/agent context vars (`src/tapps_brain/http_adapter.py`)
- [x] Implement Origin allowlist middleware with env-configurable hosts (`src/tapps_brain/http_adapter.py`)
- [x] Bearer auth dependency applied to /mcp route (`src/tapps_brain/http_adapter.py`)
- [x] Integration test: remote Python MCP client completes save+search over /mcp (`tests/http/test_mcp_mount.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] FastAPI app mounts mcp.streamable_http_app() at /mcp within a lifespan that drives mcp.session_manager.run()
- [ ] Middleware extracts X-Project-Id and X-Agent-Id headers and injects them into request-scoped context consumed by the service layer
- [ ] Authorization: Bearer validated per-request against TAPPS_BRAIN_AUTH_TOKEN before reaching FastMCP
- [ ] Origin header validated against an allowlist to prevent DNS rebinding; requests without a matching Origin rejected with 403
- [ ] Mcp-Session-Id header accepted for stateful clients but stateless is the default path

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 70](../EPIC-070.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_fastapi_app_mounts_mcpstreamablehttpapp_at_mcp_within_lifespan_drives` -- FastAPI app mounts mcp.streamable_http_app() at /mcp within a lifespan that drives mcp.session_manager.run()
2. `test_ac2_middleware_extracts_xprojectid_xagentid_headers_injects_them_into` -- Middleware extracts X-Project-Id and X-Agent-Id headers and injects them into request-scoped context consumed by the service layer
3. `test_ac3_authorization_bearer_validated_perrequest_against_tappsbrainauthtoken` -- Authorization: Bearer validated per-request against TAPPS_BRAIN_AUTH_TOKEN before reaching FastMCP
4. `test_ac4_origin_header_validated_against_allowlist_prevent_dns_rebinding` -- Origin header validated against an allowlist to prevent DNS rebinding; requests without a matching Origin rejected with 403
5. `test_ac5_mcpsessionid_header_accepted_stateful_clients_but_stateless_default` -- Mcp-Session-Id header accepted for stateful clients but stateless is the default path

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
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
