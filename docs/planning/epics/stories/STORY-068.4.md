# Story 68.4 -- Memory page — pulse, groups, tags, histograms

<!-- docsmcp:start:user-story -->

> **As a** brain-visual operator, **I want** a single Memory page that shows tier distribution, memory groups, tags, and access histograms in a coherent layout, **so that** I can understand what is stored and how it is accessed without jumping between four different scroll positions

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the four existing panels that answer 'what is stored in this brain?' — Pulse tier bars, Memory Groups table, Tag Cloud, and Access Histograms — are consolidated into one purposeful page where each panel gets adequate height and the relationships between them are visually evident.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Move Pulse, Memory Groups, Tag Cloud, and Access Histograms from the main scroll into data-page=memory. Use a CSS 2-column subgrid layout: left column holds Pulse stacked bar (taller than current) and Access Histogram; right column holds Memory Groups table and Tag Cloud. Add oldest/newest entry timestamp callouts below the Pulse bar. Add a total memory size estimate tile. Increase Tag Cloud max tags from 20 to 40 (privacy tier permitting). Increase bar chart heights by 50% — they were cramped in the single-page view.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `examples/brain-visual/index.html`
- `examples/brain-visual/brain-visual-help.js`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Move Pulse section, Memory Groups section, Tag Cloud section, and Access Histogram section markup into data-page=memory section element (`examples/brain-visual/index.html`)
- [ ] Apply 2-column CSS subgrid to memory page: left column (Pulse + Histogram), right column (Memory Groups + Tag Cloud); collapse to 1-column at 640px via container query (`examples/brain-visual/index.html`)
- [ ] Increase .bar-chart min-height from current value to at least 200px; increase Tag Cloud max display from 20 to 40 tags in renderTagCloud() (`examples/brain-visual/index.html`)
- [ ] Add oldest_entry_age_days and newest entry timestamp callout tiles below the Pulse bar, populated from snapshot.store.oldest_entry_age_days and snapshot.store.newest_entry_age_days (if available) (`examples/brain-visual/index.html`)
- [ ] Add a total memory size estimate KPI tile (entry_count × average_entry_size_estimate) with a tooltip explaining it is approximate (`examples/brain-visual/index.html`)
- [ ] Add help pill for Memory Groups section if not already present; add help article entry for memory-groups concept in brain-visual-help.js (`examples/brain-visual/brain-visual-help.js`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Memory page accessible at #memory; all four panels visible without horizontal scroll on 1280px viewport
- [ ] 2-column subgrid layout: Pulse and Histogram in left column
- [ ] Memory Groups and Tag Cloud in right column; collapses to 1-column at 640px
- [ ] Pulse bar chart height is at least 200px
- [ ] Tag Cloud displays up to 40 tags when privacy tier is local; correct fewer-tag message shown for standard/strict
- [ ] Oldest entry age and newest entry timestamp callouts appear below Pulse bar when data is available in snapshot
- [ ] All panels have help pills (?) with working help drawer entries
- [ ] No Pulse / Tag Cloud / Memory Groups / Histogram content remains in the Overview page section after this story

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Memory page — pulse, groups, tags, histograms code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] No regressions introduced
- [ ] ralph-reviewer run on memory page markup and JS changes; no Critical issues open
- [ ] All ACs verified at `http://localhost:8090` with demo JSON

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_memory_page_accessible_at_memory_all_four_panels_visible_without` -- Memory page accessible at #memory; all four panels visible without horizontal scroll on 1280px viewport
2. `test_ac2_2column_subgrid_layout_pulse_histogram_left_column` -- 2-column subgrid layout: Pulse and Histogram in left column
3. `test_ac3_memory_groups_tag_cloud_right_column_collapses_1column_at_640px` -- Memory Groups and Tag Cloud in right column; collapses to 1-column at 640px
4. `test_ac4_pulse_bar_chart_height_at_least_200px` -- Pulse bar chart height is at least 200px
5. `test_ac5_tag_cloud_displays_up_40_tags_privacy_tier_local_correct_fewertag` -- Tag Cloud displays up to 40 tags when privacy tier is local; correct fewer-tag message shown for standard/strict
6. `test_ac6_oldest_entry_age_newest_entry_timestamp_callouts_appear_below_pulse_bar` -- Oldest entry age and newest entry timestamp callouts appear below Pulse bar when data is available in snapshot
7. `test_ac7_all_panels_help_pills_working_help_drawer_entries` -- All panels have help pills (?) with working help drawer entries
8. `test_ac8_no_pulse_tag_cloud_memory_groups_histogram_content_remains_overview` -- No Pulse / Tag Cloud / Memory Groups / Histogram content remains in the Overview page section after this story

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- CSS subgrid on the memory page requires the memory section to establish a 2-column grid; child panels use grid-column: 1 or grid-column: 2 explicitly
- oldest_entry_age_days is already in VisualSnapshot.store — check field name against visual_snapshot.py before referencing in JS render path
- Tag Cloud increase from 20 to 40 is a client-side render change only; the snapshot already exports top-N tags in local privacy mode — verify N is ≥ 40 in visual_snapshot.py before wiring
- **Dev workflow:** start the tapps-brain HTTP adapter (`tapps-brain mcp start --http` or `docker compose up tapps-brain-mcp`), then `cd examples/brain-visual && python3 -m http.server 8090`; the page polls `/snapshot` live — all memory page ACs are testable against the live feed

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-068.1 (router)
- STORY-068.2 (overview page removes these panels from overview)

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
