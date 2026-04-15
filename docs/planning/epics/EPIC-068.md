# Epic 68: Multi-page brain-visual dashboard — hash-routed navigation

<!-- docsmcp:start:metadata -->
**Status:** In Progress
**Priority:** P1 - High
**Estimated LOE:** ~3 weeks (1 developer)
**Dependencies:** EPIC-064 (brand tokens, motion system, IA foundation — **done**), EPIC-065 (live /snapshot endpoint — required only for live-polling ACs in 068.1/068.3; all other stories proceed with demo JSON via `python3 -m http.server 8090` in `examples/brain-visual/`)

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that operators, team leads, and NLT storytellers can navigate the brain-visual dashboard as a purposeful multi-page application — arriving at exactly the view they need (health triage, memory audit, retrieval tuning, agent topology) without scrolling a 3,000 px monolith. Each page is deep-linkable, keyboard-navigable, and brand-aligned with the NLT Labs amber palette, Fraunces/Inter/JetBrains Mono type stack, and 2026 decision-first, glass-2.0, WCAG 2.2 AA design principles.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Refactor examples/brain-visual/index.html from a single-scroll dashboard into a six-page hash-routed application (Overview, Health, Memory, Retrieval, Agents & Hive, Integrity & Export) with a persistent side-nav, deep-linkable URLs (#overview, #health, #memory, #retrieval, #agents, #integrity), nav-badge fail counts, and View Transitions API state changes — using zero new npm dependencies, ~50 lines of vanilla JS router, and full NLT brand fidelity.

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

The current dashboard buries actionable content: a developer debugging retrieval latency must scroll past Hive Hub noise; an operator triaging a FAIL alert cannot deep-link to the scorecard; agent topology is invisible in two disconnected tables. As data model scope grows (more agents, memory groups, retrieval metrics) the single-page approach produces scroll fatigue and dilutes the product narrative. The 2026 design bar (bento grids, glass surfaces, View Transitions, decision-first copy) requires page-level hierarchy to land correctly. Navigation is also the prerequisite for every future dashboard feature — without it, new panels have nowhere to live except appended to the bottom.

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Hash router (~50 lines vanilla JS) drives all six pages via window.hashchange with correct aria-current on nav items and hidden attribute toggling on page sections
- [ ] Persistent side-nav is visible on all pages with amber active-page indicator (left border
- [ ] NLT accent primary #d97706) and animated badge counts for warn/fail items
- [ ] Nav collapses to icon-only strip at max-width 768px and hamburger at max-width 480px using CSS container queries — no JavaScript breakpoint polling
- [ ] All six pages are deep-linkable (browser back/forward works; reload restores the correct page)
- [ ] View Transitions API (with prefers-reduced-motion: no-preference gate) animates page switches using transform/opacity only — instant fallback when motion is disabled
- [ ] Nav badge counts reflect live snapshot warn/fail totals and update on every poll cycle without full re-render
- [ ] Each page section presents decision-first content: the primary question it answers is answered above the fold of that page
- [ ] No new npm dependencies added; no build step introduced; vanilla HTML/CSS/JS only
- [ ] All new markup and CSS uses NLT brand tokens (--nlt-accent-primary
- [ ] --nlt-accent-secondary
- [ ] --nlt-accent-dim
- [ ] --nlt-gradient) — no hardcoded amber hex values outside :root
- [ ] WCAG 2.2 AA: focus-visible outlines on all interactive nav items; keyboard Tab order navigates through nav then into page content; skip-to-content link present
- [ ] Lighthouse Accessibility score ≥ 90 on the final page
- [ ] docs-mcp docs_validate_epic passes on EPIC-068.md at close; docs_check_cross_refs clean on docs/ subtrees touched; tapps-mcp tapps_checklist (task_type: epic) passes

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

### 68.1 -- Hash router and persistent side-nav shell

**Points:** 5

50-line vanilla JS hash router + CSS side-nav with amber active indicator, nav-badge counts, responsive collapse, and View Transitions API gate

**Tasks:**
- [ ] Implement hash router and persistent side-nav shell
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Hash router and persistent side-nav shell is implemented, tests pass, and documentation is updated.

---

### 68.2 -- Overview page — decision strip and health summary

**Points:** 3

Refactor current hero bento into Overview page; add aggregated health-summary strip with clickable badges linking to #health

**Tasks:**
- [ ] Implement overview page — decision strip and health summary
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Overview page — decision strip and health summary is implemented, tests pass, and documentation is updated.

---

### 68.3 -- Health page — scorecard with filter bar and issue workflow

**Points:** 5

Dedicated #health page: scorecard with All/Fail/Warn/Pass filter bar, severity sort toggle, history-diff marker (live polling), amplified notes + GitHub export workflow

**Tasks:**
- [ ] Implement health page — scorecard with filter bar and issue workflow
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Health page — scorecard with filter bar and issue workflow is implemented, tests pass, and documentation is updated.

---

### 68.4 -- Memory page — pulse, groups, tags, histograms

**Points:** 3

Consolidate Pulse + Memory Groups + Tag Cloud + Access Histograms onto #memory page with 2-column subgrid layout and expanded chart heights

**Tasks:**
- [ ] Implement memory page — pulse, groups, tags, histograms
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Memory page — pulse, groups, tags, histograms is implemented, tests pass, and documentation is updated.

---

### 68.5 -- Retrieval page — mode, latency histogram, vector stats

**Points:** 3

Dedicated #retrieval page: config panel (BM25/hybrid/vector), query stats, P50/P95/P99 latency callouts, vector index details

**Tasks:**
- [ ] Implement retrieval page — mode, latency histogram, vector stats
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Retrieval page — mode, latency histogram, vector stats is implemented, tests pass, and documentation is updated.

---

### 68.6 -- Agents and Hive page — topology SVG and registry

**Points:** 5

Dedicated #agents page: SVG topology diagram (agent→namespace→hive), agent detail slide-in drawer, online/offline prominence, namespace table

**Tasks:**
- [ ] Implement agents and hive page — topology svg and registry
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Agents and Hive page — topology SVG and registry is implemented, tests pass, and documentation is updated.

---

### 68.7 -- Integrity and Export page — integrity checks, privacy tiers, export

**Points:** 3

Dedicated #integrity page: integrity check results with timestamps, visual privacy-tier selector, export format chooser, snapshot schema version + migration notes

**Tasks:**
- [ ] Implement integrity and export page — integrity checks, privacy tiers, export
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Integrity and Export page — integrity checks, privacy tiers, export is implemented, tests pass, and documentation is updated.

---

### 68.8 -- Quality sweep

**Points:** 2

docs-mcp validate_epic + check_cross_refs + check_style; tapps-mcp tapps_checklist; Lighthouse Accessibility ≥ 90; reduced-motion manual audit; keyboard nav audit

**Tasks:**
- [ ] Implement quality sweep
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Quality sweep is implemented, tests pass, and documentation is updated.

---

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Zero new npm dependencies — hash router is ~50 lines of vanilla JS using window.addEventListener hashchange + data-page/data-nav attributes
- Nav uses CSS position: sticky with backdrop-filter blur for glass-2.0 surface; card bodies stay solid (WCAG contrast); dark theme --bg #0a0e13 as equal citizen
- View Transitions API gated behind @media (prefers-reduced-motion: no-preference) — instant fallback; animate transform/opacity only per EPIC-064 motion policy
- Responsive nav uses CSS container queries (not JS breakpoint polling) to collapse at 768px and 480px
- Badge counts derived from snapshot scorecard data — no separate API call; updated in the existing poll cycle
- SVG topology diagram (068.6) is client-side generated from snapshot JSON; no external graph library; nodes capped at 50 for mid-tier devices
- All CSS additions must use existing NLT brand tokens from :root — no hardcoded hex values outside :root
- Each new page section uses dash-section + scroll-margin-top pattern established in EPIC-064 so existing help-drawer and anchor behaviour is preserved
- EPIC-065 live polling must continue to function across page switches — the poll timer must not be destroyed on hashchange

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- No migration to React
- Vue
- or any JS framework — vanilla HTML/CSS/JS is a design constraint not a gap
- No WebGPU or 3D visualisation — those belong to a later Phase 3 per brain-visual-implementation-plan.md
- No server-side routing — this is a static file; all routing is client-side hash-based
- No real-time WebSocket — live polling (EPIC-065 /snapshot endpoint) is the transport; SSE/WS is Phase D
- No per-page bundle splitting or build pipeline
- No A/B snapshot diff feature — listed as deferred in Phase B of implementation plan

<!-- docsmcp:end:non-goals -->

<!-- docsmcp:start:success-metrics -->
## Success Metrics

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| All 6 pages navigable via URL hash with browser back/forward working | - | - | - |
| Nav badge count reflects live fail/warn totals within one poll cycle | - | - | - |
| Lighthouse Accessibility ≥ 90 on overview page | - | - | - |
| Zero hardcoded amber hex values outside CSS :root | - | - | - |
| prefers-reduced-motion: all page transitions instant when motion disabled | - | - | - |

<!-- docsmcp:end:success-metrics -->

<!-- docsmcp:start:stakeholders -->
## Stakeholders

| Role | Person | Responsibility |
|------|--------|----------------|
| Solo developers and operators (primary dashboard users) | - | - |
| NLT Labs brand/product team (brand fidelity) | - | - |
| team leads (cross-machine fingerprint comparison) | - | - |

<!-- docsmcp:end:stakeholders -->

<!-- docsmcp:start:references -->
## References

- EPIC-064 (brand tokens + motion system)
- EPIC-065 (/snapshot live endpoint)
- docs/design/nlt-brand/README.md (gap matrix)
- docs/planning/brain-visual-implementation-plan.md (2026 principles)
- WCAG 2.2 Understanding 2.3.3 Animation from Interactions
- View Transitions API — MDN

<!-- docsmcp:end:references -->

<!-- docsmcp:start:implementation-order -->
## Implementation Order

1. Story 68.1: Hash router and persistent side-nav shell
2. Story 68.2: Overview page — decision strip and health summary
3. Story 68.3: Health page — scorecard with filter bar and issue workflow
4. Story 68.4: Memory page — pulse, groups, tags, histograms
5. Story 68.5: Retrieval page — mode, latency histogram, vector stats
6. Story 68.6: Agents and Hive page — topology SVG and registry
7. Story 68.7: Integrity and Export page — integrity checks, privacy tiers, export
8. Story 68.8: Quality sweep

<!-- docsmcp:end:implementation-order -->

<!-- docsmcp:start:risk-assessment -->
## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| View Transitions API browser support (Chrome 111+ | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| Firefox 130+ | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| Safari 18+) — fallback is instant transition | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| document in README | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| SVG topology for large Hive deployments (50+ agents) — cap at 50 nodes with truncation indicator; document limit | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| Brand token gap: motion duration/easing tokens were flagged as missing in EPIC-064.1 gap matrix — 068.3 depends on them being defined; if 064.3 is not yet done | Medium | High | Warning: Mitigation required - no automated recommendation available |
| define tokens inline and mark for consolidation | Medium | Medium | Warning: Mitigation required - no automated recommendation available |

<!-- docsmcp:end:risk-assessment -->

<!-- docsmcp:start:files-affected -->
## Files Affected

| File | Story | Action |
|---|---|---|
| Files will be determined during story refinement | - | - |

<!-- docsmcp:end:files-affected -->

<!-- docsmcp:start:performance-targets -->
## Performance Targets

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| Test coverage | baseline | >= 80% | pytest --cov |
| Acceptance criteria pass rate | 0% | 100% | CI pipeline |
| Story completion rate | 0% | 100% | Sprint tracking |

<!-- docsmcp:end:performance-targets -->
