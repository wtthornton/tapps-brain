# Story 66.4 -- MCP tool registration audit and fix

<!-- docsmcp:start:user-story -->

> **As a** MCP host integrator (Cursor / Claude Code / VS Code), **I want** memory_gc_config_set, memory_consolidation_config_set, and the related GC/consolidation tools to be registered on the MCP server, **so that** IDE agents can read and tune GC and consolidation policy without shelling out to the CLI

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the MCP server exposes the GC and consolidation config tools that test_mcp_server.py expects. Currently 15+ tests in TestGcAndConsolidationConfigTools fail with KeyError tool not found memory_gc_config_set. The tests pre-date ADR-007 stage 2 so the gap may be a pre-existing registration bug rather than a regression — this story is the investigation and the fix.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Audit mcp_server.py and the generated docs/generated/mcp-tools-manifest.json against test_mcp_server.py expectations. Determine whether memory_gc_config_set and friends were ever registered. If pre-existing, register them in mcp_server.py with proper schemas drawn from the existing CLI gc-config and consolidation-config commands. If introduced by ADR-007, restore them. Regenerate the manifest. Verify all 15 TestGcAndConsolidationConfigTools tests pass plus the 2 TestMcpServerInputValidation022C tests.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/mcp_server.py`
- `docs/generated/mcp-tools-manifest.json`
- `scripts/generate_mcp_tool_manifest.py`
- `tests/unit/test_mcp_server.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Grep mcp_server.py for memory_gc_config / memory_consolidation_config registrations and identify any gaps (`src/tapps_brain/mcp_server.py`)
- [ ] Cross-reference docs/generated/mcp-tools-manifest.json with the failing test names (`docs/generated/mcp-tools-manifest.json`)
- [ ] Determine via git blame whether the missing tools predate ADR-007 (`src/tapps_brain/mcp_server.py`)
- [ ] Register memory_gc_config / memory_gc_config_set / memory_consolidation_config / memory_consolidation_config_set with proper input schemas drawn from CLI maintenance gc-config and consolidation-config (`src/tapps_brain/mcp_server.py`)
- [ ] Investigate test_memory_import_unknown_tier_normalized and test_memory_import_invalid_source_counts_as_error and resolve their input validation expectations (`src/tapps_brain/mcp_server.py`)
- [ ] Regenerate docs/generated/mcp-tools-manifest.json (`scripts/generate_mcp_tool_manifest.py`)
- [ ] Verify all 17 affected tests pass (`tests/unit/test_mcp_server.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] mcp_server.py registers memory_gc_config
- [ ] memory_gc_config_set
- [ ] memory_consolidation_config
- [ ] memory_consolidation_config_set with input schemas matching the CLI command equivalents
- [ ] docs/generated/mcp-tools-manifest.json includes the new tool names
- [ ] all 15 TestGcAndConsolidationConfigTools tests pass
- [ ] both TestMcpServerInputValidation022C tests pass
- [ ] no regression in other test_mcp_server tests
- [ ] root cause documented in the PR description (pre-existing vs introduced by ADR-007)

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] MCP tool registration audit and fix code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_mcpserverpy_registers_memorygcconfig` -- mcp_server.py registers memory_gc_config
2. `test_ac2_memorygcconfigset` -- memory_gc_config_set
3. `test_ac3_memoryconsolidationconfig` -- memory_consolidation_config
4. `test_ac4_memoryconsolidationconfigset_input_schemas_matching_cli_command` -- memory_consolidation_config_set with input schemas matching the CLI command equivalents
5. `test_ac5_docsgeneratedmcptoolsmanifestjson_includes_new_tool_names` -- docs/generated/mcp-tools-manifest.json includes the new tool names
6. `test_ac6_all_15_testgcandconsolidationconfigtools_tests_pass` -- all 15 TestGcAndConsolidationConfigTools tests pass
7. `test_ac7_both_testmcpserverinputvalidation022c_tests_pass` -- both TestMcpServerInputValidation022C tests pass
8. `test_ac8_no_regression_other_testmcpserver_tests` -- no regression in other test_mcp_server tests
9. `test_ac9_root_cause_documented_pr_description_preexisting_vs_introduced_by` -- root cause documented in the PR description (pre-existing vs introduced by ADR-007)

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- If the gap is pre-existing this story actually closes a separate latent bug exposed by the broader test pass after ADR-007. Capture that finding clearly so it does not get attributed to the rip-out. The CLI gc-config command has the canonical schema for input validation; mirror it.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- EPIC-066 STORY-066.6 (CI Postgres) for full verification

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
