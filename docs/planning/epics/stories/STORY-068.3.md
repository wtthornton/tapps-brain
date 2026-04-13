# Story 68.3 -- Health page — scorecard with filter bar and issue workflow

<!-- docsmcp:start:user-story -->

> **As a** brain-visual operator, **I want** a dedicated health page where I can filter scorecard rows to only show failures and export a GitHub issue in one click, **so that** I can triage and act on failures without scrolling past passing checks or unrelated dashboard panels

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the Health scorecard — the primary daily-use feature for operators triaging memory store health — gets a full page of screen real estate with actionable filter controls, instead of being buried mid-scroll on a monolithic page.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Move the full scorecard section to data-page=health. Add a filter bar above the scorecard with four toggle buttons: All / Fail / Warn / Pass — implemented as <button role=radio> inside a radiogroup; active filter highlights with amber gradient. Add a sort toggle (Severity / Category) that reorders scorecard rows client-side without a server call. If live polling is active, mark any row that changed status since the last snapshot with a diff-marker badge (NEW / CHANGED) using a shallow comparison of the previous render's status values. Give the notes textarea and GitHub/plain-text export buttons more vertical space — they are the action endpoint of the Health page workflow.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `examples/brain-visual/index.html`
- `examples/brain-visual/brain-visual-help.js`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Move scorecard section markup into data-page=health section element; set hidden attribute (`examples/brain-visual/index.html`)
- [ ] Add filter bar: four <button role=radio> inside <div role=radiogroup aria-label=Filter> for All / Fail / Warn / Pass; store active filter in module-level variable; wire click to refilterScorecard() (`examples/brain-visual/index.html`)
- [ ] Add refilterScorecard(filter) function: toggles hidden on scorecard rows based on their data-status attribute; updates row count label (`examples/brain-visual/index.html`)
- [ ] Add sort toggle button (Severity / Category): sorts .scorecard-row elements by data-severity or data-category attribute in-place using DOM insertBefore (`examples/brain-visual/index.html`)
- [ ] Add previousScorecard cache variable; in the render path, compare current scorecard items against previousScorecard and add/remove .diff-badge (NEW/CHANGED) on changed rows; only active when live polling is on (`examples/brain-visual/index.html`)
- [ ] Add .diff-badge CSS class: small amber pill, top-right corner of scorecard row, fade-in via prefers-reduced-motion gate (`examples/brain-visual/index.html`)
- [ ] Increase textarea min-height from current value to 8rem; add a visible character count; move export buttons (GitHub Markdown / plain text) into a sticky bottom bar within the health page section (`examples/brain-visual/index.html`)
- [ ] Update help articles in brain-visual-help.js if any new scorecard concepts are introduced (filter bar, diff markers) (`examples/brain-visual/brain-visual-help.js`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Health page accessible at #health; scorecard rows render correctly from demo JSON
- [ ] Filter bar All/Fail/Warn/Pass toggles correctly hide/show rows; active button has amber gradient background and aria-checked=true
- [ ] Row count label updates to reflect filtered count (e.g. '3 of 12 checks')
- [ ] Sort toggle reorders rows by severity (fail first) and by category (alphabetical grouping)
- [ ] When live polling detects a status change on a row
- [ ] a NEW or CHANGED badge appears on that row within one poll cycle; badge is absent when status is unchanged
- [ ] Notes textarea has min-height 8rem; export buttons are visible without scrolling when health page is active on a 768px-tall viewport
- [ ] All filter bar buttons and sort toggle are keyboard-operable; Tab navigates through filter bar then through scorecard rows then to textarea
- [ ] diff-badge fade-in is absent when prefers-reduced-motion is enabled

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Health page — scorecard with filter bar and issue workflow code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_health_page_accessible_at_health_scorecard_rows_render_correctly_from` -- Health page accessible at #health; scorecard rows render correctly from demo JSON
2. `test_ac2_filter_bar_allfailwarnpass_toggles_correctly_hideshow_rows_active` -- Filter bar All/Fail/Warn/Pass toggles correctly hide/show rows; active button has amber gradient background and aria-checked=true
3. `test_ac3_row_count_label_updates_reflect_filtered_count_eg_3_12_checks` -- Row count label updates to reflect filtered count (e.g. '3 of 12 checks')
4. `test_ac4_sort_toggle_reorders_rows_by_severity_fail_first_by_category` -- Sort toggle reorders rows by severity (fail first) and by category (alphabetical grouping)
5. `test_ac5_live_polling_detects_status_change_on_row` -- When live polling detects a status change on a row
6. `test_ac6_new_or_changed_badge_appears_on_row_within_one_poll_cycle_badge_absent` -- a NEW or CHANGED badge appears on that row within one poll cycle; badge is absent when status is unchanged
7. `test_ac7_notes_textarea_minheight_8rem_export_buttons_visible_without_scrolling` -- Notes textarea has min-height 8rem; export buttons are visible without scrolling when health page is active on a 768px-tall viewport
8. `test_ac8_all_filter_bar_buttons_sort_toggle_keyboardoperable_tab_navigates` -- All filter bar buttons and sort toggle are keyboard-operable; Tab navigates through filter bar then through scorecard rows then to textarea
9. `test_ac9_diffbadge_fadein_absent_prefersreducedmotion_enabled` -- diff-badge fade-in is absent when prefers-reduced-motion is enabled

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Filter state is a module-level variable (let activeHealthFilter = 'all') — no URL param encoding required for v1
- Diff comparison uses a shallow Map(check_id → status) built during the previous render; stored as module-level previousScorecardStatuses
- Sort is a DOM sort (Array.from querySelectorAll
- sort
- then re-append children) — no data re-fetch
- radiogroup / role=radio pattern requires manual keydown ArrowLeft/ArrowRight handling to move focus between buttons per ARIA APG radiogroup pattern

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-068.1 (router)
- STORY-064.3 (motion tokens for diff-badge fade-in)

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
