# Story 70.13 -- AgentForge BrainBridge port — reference implementation

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain maintainer, **I want** a reference port of AgentForge's BrainBridge using the new client, **so that** we have proof the client surface is actually sufficient to replace embedded use

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 8 | **Size:** L | **Status:** In Progress

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the epic ends with evidence, not assertions. If we can't port ~925 LOC of real resilience code down to < 250 LOC of client-plus-resilience-wrapper, the abstraction is wrong.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **AgentForge BrainBridge port — reference implementation** will enable **tapps-brain maintainer** to **a reference port of AgentForge's BrainBridge using the new client**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `examples/agentforge_bridge/brain_bridge.py`
- `examples/agentforge_bridge/test_brain_bridge.py`
- `examples/agentforge_bridge/README.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement agentforge brainbridge port — reference implementation (`examples/agentforge_bridge/brain_bridge.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] examples/agentforge_bridge/ directory with a working port of AgentForge's BrainBridge using TappsBrainClient
- [ ] Circuit breaker
- [ ] bounded write queue
- [ ] exponential backoff preserved but thin
- [ ] Target: < 250 LOC vs current ~925
- [ ] Tests mirror AgentForge's test_brain_bridge.py against a live dockerized brain
- [ ] README explains what was embedded vs what now crosses the wire
- [ ] Any gap found during the port filed back as a follow-up story on this epic
- [ ] Does NOT become a runtime dep of tapps-brain — lives in examples/ as documentation

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] AgentForge BrainBridge port — reference implementation code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_examplesagentforgebridge_directory_working_port_agentforges_brainbridge` -- examples/agentforge_bridge/ directory with a working port of AgentForge's BrainBridge using TappsBrainClient
2. `test_ac2_circuit_breaker` -- Circuit breaker
3. `test_ac3_bounded_write_queue` -- bounded write queue
4. `test_ac4_exponential_backoff_preserved_but_thin` -- exponential backoff preserved but thin
5. `test_ac5_target_250_loc_vs_current_925` -- Target: < 250 LOC vs current ~925
6. `test_ac6_tests_mirror_agentforges_testbrainbridgepy_against_live_dockerized` -- Tests mirror AgentForge's test_brain_bridge.py against a live dockerized brain
7. `test_ac7_readme_explains_what_was_embedded_vs_what_now_crosses_wire` -- README explains what was embedded vs what now crosses the wire
8. `test_ac8_any_gap_found_during_port_filed_back_as_followup_story_on_this_epic` -- Any gap found during the port filed back as a follow-up story on this epic
9. `test_ac9_does_not_become_runtime_dep_tappsbrain_lives_examples_as_documentation` -- Does NOT become a runtime dep of tapps-brain — lives in examples/ as documentation

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Port should pass the AgentForge test suite when substituted for backend/memory/brain.py
- Keep the example tracked by CI so server changes that break the client surface fail here

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-070.11

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [ ] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [ ] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
