# Brain visual demo (static dashboard)

1. From a project with tapps-brain installed (`uv sync --extra cli`):

   ```bash
   tapps-brain visual export -o brain-visual.json
   ```

   - `--skip-diagnostics` — faster; omits diagnostics block.
   - `--privacy strict` — redacts `store_path` and tampered key list in JSON.
   - `--privacy local` — includes tag frequencies and named `memory_group` counts (do not share publicly).

2. Open `index.html` in a browser and use **Load snapshot** to pick `brain-visual.json`, or serve this folder so `brain-visual.json` loads automatically. Serving over HTTP also loads `scorecard-derive.js` (fallback if an older JSON lacks the `scorecard` array) and `brain-visual-help.js`.

3. **In-page help (`?`):** Each **scorecard** row opens a long-form article keyed by its stable `id` (same slugs as `visual_snapshot._build_scorecard`). Section headers, the top KPI strip, the scorecard count strip, and most tiles have one or more pills. Some tiles expose **two** topics (e.g. Hive hub reachability vs agent scope; snapshot schema vs federation flag; entries vs active profile). The Diagnostics bento tile uses **`scorecard:diagnostics_bento`** so it resolves to the same article namespace as the grid (not `concept:`).

4. **Scorecard & tickets:** the page shows pass/warn/fail rows from the export’s `scorecard` field; use **Copy GitHub issue (Markdown)** to paste into GitHub/Jira.

Snapshot **schema_version** `2` adds retrieval mode, Hive hub stats, access histograms, memory-group counts, and a **`scorecard`** (operator pass/warn/fail checks). No raw memory text is included. See `docs/planning/brain-visual-implementation-plan.md`, `docs/guides/visual-snapshot.md`, and article source in `brain-visual-help.js`.

## Brand

CSS tokens, typography, and logo usage follow the **NLT Labs** design language. The canonical brand style sheet is an internal NLT Labs asset (not redistributed here). A gap matrix and redistribution notes are at [`docs/design/nlt-brand/README.md`](../../docs/design/nlt-brand/README.md).

## Motion system — manual test checklist

Motion tokens (`--dur-*`, `--ease-*`) are defined in `:root` and default to **instant / linear** (zero-duration, static-first). Non-zero durations are restored inside `@media (prefers-reduced-motion: no-preference)` only, following [WCAG 2.3.3 / Technique C39](https://www.w3.org/WAI/WCAG22/Techniques/css/C39.html).

**Test: reduced-motion OFF (motion enabled)**

1. macOS → System Settings → Accessibility → Display → **Reduce motion: off**  
   Windows → Settings → Accessibility → Visual effects → **Animation effects: on**
2. Open `index.html` in a browser (serve over HTTP or as `file://`).
3. Scroll down past the hero section — each `dash-section` and the trust bento should fade and slide up gently as it enters the viewport.
4. Open the **?** help drawer — it should slide in from the right (`transform: translateX`).
5. Hover a **tile** — it should lift slightly (`translateY(-4px)`).

**Test: reduced-motion ON (motion disabled)**

1. macOS → System Settings → Accessibility → Display → **Reduce motion: on**  
   Windows → Settings → Accessibility → Visual effects → **Animation effects: off**
2. Open (or reload) `index.html`.
3. All sections should be **immediately visible** with no fade or slide — no partial/half-animated states.
4. The help drawer should open/close **without** a slide animation (instant show/hide).
5. Tile hover should produce **no transform** (border/colour change only via instant tokens).

**Expected non-motion interactions that are always present (both states):**

- Border colour on tile and nav-link hover (instant under reduced motion).
- Theme toggle border highlight on hover.
- Bar fill widths (rendered at full width without animation under reduced motion).
