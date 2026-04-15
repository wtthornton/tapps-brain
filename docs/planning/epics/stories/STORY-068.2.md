# Story 68.2 -- Overview page — decision strip and health summary

<!-- docsmcp:start:user-story -->

> **As a** brain-visual operator, **I want** the landing page to answer 'is this brain healthy?' at a glance and let me click into the right detail page, **so that** I do not need to scroll past KPI tiles to find whether there are any failures

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that any user who opens the dashboard sees the most important signal — is this brain healthy right now — within 5 seconds and without scrolling. The Overview is the landing page that earns the right to send users to deeper pages.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Refactor the current hero bento grid into the Overview page (data-page=overview). Add a health-summary strip below the hero bento that shows aggregated pass/warn/fail counts as amber-styled clickable badges; clicking a badge navigates to #health. Add quick-nav tiles for each of the 5 other pages, each showing its page title, one-sentence description, and a live metric from the snapshot (e.g. Retrieval tile shows effective_mode, Agents tile shows agent count). Apply CSS subgrid to the bento for responsive density without media query hacks. Remove any content that belongs on a dedicated page (e.g. Hive Hub detail table, Tag Cloud, Access Histograms) — those move to 068.4/068.6 stories.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `examples/brain-visual/index.html`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Rename the existing hero bento section to data-page=overview and ensure it is the default visible page (no hidden attribute) (`examples/brain-visual/index.html`)
- [x] Add health-summary strip below hero bento: three .hs-badge anchor elements (pass count, warn count, fail count) linking to #health with amber warn/fail styling via data-nonzero (`examples/brain-visual/index.html`)
- [x] Add updateHealthSummaryStrip(snapshot) function that derives pass/warn/fail totals from snapshot scorecard array and updates badge text; call from existing render path (`examples/brain-visual/index.html`)
- [x] Add five quick-nav tiles in a CSS auto-fill grid below the summary strip; each tile has page title, description sentence, and one live metric slot (`examples/brain-visual/index.html`)
- [x] Quick-nav tiles are native `<a href="#pagename">` elements — keyboard-focusable and Enter-activatable without JS (`examples/brain-visual/index.html`)
- [x] Apply container queries to the hero bento: 2-column at 640px and 1-column at 480px; removed media-query breakpoints on bento (`examples/brain-visual/index.html`)
- [x] Hive Hub detail table, Tag Cloud, and Access Histogram are on dedicated pages (placed there by 068.1 scaffolding) — not present in overview data-page div (`examples/brain-visual/index.html`)
- [x] WARN/FAIL banner (`#hs-banner`) with gradient border appears when fail > 0 or warn > 0; uses role=alert on first 0→N fail transition (`examples/brain-visual/index.html`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Overview page shows hero bento (KPI tiles) and health-summary strip above the fold at 1280px viewport
- [ ] Health-summary strip shows correct pass/warn/fail counts derived from loaded snapshot
- [ ] Clicking the warn badge navigates to #health; clicking fail badge navigates to #health; counts match scorecard totals
- [ ] Quick-nav tiles show live metrics from snapshot (retrieval effective_mode
- [ ] agent count
- [ ] entry count
- [ ] etc.)
- [ ] Large WARN/FAIL banner appears when snapshot has ≥1 fail item; disappears when all pass
- [ ] CSS subgrid is used for bento layout; no explicit media query breakpoints on the bento (container queries only)
- [ ] No Hive Hub detail table
- [ ] Tag Cloud
- [ ] or Access Histogram content remains on the Overview page
- [ ] All quick-nav tiles are keyboard-focusable and activatable with Enter/Space

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [x] All tasks completed
- [x] Overview page — decision strip and health summary code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] No regressions introduced
- [ ] ralph-reviewer run on health-summary strip and quick-nav changes in `index.html`; no Critical issues open

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_overview_page_shows_hero_bento_kpi_tiles_healthsummary_strip_above_fold` -- Overview page shows hero bento (KPI tiles) and health-summary strip above the fold at 1280px viewport
2. `test_ac2_healthsummary_strip_shows_correct_passwarnfail_counts_derived_from` -- Health-summary strip shows correct pass/warn/fail counts derived from loaded snapshot
3. `test_ac3_clicking_warn_badge_navigates_health_clicking_fail_badge_navigates` -- Clicking the warn badge navigates to #health; clicking fail badge navigates to #health; counts match scorecard totals
4. `test_ac4_quicknav_tiles_show_live_metrics_from_snapshot_retrieval_effectivemode` -- Quick-nav tiles show live metrics from snapshot (retrieval effective_mode
5. `test_ac5_agent_count` -- agent count
6. `test_ac6_entry_count` -- entry count
7. `test_ac7_etc` -- etc.)
8. `test_ac8_large_warnfail_banner_appears_snapshot_1_fail_item_disappears_all_pass` -- Large WARN/FAIL banner appears when snapshot has ≥1 fail item; disappears when all pass
9. `test_ac9_css_subgrid_used_bento_layout_no_explicit_media_query_breakpoints_on` -- CSS subgrid is used for bento layout; no explicit media query breakpoints on the bento (container queries only)
10. `test_ac10_no_hive_hub_detail_table` -- No Hive Hub detail table
11. `test_ac11_tag_cloud` -- Tag Cloud
12. `test_ac12_or_access_histogram_content_remains_on_overview_page` -- or Access Histogram content remains on the Overview page
13. `test_ac13_all_quicknav_tiles_keyboardfocusable_activatable_enterspace` -- All quick-nav tiles are keyboard-focusable and activatable with Enter/Space

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- CSS subgrid requires grid-template-columns: subgrid on the child container whose parent defines the column tracks — the parent .bento already uses CSS Grid; set the hero section as a grid with display: contents or restructure bento to use subgrid directly
- Health-summary strip badges use role=status (not role=alert) unless fail count changes from 0 to >0 on a live poll; on that transition use role=alert for screen reader announcement
- Quick-nav tiles use <a href=#pagename> elements so they work without JavaScript and are natively keyboard-focusable

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-068.1 (hash router must exist before nav tiles work)

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
