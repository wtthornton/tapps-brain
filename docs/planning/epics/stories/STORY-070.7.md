# Story 70.7 -- AgentForge integration spike and remote-MCP migration guide

<!-- docsmcp:start:user-story -->

> **As a** AgentForge integrator, **I want** a working remote MCP client example plus a migration guide from docker-run stdio to Streamable HTTP, **so that** AgentForge and other consumers can adopt tapps-brain as a first-class networked service

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the driving use case is validated end-to-end against a running deployment, and so downstream consumers have a documented path off the stdio/docker-run pattern.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **AgentForge integration spike and remote-MCP migration guide** will enable **AgentForge integrator** to **a working remote MCP client example plus a migration guide from docker-run stdio to Streamable HTTP**...

See [Epic 70](../EPIC-070.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `examples/agentforge-client.py`
- `docs/guides/remote-mcp-integration.md`
- `tests/integration/test_remote_mcp_e2e.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Write remote Streamable HTTP client example (`examples/agentforge-client.py`)
- [ ] Author remote-mcp-integration guide (`docs/guides/remote-mcp-integration.md`)
- [ ] End-to-end test: boot container + run example in CI (`tests/integration/test_remote_mcp_e2e.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] examples/agentforge-client.py uses the MCP Python SDK Streamable HTTP client against a running tapps-brain container and completes memory_save → memory_search → memory_recall
- [ ] Example demonstrates X-Project-Id
- [ ] X-Agent-Id
- [ ] and Authorization header usage
- [ ] docs/guides/remote-mcp-integration.md covers auth
- [ ] tenant headers
- [ ] session lifecycle
- [ ] error handling
- [ ] and rate-limit semantics
- [ ] Migration section compares the old .mcp.json docker-run stdio pattern with the new Streamable HTTP pattern side-by-side
- [ ] Example is runnable locally via docker-compose up + python examples/agentforge-client.py

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 70](../EPIC-070.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_examplesagentforgeclientpy_uses_mcp_python_sdk_streamable_http_client` -- examples/agentforge-client.py uses the MCP Python SDK Streamable HTTP client against a running tapps-brain container and completes memory_save → memory_search → memory_recall
2. `test_ac2_example_demonstrates_xprojectid` -- Example demonstrates X-Project-Id
3. `test_ac3_xagentid` -- X-Agent-Id
4. `test_ac4_authorization_header_usage` -- and Authorization header usage
5. `test_ac5_docsguidesremotemcpintegrationmd_covers_auth` -- docs/guides/remote-mcp-integration.md covers auth
6. `test_ac6_tenant_headers` -- tenant headers
7. `test_ac7_session_lifecycle` -- session lifecycle
8. `test_ac8_error_handling` -- error handling
9. `test_ac9_ratelimit_semantics` -- and rate-limit semantics
10. `test_ac10_migration_section_compares_old_mcpjson_dockerrun_stdio_pattern_new` -- Migration section compares the old .mcp.json docker-run stdio pattern with the new Streamable HTTP pattern side-by-side
11. `test_ac11_example_runnable_locally_via_dockercompose_up_python` -- Example is runnable locally via docker-compose up + python examples/agentforge-client.py

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
