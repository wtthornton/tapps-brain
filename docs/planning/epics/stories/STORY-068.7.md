# Story 68.7 -- Integrity and Export page — checks, privacy tiers, export workflow

<!-- docsmcp:start:user-story -->

> **As a** brain-visual operator, **I want** a dedicated Integrity and Export page where I can review data consistency checks, select a privacy tier, and export in one focused workflow, **so that** I do not accidentally share a snapshot with sensitive tag names or tampered key details because the privacy selector was too small to notice

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the Integrity checks and Export workflow — currently buried at the bottom of a 3,000 px page — become a discoverable, purposeful destination for operators who need to verify data integrity before sharing a snapshot or archiving a store.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Move Integrity checks and Privacy/Export info to data-page=integrity. Add a visual privacy-tier selector (three tile buttons: Strict / Standard / Local with clear descriptions and amber active-state border). Add export format chooser (JSON / Markdown summary). Show snapshot schema version with a migration note if schema is older than current. Show integrity check results with timestamps (verified count, tampered count, no_hash count). Amplify the three-bullet privacy footer from the implementation plan (what is always excluded / what is aggregated / what local verbose adds).

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `examples/brain-visual/index.html`
- `examples/brain-visual/brain-visual-help.js`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Move Integrity section and Privacy/Export section markup to data-page=integrity section element (`examples/brain-visual/index.html`)
- [ ] Add visual privacy-tier selector: three .tile button elements (Strict / Standard / Local) with descriptions; active tier gets amber gradient border; wire click to update selectedPrivacyTier variable and re-render export button label (`examples/brain-visual/index.html`)
- [ ] Add export format chooser: two toggle buttons (JSON snapshot / Markdown summary); store selected format in module-level variable (`examples/brain-visual/index.html`)
- [ ] Add integrity results display: verified_count, tampered_count, no_hash_count tiles with timestamps; tampered_count tile uses amber warn styling when > 0 (`examples/brain-visual/index.html`)
- [ ] Add snapshot schema version display with migration note: if snapshot.schema_version < current expected version, show a warning pill explaining the snapshot is from an older schema (`examples/brain-visual/index.html`)
- [ ] Add amplified three-bullet privacy footer section per implementation plan: bullet 1 (what is always excluded: raw memory text, PII patterns), bullet 2 (what is aggregated: entry counts, tier distribution, retrieval mode), bullet 3 (what local verbose adds: tag names, memory group names, tampered key list) (`examples/brain-visual/index.html`)
- [ ] Add help pills for privacy tiers and integrity checks; add/update help articles in brain-visual-help.js for any new concepts (`examples/brain-visual/brain-visual-help.js`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Integrity page accessible at #integrity; integrity results and privacy section render from demo JSON
- [ ] Visual privacy-tier selector shows three tiles; active tile has amber gradient border; clicking changes the selected tier and updates export button label
- [ ] Integrity results show verified_count
- [ ] tampered_count
- [ ] no_hash_count from snapshot; tampered count tile shows amber warning styling when > 0
- [ ] Schema version display is present; migration warning appears when demo JSON has an older schema_version than expected current version
- [ ] Three-bullet privacy footer is present and matches the implementation plan wording (excluded / aggregated / local verbose)
- [ ] All privacy tier tiles and export buttons are keyboard-operable
- [ ] No Integrity or Privacy content remains in the Overview page section after this story

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Integrity and Export page — checks, privacy tiers, export workflow code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_integrity_page_accessible_at_integrity_integrity_results_privacy` -- Integrity page accessible at #integrity; integrity results and privacy section render from demo JSON
2. `test_ac2_visual_privacytier_selector_shows_three_tiles_active_tile_amber` -- Visual privacy-tier selector shows three tiles; active tile has amber gradient border; clicking changes the selected tier and updates export button label
3. `test_ac3_integrity_results_show_verifiedcount` -- Integrity results show verified_count
4. `test_ac4_tamperedcount` -- tampered_count
5. `test_ac5_nohashcount_from_snapshot_tampered_count_tile_shows_amber_warning` -- no_hash_count from snapshot; tampered count tile shows amber warning styling when > 0
6. `test_ac6_schema_version_display_present_migration_warning_appears_demo_json` -- Schema version display is present; migration warning appears when demo JSON has an older schema_version than expected current version
7. `test_ac7_threebullet_privacy_footer_present_matches_implementation_plan_wording` -- Three-bullet privacy footer is present and matches the implementation plan wording (excluded / aggregated / local verbose)
8. `test_ac8_all_privacy_tier_tiles_export_buttons_keyboardoperable` -- All privacy tier tiles and export buttons are keyboard-operable
9. `test_ac9_no_integrity_or_privacy_content_remains_overview_page_section_after` -- No Integrity or Privacy content remains in the Overview page section after this story

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Privacy tier selector is a visual UI element only — it does not re-fetch snapshot with a different privacy tier (that would require a CLI re-export); it changes the export button label and could in future trigger a re-fetch from /snapshot?privacy=strict when live polling is active
- tampered_count amber warning uses the existing warn CSS class from the scorecard system — do not introduce new status color tokens
- Schema version comparison: current expected version is 2 (as of Phase A); hardcode this as CURRENT_SCHEMA_VERSION constant at top of the script block with a comment to update when schema bumps

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-068.1 (router)

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
