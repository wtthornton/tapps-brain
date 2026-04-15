# Story 70.5 -- Parity test — MCP tool registry versus HTTP route manifest

<!-- docsmcp:start:user-story -->

> **As a** CI pipeline, **I want** an automated check that every FastMCP tool is reachable over /mcp with the documented JSON-RPC envelope, **so that** stdio and HTTP transports cannot silently drift

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the transport-parity guarantee is enforced by CI — future contributors cannot add a tool that works over stdio but silently fails over HTTP, or vice versa.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Parity test — MCP tool registry versus HTTP route manifest** will enable **CI pipeline** to **an automated check that every FastMCP tool is reachable over /mcp with the documented JSON-RPC envelope**...

See [Epic 70](../EPIC-070.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `tests/parity/test_transport_parity.py`
- `tests/parity/golden/`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Write registry enumeration test using FastMCP list_tools API (`tests/parity/test_transport_parity.py`)
- [x] Add curated request/response golden cases for high-signal tools (`tests/parity/golden/`)
- [x] Wire parity test into CI workflow (`.github/workflows/`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Test enumerates mcp.list_tools() and asserts each tool name is invocable via POST /mcp with the MCP JSON-RPC envelope
- [ ] Curated high-signal sample (memory_save
- [ ] memory_recall
- [ ] hive_search
- [ ] flywheel_evaluate
- [ ] agent_register) round-trips with schema-matched responses
- [ ] CI fails on drift — tool added to registry but unreachable over HTTP
- [ ] or request/response schema diverges
- [ ] Test runs in under 30 seconds

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 70](../EPIC-070.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_test_enumerates_mcplisttools_asserts_each_tool_name_invocable_via_post` -- Test enumerates mcp.list_tools() and asserts each tool name is invocable via POST /mcp with the MCP JSON-RPC envelope
2. `test_ac2_curated_highsignal_sample_memorysave` -- Curated high-signal sample (memory_save
3. `test_ac3_memoryrecall` -- memory_recall
4. `test_ac4_hivesearch` -- hive_search
5. `test_ac5_flywheelevaluate` -- flywheel_evaluate
6. `test_ac6_agentregister_roundtrips_schemamatched_responses` -- agent_register) round-trips with schema-matched responses
7. `test_ac7_ci_fails_on_drift_tool_added_registry_but_unreachable_over_http` -- CI fails on drift — tool added to registry but unreachable over HTTP
8. `test_ac8_or_requestresponse_schema_diverges` -- or request/response schema diverges
9. `test_ac9_test_runs_under_30_seconds` -- Test runs in under 30 seconds

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
