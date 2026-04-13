# Story 68.6 -- Agents and Hive page — SVG topology diagram and registry

<!-- docsmcp:start:user-story -->

> **As a** team lead managing a multi-agent tapps-brain deployment, **I want** an Agents page that shows a topology diagram of agent-to-namespace-to-hive connections alongside the agent registry table, **so that** I can see at a glance which agents share namespaces and whether the Hive hub is reachable

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** L

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that users managing multi-agent Hive deployments can see the relationship between agents, namespaces, and the Hive hub as a spatial diagram — not as two disconnected tables that require mental assembly. A topology view communicates cluster health and isolation boundaries in seconds rather than minutes of table-reading.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Move the Hive Hub panel and Agents table to data-page=agents. Add a client-side SVG topology diagram generated from snapshot hive_health JSON (agents, namespaces, hub connection). Agents render as small amber circles; namespaces as rounded-rect containers; the Hive hub as a central octagon. Edges drawn as SVG path elements. Node count capped at 50 with a truncation indicator. An agent-detail slide-in drawer opens on row click (or SVG node click) showing agent_id, namespace, scope, registered_at, last_write_at. The online/offline Hive badge moves to the page header (prominent) rather than buried in a panel. All SVG is generated from snapshot data at render time — no external graph library.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `examples/brain-visual/index.html`
- `examples/brain-visual/brain-visual-help.js`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Move Hive Hub panel and Agents table markup to data-page=agents section element (`examples/brain-visual/index.html`)
- [ ] Add renderAgentTopology(snapshot) function: generates SVG element with agent circles, namespace rounded-rects, Hive hub octagon, and path edges from snapshot.hive_health data; cap at 50 nodes (`examples/brain-visual/index.html`)
- [ ] Add node truncation indicator: if agent count > 50, add '+ N more' text node to SVG and a note below the diagram (`examples/brain-visual/index.html`)
- [ ] Add SVG node click handler: clicking an agent circle opens the agent-detail drawer with agent data; clicking outside closes it (same pattern as existing help drawer) (`examples/brain-visual/index.html`)
- [ ] Add agent-detail drawer: fixed right panel (same .help-drawer CSS class or new .agent-drawer), shows agent_id, namespace, scope, registered_at, last_write_at fields; closeable via Escape key or X button (`examples/brain-visual/index.html`)
- [ ] Add row click handler to Agents table that also opens the agent-detail drawer with that agent's data (`examples/brain-visual/index.html`)
- [ ] Move Hive online/offline badge to the agents page section header (h2 level), styled prominently with amber gradient for Online and --fg-dim for Offline/Unknown (`examples/brain-visual/index.html`)
- [ ] Add SVG accessibility: role=img aria-label on the SVG element; aria-label on each node group; title element inside SVG for screen reader fallback (`examples/brain-visual/index.html`)
- [ ] Add help article for agent topology diagram in brain-visual-help.js explaining node types and edge meanings (`examples/brain-visual/brain-visual-help.js`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Agents page accessible at #agents; Hive Hub and Agents table render from demo JSON
- [ ] SVG topology diagram renders with agent circles
- [ ] namespace containers
- [ ] hub octagon
- [ ] and connecting edges for demo data
- [ ] Clicking an agent node or table row opens the agent-detail drawer with correct agent_id
- [ ] namespace
- [ ] scope
- [ ] registered_at
- [ ] last_write_at values
- [ ] Agent-detail drawer closeable with Escape key and X button; focus returns to the triggering element on close
- [ ] When demo JSON has > 50 agents
- [ ] diagram shows 50 nodes and displays '+ N more' truncation indicator
- [ ] Hive Online/Offline badge is in the page section heading and visually prominent (amber gradient for Online)
- [ ] SVG element has role=img and aria-label; no accessibility violation flagged by Lighthouse for the SVG
- [ ] No external graph library added — SVG generated entirely by vanilla JS

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Agents and Hive page — SVG topology diagram and registry code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_agents_page_accessible_at_agents_hive_hub_agents_table_render_from_demo` -- Agents page accessible at #agents; Hive Hub and Agents table render from demo JSON
2. `test_ac2_svg_topology_diagram_renders_agent_circles` -- SVG topology diagram renders with agent circles
3. `test_ac3_namespace_containers` -- namespace containers
4. `test_ac4_hub_octagon` -- hub octagon
5. `test_ac5_connecting_edges_demo_data` -- and connecting edges for demo data
6. `test_ac6_clicking_agent_node_or_table_row_opens_agentdetail_drawer_correct` -- Clicking an agent node or table row opens the agent-detail drawer with correct agent_id
7. `test_ac7_namespace` -- namespace
8. `test_ac8_scope` -- scope
9. `test_ac9_registeredat` -- registered_at
10. `test_ac10_lastwriteat_values` -- last_write_at values
11. `test_ac11_agentdetail_drawer_closeable_escape_key_x_button_focus_returns` -- Agent-detail drawer closeable with Escape key and X button; focus returns to the triggering element on close
12. `test_ac12_demo_json_50_agents` -- When demo JSON has > 50 agents
13. `test_ac13_diagram_shows_50_nodes_displays_n_more_truncation_indicator` -- diagram shows 50 nodes and displays '+ N more' truncation indicator
14. `test_ac14_hive_onlineoffline_badge_page_section_heading_visually_prominent_amber` -- Hive Online/Offline badge is in the page section heading and visually prominent (amber gradient for Online)
15. `test_ac15_svg_element_roleimg_arialabel_no_accessibility_violation_flagged_by` -- SVG element has role=img and aria-label; no accessibility violation flagged by Lighthouse for the SVG
16. `test_ac16_no_external_graph_library_added_svg_generated_entirely_by_vanilla_js` -- No external graph library added — SVG generated entirely by vanilla JS

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- SVG layout algorithm: namespaces as fixed-size rounded-rects arranged in a row; agents placed inside their namespace rect evenly spaced; Hive hub centered at top; edges drawn as quadratic bezier paths (SVG path Q command) from hub to each namespace rect midpoint
- Use SVG foreignObject for text labels that need CSS styling; otherwise use SVG text elements with font-family from --font-body CSS variable (SVG text does not inherit CSS font by default — set explicitly)
- Agent-detail drawer reuses the .help-drawer CSS pattern (position: fixed
- right: 0
- slide-in from right) with a different trigger mechanism — do not duplicate CSS
- Node count 50 cap is a hard limit — log a console.info if truncated so developers notice during testing

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-068.1 (router; also agent-detail drawer reuses help-drawer CSS)

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
