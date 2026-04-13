# Story 68.8 -- Quality sweep — docs-mcp, tapps-mcp, Lighthouse, accessibility audit

<!-- docsmcp:start:user-story -->

> **As a** brain-visual developer, **I want** a structured quality gate that verifies accessibility, brand compliance, doc integrity, and cross-references before the epic is closed, **so that** EPIC-068 does not introduce accessibility regressions, orphan doc links, or hardcoded brand values that will drift from the NLT token source

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 2 | **Size:** S

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that EPIC-068 ships with verified accessibility scores, clean cross-references, no doc drift, and confirmed brand-token compliance — not as an afterthought audit but as a formal gate that prevents regressions from reaching users.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Run the full MCP quality matrix: docs-mcp docs_validate_epic on EPIC-068.md; docs_check_cross_refs on docs/ subtrees touched; docs_check_style on all new/changed markdown. Run tapps-mcp tapps_checklist with task_type: epic. Run Lighthouse Accessibility audit on the overview page (or equivalent static analysis); record score; fix any Critical issues; file follow-ups for non-critical issues. Manual keyboard nav audit: Tab through all six pages verifying focus order. Manual prefers-reduced-motion audit: enable OS-level reduce motion and verify all page transitions are instant. Scan index.html for any hardcoded amber hex values outside :root and replace with tokens.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `docs/planning/epics/EPIC-068.md`
- `examples/brain-visual/index.html`
- `examples/brain-visual/README.md`
- `docs/`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Run mcp__docs-mcp docs_validate_epic on docs/planning/epics/EPIC-068.md and resolve any validation errors (`docs/planning/epics/EPIC-068.md`)
- [ ] Run mcp__docs-mcp docs_check_cross_refs on docs/planning/epics/ and docs/design/nlt-brand/ subtrees; fix any broken links or missing cross-references introduced during EPIC-068 (`docs/planning/epics/`)
- [ ] Run mcp__docs-mcp docs_check_style on all .md files added or modified during EPIC-068; resolve style issues (`docs/`)
- [ ] Run mcp__tapps-mcp tapps_checklist with task_type: epic for EPIC-068; document results in EPIC-068.md quality section (`docs/planning/epics/EPIC-068.md`)
- [ ] Run Lighthouse Accessibility audit on examples/brain-visual/index.html (serve locally); record score in EPIC-068.md; fix any Critical (score impact > 5) issues before merge (`examples/brain-visual/index.html`)
- [ ] Manual keyboard audit: Tab through all six pages, verify focus order is nav-first then page-content; verify Enter/Space activates all interactive elements; document pass/fail per page (`examples/brain-visual/index.html`)
- [ ] Manual reduced-motion audit: enable OS reduce motion setting; navigate through all six pages; verify all transitions are instant (no slide, no fade); document results (`examples/brain-visual/index.html`)
- [ ] Grep index.html for hardcoded hex values (#d97706, #f59e0b, #b45309) outside the :root block; replace any found instances with the corresponding CSS custom property token (`examples/brain-visual/index.html`)
- [ ] Update examples/brain-visual/README.md with nav breakdown, reduced-motion test steps, and View Transitions browser support note (`examples/brain-visual/README.md`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] mcp__docs-mcp docs_validate_epic returns clean result for EPIC-068.md
- [ ] mcp__docs-mcp docs_check_cross_refs returns no broken links in touched doc subtrees
- [ ] mcp__docs-mcp docs_check_style returns no style errors on EPIC-068 markdown files
- [ ] mcp__tapps-mcp tapps_checklist passes for EPIC-068 (or failures are documented with follow-up issue IDs)
- [ ] Lighthouse Accessibility score ≥ 90 on examples/brain-visual/index.html overview page
- [ ] All Critical Lighthouse Accessibility issues (impact > 5) are fixed before epic close
- [ ] Keyboard audit: Tab order is correct on all six pages — no focus traps
- [ ] no unreachable interactive elements
- [ ] prefers-reduced-motion audit: all page transitions are instant when OS reduce motion is enabled
- [ ] No hardcoded amber hex values (#d97706
- [ ] #f59e0b
- [ ] #b45309) exist outside the CSS :root block in index.html
- [ ] README.md documents View Transitions browser support and reduced-motion fallback

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Quality sweep — docs-mcp, tapps-mcp, Lighthouse, accessibility audit code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_mcpdocsmcp_docsvalidateepic_returns_clean_result_epic068md` -- mcp__docs-mcp docs_validate_epic returns clean result for EPIC-068.md
2. `test_ac2_mcpdocsmcp_docscheckcrossrefs_returns_no_broken_links_touched_doc` -- mcp__docs-mcp docs_check_cross_refs returns no broken links in touched doc subtrees
3. `test_ac3_mcpdocsmcp_docscheckstyle_returns_no_style_errors_on_epic068_markdown` -- mcp__docs-mcp docs_check_style returns no style errors on EPIC-068 markdown files
4. `test_ac4_mcptappsmcp_tappschecklist_passes_epic068_or_failures_documented` -- mcp__tapps-mcp tapps_checklist passes for EPIC-068 (or failures are documented with follow-up issue IDs)
5. `test_ac5_lighthouse_accessibility_score_90_on_examplesbrainvisualindexhtml` -- Lighthouse Accessibility score ≥ 90 on examples/brain-visual/index.html overview page
6. `test_ac6_all_critical_lighthouse_accessibility_issues_impact_5_fixed_before_epic` -- All Critical Lighthouse Accessibility issues (impact > 5) are fixed before epic close
7. `test_ac7_keyboard_audit_tab_order_correct_on_all_six_pages_no_focus_traps` -- Keyboard audit: Tab order is correct on all six pages — no focus traps
8. `test_ac8_no_unreachable_interactive_elements` -- no unreachable interactive elements
9. `test_ac9_prefersreducedmotion_audit_all_page_transitions_instant_os_reduce` -- prefers-reduced-motion audit: all page transitions are instant when OS reduce motion is enabled
10. `test_ac10_no_hardcoded_amber_hex_values_d97706` -- No hardcoded amber hex values (#d97706
11. `test_ac11_f59e0b` -- #f59e0b
12. `test_ac12_b45309_exist_outside_css_root_block_indexhtml` -- #b45309) exist outside the CSS :root block in index.html
13. `test_ac13_readmemd_documents_view_transitions_browser_support_reducedmotion` -- README.md documents View Transitions browser support and reduced-motion fallback

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Lighthouse can be run via npx lighthouse http://localhost:8080 --only-categories=accessibility --output=json — no global install required; alternatively use Chrome DevTools Lighthouse panel
- tapps_checklist task_type: epic expects the epic to have all story links and acceptance criteria populated — ensure EPIC-068.md story stubs are linked before running
- docs_check_cross_refs should be run with the docs/planning/epics/ path and docs/design/nlt-brand/ path as minimum scope; expand to docs/ root if any guides were modified

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-068.1 through STORY-068.7 (all must be merged before quality sweep)

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
