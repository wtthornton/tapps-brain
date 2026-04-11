---
id: EPIC-064
title: "Product surface — narrative motion, deep insight, NLT brand fidelity"
status: done
priority: medium
created: 2026-04-10
tags: [product, ux, brain-visual, branding, motion, accessibility, marketing, tapps-mcp, docs-mcp]
depends_on: []
blocks: []
---

# EPIC-064: Product surface — narrative motion, deep insight, NLT brand fidelity

## Context

The **greenfield v3** epics created the same day ([EPIC-059](EPIC-059.md)–[EPIC-063](EPIC-063.md)) are infrastructure-heavy by design. The main **product-shaped** static surface in this repo is [`examples/brain-visual/index.html`](../../../examples/brain-visual/index.html): a capable operator dashboard that still reads as “telemetry dump” before it reads as **why tapps-brain matters**. README and docs index skew toward bullet lists rather than a guided story.

This epic raises the **experience bar**: scroll-linked narrative, meaningful motion (state explanation, not decoration), and **deep insight** panels that teach retrieval, diagnostics, and privacy boundaries—while staying honest (deterministic, aggregated snapshot contract per [`brain-visual-implementation-plan.md`](../brain-visual-implementation-plan.md)).

**Brand:** Code and comments reference **NLT Labs** tokens and `BRAND-STYLE-GUIDE.md`, but that guide is **not** in this repository today; only [`examples/brain-visual/nlt-an-mark-sm.svg`](../../../examples/brain-visual/nlt-an-mark-sm.svg) ships as a discrete logo asset. Work must **ingest the canonical style sheet and logo pack** from the approved NLT source (internal drive, design repo, or sibling checkout), then align the brain-visual surface and any token seeds in [`src/tapps_brain/visual_snapshot.py`](../../../src/tapps_brain/visual_snapshot.py).

**Sibling automation:** [**tapps-mcp**](https://github.com/tapps-mcp/tapps-mcp) (repo tooling: impact, dead code, quality gate, checklist, dependency graph, security scan) and [**docs-mcp**](https://github.com/tapps-mcp/tapps-mcp/tree/main/packages/docs-mcp) (documentation validation, cross-refs, style, drift) are configured for Cursor/Claude in [`.cursor/mcp.json`](../../../.cursor/mcp.json) and described in [`AGENTS.md`](../../../AGENTS.md). This epic **requires** both where applicable—not optional polish.

## MCP + web coverage (epic-wide)

Agents must use **three evidence channels**: in-repo contracts, **web** primary sources (W3C / MDN / web.dev), and the two MCP servers above.

| When | **docs-mcp** (invoke with repo paths) | **tapps-mcp** | **Web** |
|------|----------------------------------------|---------------|--------|
| Any story edits `docs/**/*.md` | `docs_check_cross_refs` on the smallest subtree that contains all edits; `docs_check_style` on new/changed guides or planning prose; if `DOCUMENTATION_INDEX.md` or engineering map changes, run `docs_check_drift`. | — | — |
| Epic close (**064.CLEAN**) | `docs_validate_epic` on `EPIC-064.md`; `docs_check_cross_refs` on `docs/guides/`, `docs/planning/` as touched; `docs_check_style` on all new/changed markdown under those trees. | `tapps_checklist` with `task_type: "epic"`. If **any** story touched Python under `src/tapps_brain/`: run `tapps_impact_analysis` per changed file; `tapps_quality_gate` on `visual_snapshot.py` (or other hot modules) if modified; `tapps_dead_code` on `src/tapps_brain/` if exports or snapshot code moved; `tapps_dependency_graph` if imports changed. | Extend **Research notes** table with any new technique (one primary URL per row). |
| Security-sensitive doc (e.g. privacy tiers, export paths) | Same as docs row; ensure links to `visual-snapshot.md` / privacy guides remain valid. | Optional `tapps_security_scan` if new SQL or migration snippets ship alongside this epic (unlikely for pure HTML—document “N/A” if skipped). | [WCAG Understanding](https://www.w3.org/WAI/WCAG22/Understanding/) as needed |

**Cursor naming:** tasks in `.ralph/fix_plan.md` use the `mcp__tapps-mcp <tool>` / `mcp__docs-mcp <tool>` invocation style (same as EPIC-059–063 CLEAN tasks).

## Research notes (2026 — for implementation)

Use these as **constraints and citations**, not as a license to add heavy JS frameworks.

| Topic | Takeaway | References |
|-------|-----------|------------|
| **Motion & vestibular safety** | Non-essential motion triggered by scroll/hover must respect user preference; prefer **static-first** + `@media (prefers-reduced-motion: no-preference)` to add motion (W3C **C39**). Parallax and scroll-bound motion are called out as common triggers. | [WCAG 2.2 Understanding 2.3.3 Animation from Interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html) (AAA); [Technique C39: `prefers-reduced-motion`](https://www.w3.org/WAI/WCAG22/Techniques/css/C39.html) |
| **Auto motion** | Long-running or auto-updating motion needs pause/stop/hide per **2.2.2** when parallel with other content. | [Understanding 2.2.2 Pause, Stop, Hide](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html) |
| **Performance** | Animate **transform** and **opacity** on compositor; avoid width/height/top/left animation on large trees; prefer CSS **scroll-driven animations** with **Intersection Observer** fallback where timeline support is incomplete. | [MDN — Using scroll-driven animations](https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_scroll-driven_animations/Using_scroll-driven_animations); [MDN — `prefers-reduced-motion`](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion); align with repo plan § “2026 forward-thinking principles” in [`brain-visual-implementation-plan.md`](../brain-visual-implementation-plan.md) |
| **Brand handoff** | Mature orgs ship **design tokens** as versioned packages or CSS variable bundles (Style Dictionary–style pipelines) so apps import one artifact; logo packs stay **versioned** with clear space/clearance rules. | Examples: [Palmetto design-tokens](https://github.com/palmetto/palmetto-design-tokens), [Sage design-tokens](https://github.com/Sage/design-tokens), [Red Hat RHDS tokens](https://github.com/RedHat-UX/red-hat-design-tokens) — patterns only, not product endorsement |
| **Web platform docs** | Prefer **MDN** + **web.dev** for implementation details; W3C for **accessibility normative understanding** documents. | [web.dev — prefers-reduced-motion](https://web.dev/articles/prefers-reduced-motion); [web.dev — animations](https://web.dev/learn/css/animations/) (conceptual); MDN links in row above |

**In-repo alignment:** The implementation plan already sets a **WCAG 2.2 AA** floor, **`prefers-reduced-motion`**, optional **View Transitions** for state changes, and **decision-first** dashboard copy—EPIC-064 executes and extends that bar.

## Success criteria

- [ ] Canonical **NLT Labs** style sheet and **logo pack** are located, version-referenced, and a **gap matrix** (guide § → CSS variable / component / Python token) is recorded for this repo’s surfaces.
- [ ] `examples/brain-visual/` presents a **clear product narrative** above the fold and at least one **deep insight** section (e.g. composite score + retrieval pipeline explained with real field names, not generic “AI” language).
- [ ] Motion is **purposeful**, **degrades gracefully**, and **honors `prefers-reduced-motion`** across new interactions.
- [ ] First-run experience works with a **checked-in demo snapshot** (synthetic or fixture) so the page “sells” before the user hunts for JSON.
- [ ] README or [`docs/DOCUMENTATION_INDEX.md`](../../DOCUMENTATION_INDEX.md) links the visual demo with a short **why open this** blurb.
- [ ] **docs-mcp:** All merged markdown in `docs/` for this epic passes `docs_check_cross_refs` (scoped subtree), `docs_check_style` where prose changed, and epic close runs `docs_validate_epic` on this file; `docs_check_drift` if the doc index or engineering map changes.
- [ ] **tapps-mcp:** Epic close runs `tapps_checklist` (`task_type: "epic"`). Any Python changes under `src/tapps_brain/` also run `tapps_impact_analysis` on touched files and `tapps_quality_gate` on modified modules (especially `visual_snapshot.py`); `tapps_dead_code` + `tapps_dependency_graph` when imports or public surface change.
- [ ] **Web:** Research notes table (and motion implementation) cite at least one **W3C Understanding** or **Technique** doc plus **MDN** or **web.dev** for motion implementation—no orphan “blog-only” patterns without a standards anchor.

## Stories

### STORY-064.1: NLT Labs — style sheet & logo pack audit (source of truth)

**Status:** planned  
**Effort:** M  
**Depends on:** none  
**Context refs:** `examples/brain-visual/index.html`, `examples/brain-visual/nlt-an-mark-sm.svg`, `src/tapps_brain/visual_snapshot.py` (`VisualThemeTokens`), internal or sibling **`BRAND-STYLE-GUIDE.md`** (locate off-repo if needed)  
**Verification:** Doc-only: deliverable is a markdown artifact under `docs/planning/` or `docs/design/` with checklist tables. Run `mcp__docs-mcp docs_check_cross_refs` on the subtree that contains the new/edited files; run `mcp__docs-mcp docs_check_style` on the new markdown.

#### Why

Without anchoring to the **approved** palette, typography, spacing, logo clearance, and wordmark rules, frontend work drifts into “looks fine” instead of **on-brand**.

#### Acceptance criteria

- [ ] **Locate** the canonical NLT Labs **style sheet** (the referenced `BRAND-STYLE-GUIDE.md` or successor) and **logo pack** (SVG/PNG master, monochrome, wordmark combinations, minimum sizes, exclusion zone).
- [ ] Produce a **gap matrix**: each guide section (e.g. color §, typography §, motion §, logo §) → current implementation in `index.html` / `nlt-an-mark-sm.svg` / `VisualThemeTokens` → gap or match.
- [ ] If policy allows copying a **redistributable subset** into the repo (e.g. `docs/design/nlt-brand/README.md` + approved SVGs only), add it and link from `examples/brain-visual/README.md`; if not, document **exact fetch path** (URL or internal path pattern) maintainers must use—no broken assumptions.

---

### STORY-064.2: Narrative & information architecture refresh

**Status:** planned  
**Effort:** M  
**Depends on:** STORY-064.1  
**Context refs:** `docs/planning/brain-visual-implementation-plan.md` (“Marketing & UX narrative”, story beats), `examples/brain-visual/index.html`  
**Verification:** Peer review + manual read-through. Run `mcp__docs-mcp docs_check_style` and `mcp__docs-mcp docs_check_cross_refs` on `docs/planning/` and `docs/guides/` subtrees touched by copy or links.

#### Why

Operators need a **decision-first** story before dense charts (see implementation plan digest).

#### Acceptance criteria

- [ ] Above-the-fold copy answers: **what this is**, **who it’s for**, **what happens if you do nothing else** (load demo / load export).
- [ ] Section order follows an agreed **story beat** list (trust → pulse → retrieval reality → …) with anchors; stale or redundant blocks merged.
- [ ] Microcopy avoids vague “RAG enabled” hype unless tooltips tie to **real** `retrieval_effective_mode` strings.

---

### STORY-064.3: Motion system (tokens, reduced motion, performance)

**Status:** planned  
**Effort:** L  
**Depends on:** STORY-064.1, STORY-064.2  
**Context refs:** `examples/brain-visual/index.html` (existing `prefers-reduced-motion` partial coverage)  
**Verification:** Manual: OS “reduce motion” on → no parallax/reveal jank; DevTools performance sanity; document test steps in `examples/brain-visual/README.md`. If `visual-snapshot.md` or planning docs change for motion notes, run `mcp__docs-mcp docs_check_cross_refs` on those paths.

#### Why

Motion should **explain state** (snapshot load, section entry) and meet **WCAG 2.3.3** intent for interaction-driven animation via user preference.

#### Acceptance criteria

- [ ] Define **duration/easing tokens** (CSS custom properties) consistent with NLT guide after 064.1.
- [ ] New scroll or enter-view effects use **transform/opacity** only; gated behind `prefers-reduced-motion: no-preference` with instant final state otherwise.
- [ ] Drawer/modals already animated: audit for **consistent** reduced-motion behavior (no mixed “half on” states).

---

### STORY-064.4: “Deep insight” panels — retrieval, diagnostics, privacy

**Status:** planned  
**Effort:** L  
**Depends on:** STORY-064.2  
**Context refs:** `src/tapps_brain/visual_snapshot.py`, `docs/guides/visual-snapshot.md`, `examples/brain-visual/brain-visual-help.js`  
**Verification:** Manual: with demo JSON, each panel shows accurate strings; help articles updated if new concepts added. Run `mcp__docs-mcp docs_check_cross_refs` on `docs/guides/visual-snapshot.md` if it references new UI behavior. If `visual_snapshot.py` or schema changes: `mcp__tapps-mcp tapps_impact_analysis` on each touched file and `mcp__tapps-mcp tapps_quality_gate` on `visual_snapshot.py`.

#### Why

The product differentiates on **deterministic** retrieval, decay, and safety—not on generic charts.

#### Acceptance criteria

- [ ] At least one **interactive or stepped explainer** (CSS/JS, no new npm dependency unless justified) for **composite score + circuit** or **retrieval stack** (BM25 / hybrid / sqlite-vec) using snapshot fields.
- [ ] Privacy section amplified per implementation plan **footer** pattern (three bullets: excluded / aggregated / local).
- [ ] Help `?` entries exist for any new concepts (reuse `brain-visual-help.js` patterns).

---

### STORY-064.5: Demo snapshot + first-run empty state

**Status:** planned  
**Effort:** S  
**Depends on:** STORY-064.4 (for coherent numbers)  
**Context refs:** `examples/brain-visual/`, `tests/unit/test_visual_snapshot.py` (fixture shapes)  
**Verification:** Serving folder over HTTP shows populated UI without user-provided file; `pytest tests/unit/test_visual_snapshot.py -q` still green if Python touched. If Python touched: `mcp__tapps-mcp tapps_impact_analysis` per file + `mcp__tapps-mcp tapps_checklist` with `task_type: "epic"` (or story-sized checklist if tool supports it).

#### Why

An empty dashboard **does not sell**; a tasteful fixture proves the narrative.

#### Acceptance criteria

- [ ] Add `examples/brain-visual/brain-visual.demo.json` (or name per README) with **synthetic** data only; document that it is not a real store export.
- [ ] `fetch()` prefers demo when `brain-visual.json` missing, or explicit **“Load demo”** control—product decision documented in README.

---

### STORY-064.6: README / docs index — CTA to the visual

**Status:** planned  
**Effort:** S  
**Depends on:** STORY-064.5  
**Context refs:** `README.md`, `docs/DOCUMENTATION_INDEX.md`, `docs/guides/visual-snapshot.md`  
**Verification:** `mcp__docs-mcp docs_check_cross_refs` on touched files; `mcp__docs-mcp docs_check_style` on edited markdown. If `DOCUMENTATION_INDEX.md` changes: `mcp__docs-mcp docs_check_drift`.

#### Why

Discovery is part of the product; GitHub visitors should see the visual story linked early.

#### Acceptance criteria

- [ ] README has a short **“See it”** subsection with link to `examples/brain-visual/` and one sentence on **privacy-safe** aggregates.
- [ ] Documentation index or visual guide cross-links the demo behavior.

---

### STORY-064.CLEAN: Quality sweep

**Status:** planned  
**Effort:** S  
**Depends on:** STORY-064.3–064.6  
**Context refs:** `docs/planning/epics/EPIC-064.md`, [`.cursor/mcp.json`](../../../.cursor/mcp.json), [`AGENTS.md`](../../../AGENTS.md)  
**Verification:** Full MCP matrix below + Lighthouse note.

#### Why

Ship confidence: a11y regression on static HTML is easy to miss without an explicit gate; doc drift and Python blast radius are caught by **docs-mcp** and **tapps-mcp** respectively.

#### Acceptance criteria

- [ ] **docs-mcp:** `docs_validate_epic` on `EPIC-064.md`; `docs_check_cross_refs` on `docs/guides/` and `docs/planning/` (full subtrees if any file in them changed during the epic; else scoped to touched paths); `docs_check_style` on all epic-touched markdown under `docs/`; `docs_check_drift` if `DOCUMENTATION_INDEX.md` or engineering overview files changed.
- [ ] **tapps-mcp:** `tapps_checklist` with `task_type: "epic"`. If `src/tapps_brain/visual_snapshot.py` (or other `src/tapps_brain/` modules) changed in 064.4–064.5: `tapps_impact_analysis` on each changed file; `tapps_quality_gate` on `visual_snapshot.py`; if imports/public API changed, `tapps_dependency_graph` and `tapps_dead_code` on `src/tapps_brain/`. Document “N/A — no Python changes” otherwise.
- [ ] **Web:** Confirm Research notes table still includes ≥1 W3C WCAG link and ≥1 MDN or web.dev link for motion (update row if implementation diverged).
- [ ] Lighthouse (or equivalent) **Accessibility** score recorded; critical issues fixed or filed as follow-ups with IDs.

## Priority order

| Order | Story | Rationale |
|------:|-------|-----------|
| 1 | 064.1 | Brand source of truth before pixels move |
| 2 | 064.2 | IA and copy drive layout |
| 3 | 064.3 | Motion layered on stable structure |
| 4 | 064.4 | Insight content uses IA |
| 5 | 064.5 | Demo supports narrative |
| 6 | 064.6 | Discovery |
| 7 | 064.CLEAN | Gate |
