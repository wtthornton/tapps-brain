---
title: "NLT Labs — Brand Style Sheet & Logo Pack Audit"
description: "Gap matrix between the NLTWeb canonical brand guide and brain-visual implementation. Source of truth for brand-aligned work in EPIC-064."
created: 2026-04-11
updated: 2026-06-23
epic: EPIC-064
story: STORY-064.1
---

# NLT Labs — Brand Style Sheet & Logo Pack Audit

## Status

| Item | Location |
|------|----------|
| Canonical `BRAND-STYLE-GUIDE.md` | **NLTWeb** repo — `docs/BRAND-STYLE-GUIDE.md` (v3.2, June 2026) |
| Canonical logo pack | **NLTWeb** repo — `assets/logo-pack/` |
| Redistributable subset in tapps-brain | `examples/brain-visual/assets/logo-pack/svg/` |
| Favicon | `examples/brain-visual/favicon.svg` (gold mark) |
| Token references in code | `src/tapps_brain/visual_snapshot.py` (`VisualThemeTokens`) |
| CSS token definitions | `examples/brain-visual/index.html` `:root` |

---

## Canonical Guide Fetch Path

1. Open sibling checkout **`NLTWeb`** (`../NLTWeb` from this repo) or internal design repo.
2. Read `docs/BRAND-STYLE-GUIDE.md` (binding spec) and `assets/logo-pack/README.md` (inventory).
3. When NLTWeb ships a new guide version, re-copy approved SVGs and update the gap matrix below.
4. Do **not** commit unreleased logo variants. Only the subset listed in `examples/brain-visual/assets/logo-pack/README.md` is cleared for this repo.

Regenerate upstream pack: `NLTWeb/scripts/rebuild_logo_pack.py`.

---

## Logo Pack — As-Is in Repository

| Asset | Path | Notes |
|-------|------|-------|
| Header lockup (light) | `assets/logo-pack/svg/nltlabs-lockup-fullcolor.svg` | Transparent; deep honey `#bd6510` mark |
| Header lockup (dark) | `assets/logo-pack/svg/nltlabs-lockup-fullcolor-on-dark.svg` | Warm gold `#e8941f` mark on dark theme |
| Gold mark (favicon) | `favicon.svg` + `assets/logo-pack/svg/nltlabs-mark-gold.svg` | Browser icon |
| Mark on dark (transparent) | `assets/logo-pack/svg/nltlabs-mark-gold-on-dark-transparent.svg` | Optional compact use |
| Wordmark only (light/dark) | `nltlabs-wordmark-fullcolor.svg`, `nltlabs-wordmark-on-dark.svg` | Horizontal tight layouts |

**Implementation:** Theme-aware `<img>` lockups per NLTWeb §Logo — `.logo-lockup-img--light` / `--dark` toggled by `html[data-theme="dark"]`. Do **not** reconstruct wordmark in HTML.

**Clear space / minimum size:** See NLTWeb `BRAND-STYLE-GUIDE.md` §Logo (0.5× cap-height clear space; ≥128px wide full lockup). Dashboard header renders 48–70px height by breakpoint.

---

## Gap Matrix (updated 2026-06-23)

### § Color

| Brand guide section | NLTWeb v3.1 spec | Current implementation | Status |
|--------------------|------------------|------------------------|--------|
| Primary accent | `#bd6510` deep honey | `--nlt-accent-primary: #bd6510` | ✅ |
| Secondary accent | `#e8941f` warm gold | `--nlt-accent-secondary: #e8941f` | ✅ |
| Dim / pressed | `#98500e` burnt | `--nlt-accent-dim: #98500e` | ✅ |
| Gradient | `135deg, #bd6510 → #e8941f` | `--nlt-gradient` matches | ✅ |
| No-cyan rule | Amber wedge only | `VisualThemeTokens` hue 28–48° | ✅ |
| Background light/dark | `#f0ede6` / `#0a0e13` | `--bg` tokens match | ✅ |

### § Logo Usage

| Brand guide section | Expected spec | Current implementation | Status |
|--------------------|---------------|------------------------|--------|
| Primary lockup SVG | Light + dark variants | NLTWeb lockup SVGs, theme swap | ✅ |
| Favicon | Gold mark SVG | `/favicon.svg` | ✅ |
| Wordmark in HTML | **Do not reconstruct** | Removed — SVG-only lockup | ✅ |
| Monochrome lockups | mono-dark / mono-white | Not copied (print-only); add if needed | ⚠️ |
| Exclusion zone | 0.5× cap height | Documented; not drawn in UI | ⚠️ |

### § Motion

Motion tokens (`--dur-*`, `--ease-*`) defined in `:root` with `prefers-reduced-motion` gating — see `examples/brain-visual/README.md`.

---

## Python Token Alignment (`VisualThemeTokens`)

`VisualThemeTokens` in `visual_snapshot.py` derives amber-family hues from fingerprint bytes (28–48°). No field changes required for v3.1 palette; CSS `:root` carries the canonical hex values for the static dashboard.

---

## Links

- [`examples/brain-visual/index.html`](../../../examples/brain-visual/index.html) — Dashboard + brand CSS
- [`examples/brain-visual/assets/logo-pack/README.md`](../../../examples/brain-visual/assets/logo-pack/README.md) — Shipped SVG subset
- [`src/tapps_brain/visual_snapshot.py`](../../../src/tapps_brain/visual_snapshot.py) — `VisualThemeTokens`
- NLTWeb `docs/BRAND-STYLE-GUIDE.md` — canonical style guide (sibling repo)
