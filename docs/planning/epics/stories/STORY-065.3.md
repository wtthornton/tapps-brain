# Story 65.3 -- Purge stale and privacy-gated components

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** every panel in the dashboard to show real, always-available data, **so that** I am not confused by permanently-empty sections, demo contributor data, or decorative diagrams that imply pipeline activity I cannot verify

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the dashboard surface is honest. Three categories of components currently mislead operators: sections that are permanently hidden in Docker deployments because they require privacy=local (Tags, Memory Groups), a decorative retrieval pipeline step-flow diagram that lights up nodes based on a static mode string rather than actual pipeline execution, and the shipped brain-visual.json demo file that displays a contributor's real Hive data (1176 entries, 20 agents, Windows store path) as if it were the operator's own system. Removing these eliminates confusion and makes room for the live panels added in other stories.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Remove the Tags section and Memory Groups section from index.html entirely — they are gated behind privacy_tier == local which is never set in a Docker/Hive deployment. Remove the static retrieval pipeline step-flow SVG diagram (5-node flow with decorative arrows); it will be replaced by the live metrics panel in STORY-065.7. Remove scorecard-derive.js — it was a client-side fallback for v1 snapshots; v2 is now the only supported schema. Replace the privacy footer static text block with a single dynamic privacy-tier badge. Commit an empty brain-visual.json (schema stub with no entries, no hive data, generated_at epoch) so the repo ships with a clearly-empty placeholder rather than real contributor telemetry.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `examples/brain-visual/index.html`
- `examples/brain-visual/scorecard-derive.js`
- `examples/brain-visual/brain-visual.json`
- `examples/brain-visual/brain-visual-help.js`

> Note (2026-04-13): `brain-visual.demo.json` was deleted when the dashboard went live-only.

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Delete Tags section HTML block from index.html (section#tags and its children) (`examples/brain-visual/index.html`)
- [ ] Delete Memory Groups section HTML block from index.html (section#groups and its children) (`examples/brain-visual/index.html`)
- [ ] Remove the retrieval pipeline step-flow diagram HTML block (#retrieval-insight-panel with rstep-* nodes and arrows) — leave #retrieval-mode tile and mode description text (`examples/brain-visual/index.html`)
- [ ] Remove section anchor nav links for #tags and #groups from the nav bar (`examples/brain-visual/index.html`)
- [ ] Remove all JS that populates #tags-body and #groups-body (renderTags, renderGroups functions and their calls) (`examples/brain-visual/index.html`)
- [ ] Remove scorecard-derive.js script tag and delete the file (`examples/brain-visual/scorecard-derive.js`)
- [ ] Replace privacy footer static text block with a single <span id='privacy-tier-badge'> populated from snapshot.privacy_tier (`examples/brain-visual/index.html`)
- [ ] Replace brain-visual.json with an empty stub: schema_version=2, generated_at=epoch, zero entries, hive_attached=false, empty scorecard (`examples/brain-visual/brain-visual.json`)
- [ ] Remove Tags and Memory Groups help entries from brain-visual-help.js or repurpose them as archive comments (`examples/brain-visual/brain-visual-help.js`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] No Tags or Memory Groups sections appear in the rendered dashboard HTML
- [ ] scorecard-derive.js is deleted from the repository
- [ ] Retrieval pipeline step-flow diagram SVG is removed — #retrieval-mode tile text remains
- [ ] Section nav contains no links to #tags or #groups
- [ ] brain-visual.json in the repo has entry_count=0 and hive_health.connected=false
- [ ] Privacy footer is a single badge element populated dynamically from snapshot.privacy_tier
- [ ] No JavaScript references to renderTags
- [ ] renderGroups
- [ ] or scorecard-derive remain
- [ ] Dashboard loads without JS errors after removal

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Purge stale and privacy-gated components code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. HTML parse: no element with id matching tags-body
2. groups-body
3. rstep-query
4. rstep-bm25
5. rstep-vec
6. rstep-rrf
7. rstep-result
8. File existence: scorecard-derive.js does not exist
9. JSON parse: brain-visual.json health.entry_count == 0 and hive_health.connected == false
10. JS runtime: no console errors on load in headless Chromium (playwright smoke test)

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- grep for 'tags-body'
- 'groups-body'
- 'scorecard-derive'
- 'rstep-' before closing PR to confirm complete removal
- The retrieval mode tile (#retrieval-mode
- #retrieval-mode-desc
- #sqlite-vec-line) stays — only the step-flow diagram sub-panel is removed
- **Superseded 2026-04-13:** `brain-visual.demo.json` and the Load-demo button were deleted when the dashboard went live-only

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- List stories or external dependencies that must complete first...

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [x] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
