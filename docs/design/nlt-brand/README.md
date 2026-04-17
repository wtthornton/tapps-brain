---
title: "NLT Labs — Brand Style Sheet & Logo Pack Audit"
description: "Gap matrix between the canonical NLT Labs brand guide and current brain-visual implementation. Source of truth for brand-aligned work in EPIC-064."
created: 2026-04-11
epic: EPIC-064
story: STORY-064.1
---

# NLT Labs — Brand Style Sheet & Logo Pack Audit

## Status

| Item | Location |
|------|----------|
| Canonical `BRAND-STYLE-GUIDE.md` | **Not in this repository** — internal NLT Labs asset (see [§ Fetch path](#canonical-guide-fetch-path)) |
| Logo pack (master SVG/PNG, monochrome, wordmark) | **Not in this repository** — only the compact `AN` mark ships here |
| Redistributable subset in repo | `examples/brain-visual/nlt-an-mark-sm.svg` (compact mark only) |
| Token references in code | `src/tapps_brain/visual_snapshot.py` (`VisualThemeTokens`) |
| CSS token definitions | `examples/brain-visual/index.html` `:root` (lines 17–49) |

---

## Canonical Guide Fetch Path

The full NLT Labs brand guide is maintained as an **internal NLT Labs asset** — not redistributed in this open-source repository. Maintainers doing brand-aligned frontend work must:

1. Obtain the current `BRAND-STYLE-GUIDE.md` (or successor) from the internal NLT Labs design repository or shared drive.
2. Verify the version date matches the tokens recorded in the gap matrix below.
3. Do **not** commit the full guide or unreleased logo variants to this repo. Only commit assets explicitly cleared for open-source distribution (e.g. the compact `AN` mark already at `examples/brain-visual/nlt-an-mark-sm.svg`).

When a new guide version ships, update the **Gap matrix** section below and re-run `mcp__docs-mcp docs_check_cross_refs` on `docs/design/nlt-brand/`.

---

## Logo Pack — As-Is in Repository

| Asset | Path | Format | Notes |
|-------|------|--------|-------|
| Compact `AN` mark (40 × 40 px use size) | `examples/brain-visual/nlt-an-mark-sm.svg` | SVG, 128 × 128 viewBox | Amber gradient `#d97706 → #f59e0b`, 135 ° diagonal |
| Full wordmark (primary) | **missing** — not in repo | — | Required for full brand compliance |
| Monochrome / reversed mark | **missing** — not in repo | — | Required for dark backgrounds, print |
| Clearance / exclusion zone rules | **missing** — not in guide text | — | Minimum clear space around mark undefined in-repo |
| Minimum size rules | **missing** — not in guide text | — | Smallest legible render size undefined in-repo |

The SVG compact mark uses gradient ID `nlt-an-grad-sm`. If the logo pack ships a canonical gradient definition, align IDs.

---

## Gap Matrix

Each row is one canonical brand guide section mapped to the current implementation. Status:
- ✅ **Match** — implementation aligns with what the brand guide is expected to specify
- ⚠️ **Partial** — some tokens present but completeness cannot be verified without the canonical guide
- ❌ **Gap** — section present in a typical brand guide but absent or unverifiable in this repo

### § Color

| Brand guide section | Expected spec | Current implementation | Status |
|--------------------|---------------|------------------------|--------|
| Primary accent | Amber-600 hex + Tailwind alias | `--nlt-accent-primary: #d97706` (amber-600) | ✅ |
| Secondary accent | Amber-400 hex + Tailwind alias | `--nlt-accent-secondary: #f59e0b` (amber-400) | ✅ |
| Dim / hover state | Amber-800 hex | `--nlt-accent-dim: #b45309` (amber-800) | ✅ |
| Gradient definition | Direction + stops | `linear-gradient(135deg, #d97706, #f59e0b)` | ✅ |
| No-cyan / no-blue rule | Explicit exclusion of cool hues | `VisualThemeTokens` constrains hue to 28–48 ° amber wedge | ✅ |
| Focus ring colour | Accessible amber | `--nlt-focus-ring: #f59e0b` (3:1 + context) | ✅ |
| Background neutral (light) | Warm off-white hex | `--bg: #f0ede6` | ⚠️ |
| Background neutral (dark) | Deep navy/charcoal hex | `--bg: #0a0e13` | ⚠️ |
| Surface (card) | White / near-white | `--surface: #ffffff` (light), `#141b22` (dark) | ⚠️ |
| Text / foreground | Near-black + secondary | `--fg: #1a1a1a`, `--fg-dim: #5a6370` | ⚠️ |
| Success / warning / error palette | Status colours | **not defined in CSS tokens** | ❌ |
| Full accessible contrast matrix | WCAG AA minimum 4.5:1 for body text | Partial — focus ring only; body contrast not formally documented | ❌ |

> ⚠️ rows cannot be fully verified without the canonical guide — values may be correct but are unconfirmed.

### § Typography

| Brand guide section | Expected spec | Current implementation | Status |
|--------------------|---------------|------------------------|--------|
| Display / heading typeface | Named typeface + weight range | Fraunces (opsz 9–144, wt 600–700) via Google Fonts | ⚠️ |
| Body typeface | Named typeface + weight range | Inter 400/500/600/700 via Google Fonts | ⚠️ |
| Monospace typeface | Named typeface + weight range | JetBrains Mono 400/500 via Google Fonts | ⚠️ |
| Font loading strategy | `font-display` / self-host rules | `display=swap` via Google Fonts CDN | ⚠️ |
| Type scale (size + line-height) | Named scale tokens (e.g. `sm`, `base`, `lg`, `xl`) | `font-size: 16px` base; heading sizes inline in CSS — no named scale tokens | ❌ |
| Typographic fallback stack | System font order | Defined in CSS variables (`system-ui`, `Georgia`, etc.) | ✅ |
| Letter-spacing / tracking | Brand-specific kerning | **not documented in CSS tokens** | ❌ |

### § Spacing & Layout

| Brand guide section | Expected spec | Current implementation | Status |
|--------------------|---------------|------------------------|--------|
| Spacing scale | 4 px or 8 px base grid | Uses `clamp()` + `rem` inline — no named spacing tokens | ❌ |
| Max content width | Canonical breakpoint px | `max-width: min(1400px, 95vw)` — dashboard-appropriate; reading-measure handled per-block via `ch` | ✅ |
| Grid / column system | Named grid spec | CSS Grid ad-hoc in sections — no reusable grid tokens | ❌ |
| Border radius tokens | Named radius scale | Inline values (`0.5rem`, `0.75rem`, `1rem`) — no token names | ❌ |

### § Motion & Animation

| Brand guide section | Expected spec | Current implementation | Status |
|--------------------|---------------|------------------------|--------|
| Duration scale tokens | Fast / mid / slow ms values | **not defined as CSS custom properties** | ❌ |
| Easing tokens | Named easing curves | **not defined as CSS custom properties** | ❌ |
| Reduced-motion policy | `prefers-reduced-motion` requirement | Partial — some blocks gated, not systematic | ⚠️ |
| Allowed motion types | transform / opacity only rule | Implicitly followed but not formally documented | ⚠️ |

> EPIC-064 story 064.3 addresses the motion token gap.

### § Logo Usage

| Brand guide section | Expected spec | Current implementation | Status |
|--------------------|---------------|------------------------|--------|
| Primary mark (full) | Full SVG + PNG at 1×/2× | **not in repo** | ❌ |
| Compact / icon mark | `AN` initials mark SVG | `nlt-an-mark-sm.svg` ✅ | ✅ |
| Monochrome variant | Single-colour mark | **not in repo** | ❌ |
| Wordmark (name only) | SVG / PNG | **not in repo** | ❌ |
| Exclusion zone | Clear space rule in mark-widths | **not documented** | ❌ |
| Minimum size | px / pt minimum rendered size | **not documented** | ❌ |
| Incorrect usage | "Do not" examples | **not documented** | ❌ |
| Dark / reversed | Mark on dark surfaces | Implicit (`color` CSS fallback used) — no explicit cleared variant | ⚠️ |

### § Component Tokens (Buttons, Badges, etc.)

| Brand guide section | Expected spec | Current implementation | Status |
|--------------------|---------------|------------------------|--------|
| Button fill / hover | Colour + state tokens | Inline CSS per button class — no named component tokens | ❌ |
| Badge / pill | Colour spectrum | Inline — no named badge tokens | ❌ |
| Elevation / shadow | Shadow scale | Inline box-shadow values — no named shadow tokens | ❌ |

---

## Python Token Alignment (`VisualThemeTokens`)

`src/tapps_brain/visual_snapshot.py` derives theme seeds deterministically from a fingerprint hash. The constraints are already encoded in the model:

| `VisualThemeTokens` field | Constraint | Brand alignment |
|--------------------------|-----------|-----------------|
| `hue_primary` | 0–359 ° (amber wedge in practice) | Aligned with amber-only palette rule |
| `hue_accent` | 0–359 °; generator constrains 28–48 ° | Aligned — excludes cyan/blue/purple |
| `accent_chroma` | 0.0–1.0 | No canonical spec; reasonable |
| `surface_lightness` | 6–18 (dark-first OKLCH L) | Consistent with dark-theme bg values |
| `text_lightness` | 88–98 | Consistent with `--fg: #f0f4f8` in dark mode |
| `flow_angle_deg` | 0–359 ° | Derived from fingerprint; `--flow-ambient: 135deg` as default |

No changes to `VisualThemeTokens` are required at this stage. If the canonical guide publishes OKLCH-anchored colour tokens, re-validate field ranges.

---

## Recommended Next Steps (for EPIC-064 stories)

| Gap | Addressing story |
|-----|-----------------|
| Motion duration / easing tokens missing | 064.3 |
| Type scale tokens missing | 064.2 (IA refresh, can add scale) |
| Spacing tokens missing | 064.3 or follow-on |
| Full logo pack (wordmark, monochrome) | Obtain from internal NLT Labs repo; do not add to this doc until confirmed redistributable |
| Status colour palette (success/warn/error) | Follow-on — not in EPIC-064 scope |
| Contrast matrix / WCAG audit | 064.CLEAN (Lighthouse check) |

---

## Links

- [`examples/brain-visual/index.html`](../../../examples/brain-visual/index.html) — Dashboard CSS tokens
- [`examples/brain-visual/nlt-an-mark-sm.svg`](../../../examples/brain-visual/nlt-an-mark-sm.svg) — Compact AN mark
- [`src/tapps_brain/visual_snapshot.py`](../../../src/tapps_brain/visual_snapshot.py) — `VisualThemeTokens` model
- [`docs/planning/brain-visual-implementation-plan.md`](../../planning/brain-visual-implementation-plan.md) — Design principles
- [`docs/planning/epics/EPIC-064.md`](../../planning/epics/EPIC-064.md) — Epic spec
