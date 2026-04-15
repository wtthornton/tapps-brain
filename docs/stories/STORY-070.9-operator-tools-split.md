# Story 70.9 -- Operator-tool separation

<!-- docsmcp:start:user-story -->

> **As a** AGENT.md author, **I want** to grant brain memory access without also granting destructive operator tools, **so that** a single mcp: brain grant is safe by default

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S | **Status:** Proposed

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that AgentForge agents can declare the brain MCP server in their AGENT.md without inadvertently getting gc_run or consolidation_merge. MCP grants are currently coarse (per-server); splitting servers gives per-capability granularity.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Operator-tool separation** will enable **AGENT.md author** to **to grant brain memory access without also granting destructive operator tools**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/mcp_server/__init__.py`
- `src/tapps_brain/mcp_server/standard.py`
- `src/tapps_brain/mcp_server/operator.py`
- `pyproject.toml`
- `docs/guides/agent-md-wiring.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement operator-tool separation (`src/tapps_brain/mcp_server/__init__.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Two FastMCP instances: tapps-brain-mcp (standard) and tapps-brain-operator-mcp (operator)
- [ ] Operator server exposes memory_gc_run
- [ ] memory_consolidation_merge
- [ ] memory_consolidation_undo
- [ ] memory_import
- [ ] memory_export
- [ ] migration tools
- [ ] Standard server loses those tools even with TAPPS_BRAIN_OPERATOR_TOOLS=1
- [ ] Both share the service layer and error taxonomy
- [ ] Separate CLI entry points (tapps-brain-mcp
- [ ] tapps-brain-operator-mcp)
- [ ] Dockerfile.http and compose stack support running both on different ports
- [ ] Documented AGENT.md wiring example showing safe brain-only grant

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Operator-tool separation code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_two_fastmcp_instances_tappsbrainmcp_standard_tappsbrainoperatormcp` -- Two FastMCP instances: tapps-brain-mcp (standard) and tapps-brain-operator-mcp (operator)
2. `test_ac2_operator_server_exposes_memorygcrun` -- Operator server exposes memory_gc_run
3. `test_ac3_memoryconsolidationmerge` -- memory_consolidation_merge
4. `test_ac4_memoryconsolidationundo` -- memory_consolidation_undo
5. `test_ac5_memoryimport` -- memory_import
6. `test_ac6_memoryexport` -- memory_export
7. `test_ac7_migration_tools` -- migration tools
8. `test_ac8_standard_server_loses_those_tools_even_tappsbrainoperatortools1` -- Standard server loses those tools even with TAPPS_BRAIN_OPERATOR_TOOLS=1
9. `test_ac9_both_share_service_layer_error_taxonomy` -- Both share the service layer and error taxonomy
10. `test_ac10_separate_cli_entry_points_tappsbrainmcp` -- Separate CLI entry points (tapps-brain-mcp
11. `test_ac11_tappsbrainoperatormcp` -- tapps-brain-operator-mcp)
12. `test_ac12_dockerfilehttp_compose_stack_support_running_both_on_different_ports` -- Dockerfile.http and compose stack support running both on different ports
13. `test_ac13_documented_agentmd_wiring_example_showing_safe_brainonly_grant` -- Documented AGENT.md wiring example showing safe brain-only grant

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Shared code in tapps_brain.mcp_server.core
- Standard in tapps_brain.mcp_server.standard
- Operator in tapps_brain.mcp_server.operator

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
