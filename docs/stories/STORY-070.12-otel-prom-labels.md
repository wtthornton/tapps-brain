# Story 70.12 -- OTel + Prometheus label enrichment

<!-- docsmcp:start:user-story -->

> **As a** operator of a shared brain deployment, **I want** metrics and traces labeled with project_id and agent_id, **so that** I can diagnose noisy-neighbor and per-tenant latency without server-side log grepping

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S | **Status:** In Progress

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that multi-tenant operations are observable. Without per-tenant labels, one bad agent in one project can degrade everyone silently and untraceably.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **OTel + Prometheus label enrichment** will enable **operator of a shared brain deployment** to **metrics and traces labeled with project_id and agent_id**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/otel_tracer.py`
- `src/tapps_brain/metrics.py`
- `src/tapps_brain/http_adapter.py`
- `examples/observability/grafana-per-tenant.json`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement otel + prometheus label enrichment (`src/tapps_brain/otel_tracer.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] HTTP middleware extracts traceparent; MCP already does (3.4.0) — both paths unified
- [ ] All memory-op spans carry attributes: tapps.project_id
- [ ] tapps.agent_id
- [ ] tapps.scope
- [ ] tapps.tool
- [ ] tapps.rows_returned
- [ ] tapps.latency_ms
- [ ] Prometheus histograms + counters gain labels: project_id
- [ ] agent_id
- [ ] tool
- [ ] status
- [ ] Label cardinality capped: agent_id limited to top-100 distinct values per scrape window
- [ ] overflow mapped to "other"
- [ ] Existing metrics names unchanged — only labels added
- [ ] Grafana dashboard JSON in examples/observability/ showing per-project breakdown

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] OTel + Prometheus label enrichment code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_http_middleware_extracts_traceparent_mcp_already_does_340_both_paths` -- HTTP middleware extracts traceparent; MCP already does (3.4.0) — both paths unified
2. `test_ac2_all_memoryop_spans_carry_attributes_tappsprojectid` -- All memory-op spans carry attributes: tapps.project_id
3. `test_ac3_tappsagentid` -- tapps.agent_id
4. `test_ac4_tappsscope` -- tapps.scope
5. `test_ac5_tappstool` -- tapps.tool
6. `test_ac6_tappsrowsreturned` -- tapps.rows_returned
7. `test_ac7_tappslatencyms` -- tapps.latency_ms
8. `test_ac8_prometheus_histograms_counters_gain_labels_projectid` -- Prometheus histograms + counters gain labels: project_id
9. `test_ac9_agentid` -- agent_id
10. `test_ac10_tool` -- tool
11. `test_ac11_status` -- status
12. `test_ac12_label_cardinality_capped_agentid_limited_top100_distinct_values_per` -- Label cardinality capped: agent_id limited to top-100 distinct values per scrape window
13. `test_ac13_overflow_mapped_other` -- overflow mapped to "other"
14. `test_ac14_existing_metrics_names_unchanged_only_labels_added` -- Existing metrics names unchanged — only labels added
15. `test_ac15_grafana_dashboard_json_examplesobservability_showing_perproject` -- Grafana dashboard JSON in examples/observability/ showing per-project breakdown

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Cardinality cap implemented as a LRU of distinct label values per registry
- Never include raw user input as a label

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-070.2
- STORY-070.7

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
