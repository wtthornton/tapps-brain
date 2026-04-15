# Story 70.2 -- Adopt FastMCP and Streamable HTTP transport

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain maintainer, **I want** a single FastMCP instance configured for stateless Streamable HTTP alongside the existing stdio transport, **so that** one tool registry serves both local subprocess agents and remote HTTP agents with no drift

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that tapps-brain aligns with the MCP 2025-03-26 spec (Streamable HTTP) which is the 2026 standard for remote MCP. The deprecated HTTP+SSE two-endpoint transport must not be reintroduced.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Adopt FastMCP and Streamable HTTP transport** will enable **tapps-brain maintainer** to **a single FastMCP instance configured for stateless Streamable HTTP alongside the existing stdio transport**...

See [Epic 70](../EPIC-070.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `pyproject.toml`
- `src/tapps_brain/mcp_server.py`
- `tests/transport/test_streamable_http_smoke.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Bump mcp pin in pyproject.toml and verify FastMCP import path (`pyproject.toml`)
- [x] Configure single FastMCP(stateless_http=True, json_response=True) in mcp_server.py (`src/tapps_brain/mcp_server.py`)
- [x] Audit and remove any sse_path/message_path/SSE transport references (`src/tapps_brain/`)
- [x] Add smoke test that imports mcp.streamable_http_app() and lists tools (`tests/transport/test_streamable_http_smoke.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] FastMCP instance created with stateless_http=True and json_response=True
- [ ] streamable_http_app() exposed for ASGI mounting
- [ ] stdio transport via tapps-brain-mcp entrypoint unchanged and passes all existing tests
- [ ] No code references the deprecated HTTP+SSE transport (protocol 2024-11-05 or sse_path/message_path patterns)
- [ ] Pinned dependency: mcp>=1.25 with FastMCP ≥ 3.2 semantics verified

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 70](../EPIC-070.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_fastmcp_instance_created_statelesshttptrue_jsonresponsetrue` -- FastMCP instance created with stateless_http=True and json_response=True
2. `test_ac2_streamablehttpapp_exposed_asgi_mounting` -- streamable_http_app() exposed for ASGI mounting
3. `test_ac3_stdio_transport_via_tappsbrainmcp_entrypoint_unchanged_passes_all` -- stdio transport via tapps-brain-mcp entrypoint unchanged and passes all existing tests
4. `test_ac4_no_code_references_deprecated_httpsse_transport_protocol_20241105_or` -- No code references the deprecated HTTP+SSE transport (protocol 2024-11-05 or sse_path/message_path patterns)
5. `test_ac5_pinned_dependency_mcp125_fastmcp_32_semantics_verified` -- Pinned dependency: mcp>=1.25 with FastMCP ≥ 3.2 semantics verified

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
