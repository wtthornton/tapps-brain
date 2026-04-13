# Story 68.1 -- Hash router and persistent side-nav shell

<!-- docsmcp:start:user-story -->

> **As a** brain-visual operator, **I want** to navigate between focused dashboard pages using a persistent side-nav or browser back/forward, **so that** I can jump directly to the Health scorecard, Retrieval stats, or Agents topology without scrolling a 3,000 px page

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that every other EPIC-068 story has a stable routing foundation to build on. Without a hash router and persistent nav, each page is an island — no deep linking, no keyboard nav between pages, no badge-count feedback loop. The nav shell is the single change that transforms brain-visual from a scroll document into an application.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Add a ~50-line vanilla JS hash router to index.html. The router listens on window hashchange, reads data-page attributes on section elements, and toggles the hidden attribute so only the active page is visible. A CSS side-nav (position: sticky, glass-2.0 backdrop-filter blur, NLT amber active indicator) renders on every page. Nav items carry data-nav attributes and receive aria-current=page when active. Amber badge counts (warn/fail totals from snapshot scorecard) sit on the Health nav item and update on every poll cycle. The nav collapses to icon-only at 768px and hamburger at 480px using CSS container queries. View Transitions API (document.startViewTransition) is called on each route change and gated behind prefers-reduced-motion: no-preference — instant fallback otherwise. The EPIC-065 poll timer must survive hashchange without being reset.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `examples/brain-visual/index.html`
- `examples/brain-visual/README.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Add data-page attribute to each of the 6 section elements (overview, health, memory, retrieval, agents, integrity) and hidden to all but overview (`examples/brain-visual/index.html`)
- [ ] Write router() function (~50 lines): reads location.hash, toggles hidden on data-page sections, sets aria-current on matching data-nav items, calls document.startViewTransition when API available and prefers-reduced-motion: no-preference (`examples/brain-visual/index.html`)
- [ ] Wire router() to window addEventListener hashchange and call router() on DOMContentLoaded (`examples/brain-visual/index.html`)
- [ ] Add CSS side-nav: position sticky, left column, NLT glass-2.0 surface (backdrop-filter blur, --surface token, gradient border using --nlt-gradient), amber left-border active indicator using [aria-current=page] (`examples/brain-visual/index.html`)
- [ ] Add CSS container queries for nav collapse: icon-only strip at 768px, hamburger toggle at 480px — no JS breakpoint polling (`examples/brain-visual/index.html`)
- [ ] Add .nav-badge CSS class and updateNavBadges(snapshot) JS function that computes warn/fail counts from scorecard data and updates badge text; wire into existing poll render path (`examples/brain-visual/index.html`)
- [ ] Add skip-to-main-content link as first focusable element; ensure Tab order: skip link → nav items → page content (`examples/brain-visual/index.html`)
- [ ] Add View Transitions CSS: @keyframes slide-in/slide-out using transform/opacity only; wrap in @media (prefers-reduced-motion: no-preference) (`examples/brain-visual/index.html`)
- [ ] Verify EPIC-065 poll timer (pollTimer — local to the initLivePolling IIFE at line ~3224 of index.html) is not cleared/reset on hashchange — router must not touch setInterval/clearInterval (`examples/brain-visual/index.html`)
- [ ] Document nav collapse breakpoints and View Transitions fallback in examples/brain-visual/README.md (`examples/brain-visual/README.md`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Navigating to #health shows only the Health section; #memory shows only Memory; all 6 hashes work correctly
- [ ] Browser back and forward buttons restore the previous page
- [ ] Active nav item has aria-current=page and visible amber left-border indicator
- [ ] Health nav item shows warn+fail badge count from the loaded snapshot; badge updates on next poll without full re-render
- [ ] At viewport width 768px nav collapses to icon-only (no label text); at 480px a hamburger toggle appears — implemented with CSS container queries only
- [ ] View Transitions API fires on route change when prefers-reduced-motion: no-preference; no transition when motion is disabled (instant hide/show)
- [ ] Poll timer continues firing at the expected interval after 5 hash navigations
- [ ] Skip-to-content link is the first Tab stop and moves focus to main content area
- [ ] All nav items are keyboard-reachable and activatable with Enter/Space
- [ ] No hardcoded amber hex values added outside CSS :root

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Hash router and persistent side-nav shell code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_navigating_health_shows_only_health_section_memory_shows_only_memory` -- Navigating to #health shows only the Health section; #memory shows only Memory; all 6 hashes work correctly
2. `test_ac2_browser_back_forward_buttons_restore_previous_page` -- Browser back and forward buttons restore the previous page
3. `test_ac3_active_nav_item_ariacurrentpage_visible_amber_leftborder_indicator` -- Active nav item has aria-current=page and visible amber left-border indicator
4. `test_ac4_health_nav_item_shows_warnfail_badge_count_from_loaded_snapshot_badge` -- Health nav item shows warn+fail badge count from the loaded snapshot; badge updates on next poll without full re-render
5. `test_ac5_at_viewport_width_768px_nav_collapses_icononly_no_label_text_at_480px` -- At viewport width 768px nav collapses to icon-only (no label text); at 480px a hamburger toggle appears — implemented with CSS container queries only
6. `test_ac6_view_transitions_api_fires_on_route_change_prefersreducedmotion` -- View Transitions API fires on route change when prefers-reduced-motion: no-preference; no transition when motion is disabled (instant hide/show)
7. `test_ac7_poll_timer_continues_firing_at_expected_interval_after_5_hash` -- Poll timer continues firing at the expected interval after 5 hash navigations
8. `test_ac8_skiptocontent_link_first_tab_stop_moves_focus_main_content_area` -- Skip-to-content link is the first Tab stop and moves focus to main content area
9. `test_ac9_all_nav_items_keyboardreachable_activatable_enterspace` -- All nav items are keyboard-reachable and activatable with Enter/Space
10. `test_ac10_no_hardcoded_amber_hex_values_added_outside_css_root` -- No hardcoded amber hex values added outside CSS :root

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Router is ~50 lines; if it grows beyond 80 lines extract to brain-visual-router.js but do NOT introduce a module bundler
- document.startViewTransition is called synchronously before DOM mutation so the browser captures the old state; DOM mutation happens inside the callback
- CSS container queries target the nav wrapper element — use @container nav-shell (max-width: 768px); define container-type: inline-size on the nav wrapper
- backdrop-filter: blur(8px) with --surface token as fallback background for browsers without backdrop-filter support
- View Transitions CSS ::view-transition-old/new pseudo-elements animate transform: translateX(-8px) and opacity only — no width/height/top/left

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- EPIC-064 (motion tokens and prefers-reduced-motion CSS pattern must be in place)
- EPIC-065 (poll timer architecture known before router touches event wiring)

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
