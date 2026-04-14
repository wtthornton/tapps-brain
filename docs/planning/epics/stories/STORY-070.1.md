# Story 70.1 -- Extract pure service layer from MCP tool bodies

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain maintainer, **I want** every MCP tool body extracted into a transport-agnostic service function, **so that** the same business logic serves stdio, Streamable HTTP, and any future transport without duplication

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 13 | **Size:** XL

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that transport parity becomes a property of the architecture rather than a discipline. With tool bodies in a pure service layer taking (store, project_id, agent_id, **args), both MCP stdio and HTTP adapters become thin delegates — no drift is possible because there is one implementation.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Extract pure service layer from MCP tool bodies** will enable **tapps-brain maintainer** to **every MCP tool body extracted into a transport-agnostic service function**...

See [Epic 70](../EPIC-070.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/mcp_server.py`
- `src/tapps_brain/services/__init__.py`
- `src/tapps_brain/services/memory_service.py`
- `src/tapps_brain/services/hive_service.py`
- `src/tapps_brain/services/flywheel_service.py`
- `src/tapps_brain/services/feedback_service.py`
- `src/tapps_brain/services/agents_service.py`
- `src/tapps_brain/services/maintenance_service.py`
- `src/tapps_brain/services/diagnostics_service.py`
- `tests/services/`
- `tests/golden/`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Capture golden responses from current stdio MCP for all tools into tests/golden/ (`tests/golden/`)
- [ ] Create services/ package skeleton with one module per domain (`src/tapps_brain/services/__init__.py`)
- [ ] Extract memory_* tool bodies into memory_service.py (`src/tapps_brain/services/memory_service.py`)
- [ ] Extract hive_* tool bodies into hive_service.py (`src/tapps_brain/services/hive_service.py`)
- [ ] Extract flywheel_* and feedback_* into their service modules (`src/tapps_brain/services/flywheel_service.py`)
- [ ] Extract agent_*, maintenance_*, diagnostics_* into services (`src/tapps_brain/services/agents_service.py`)
- [ ] Rewrite mcp_server.py tool handlers as thin delegates (`src/tapps_brain/mcp_server.py`)
- [ ] Add service-layer unit tests (`tests/services/`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] All @mcp.tool() handlers in src/tapps_brain/mcp_server.py delegate to src/tapps_brain/services/*.py functions
- [ ] Service functions take (store
- [ ] project_id
- [ ] agent_id
- [ ] **typed_kwargs) and return JSON-serializable dicts — no MCP SDK imports
- [ ] mcp_server.py reduced to thin adapters (~200 lines of registration + delegation)
- [ ] Golden-response tests captured from the current stdio server pass against the refactored code with zero diff for all 70+ tools
- [ ] Service modules split by domain: memory_service
- [ ] hive_service
- [ ] flywheel_service
- [ ] feedback_service
- [ ] agents_service
- [ ] maintenance_service
- [ ] diagnostics_service
- [ ] Unit tests cover the service layer directly (without MCP transport) at ≥ 85% line coverage

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 70](../EPIC-070.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_all_mcptool_handlers_srctappsbrainmcpserverpy_delegate` -- All @mcp.tool() handlers in src/tapps_brain/mcp_server.py delegate to src/tapps_brain/services/*.py functions
2. `test_ac2_service_functions_take_store` -- Service functions take (store
3. `test_ac3_projectid` -- project_id
4. `test_ac4_agentid` -- agent_id
5. `test_ac5_typedkwargs_return_jsonserializable_dicts_no_mcp_sdk_imports` -- **typed_kwargs) and return JSON-serializable dicts — no MCP SDK imports
6. `test_ac6_mcpserverpy_reduced_thin_adapters_200_lines_registration_delegation` -- mcp_server.py reduced to thin adapters (~200 lines of registration + delegation)
7. `test_ac7_goldenresponse_tests_captured_from_current_stdio_server_pass_against` -- Golden-response tests captured from the current stdio server pass against the refactored code with zero diff for all 70+ tools
8. `test_ac8_service_modules_split_by_domain_memoryservice` -- Service modules split by domain: memory_service
9. `test_ac9_hiveservice` -- hive_service
10. `test_ac10_flywheelservice` -- flywheel_service
11. `test_ac11_feedbackservice` -- feedback_service
12. `test_ac12_agentsservice` -- agents_service
13. `test_ac13_maintenanceservice` -- maintenance_service
14. `test_ac14_diagnosticsservice` -- diagnostics_service
15. `test_ac15_unit_tests_cover_service_layer_directly_without_mcp_transport_at_85` -- Unit tests cover the service layer directly (without MCP transport) at ≥ 85% line coverage

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
