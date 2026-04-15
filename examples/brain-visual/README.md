# Brain visual (live dashboard)

A static HTML/JS dashboard that polls the live tapps-brain `/snapshot` endpoint. There is no file-load or demo fallback — the page only renders real data from a running hub.

## Run

1. Start the tapps-brain HTTP adapter so `/snapshot` is reachable:

   ```bash
   tapps-brain mcp start --http
   # or
   docker compose up tapps-brain-mcp
   ```

2. Serve this folder over HTTP (so `brain-visual-help.js` loads and `/snapshot` is same-origin or proxied):

   ```bash
   cd examples/brain-visual
   python3 -m http.server 8090
   ```

3. Open `http://localhost:8090/index.html`. The page polls `/snapshot` every 30 s by default (configurable via the interval selector). Status: **LIVE · hh:mm:ss** when fresh, **STALE · Ns ago** after 90 s, **OFFLINE / ERROR** after 3 consecutive failures.

### Pointing at a different endpoint

Set a `<meta name="tapps-snapshot-url" content="https://your-host/snapshot">` in `index.html` to override the default `/snapshot` URL.

## In-page help (`?` buttons)

Each **scorecard** row opens a long-form article keyed by its stable `id` (same slugs as `visual_snapshot._build_scorecard`). Section headers, the top KPI strip, the scorecard count strip, and most tiles have one or more pills. Some tiles expose **two** topics (e.g. Hive hub reachability vs agent scope; snapshot schema vs federation flag; entries vs active profile). The Diagnostics bento tile uses `scorecard:diagnostics_bento` so it resolves to the same article namespace as the grid (not `concept:`).

## Navigation

The dashboard is a six-page hash-routed application. Each page is deep-linkable and bookmarkable:

| Hash | Page | Primary question answered |
|------|------|--------------------------|
| `#overview` | Overview | Is my brain healthy right now? |
| `#health` | Health | Which scorecard checks are failing and why? |
| `#memory` | Memory | How much memory is stored and how is it accessed? |
| `#retrieval` | Retrieval | What retrieval mode is active and what are the latency characteristics? |
| `#agents` | Agents & Hive | Which agents are registered, online, and federated? |
| `#integrity` | Integrity & Export | Are memory entries verified? What data can I export and at what privacy tier? |

The persistent side-nav collapses to an icon-only strip at ≤ 768 px and a hamburger at ≤ 480 px (CSS container queries — no JavaScript breakpoint polling). The nav badge next to **Health** shows live fail/warn counts, updated on every poll cycle.

Browser back/forward and reload all restore the correct page. The default page on load is `#overview`.

## View Transitions

Page switches use the [View Transitions API](https://developer.mozilla.org/en-US/docs/Web/API/View_Transitions_API) (Chrome 111+, Firefox 130+, Safari 18+) with a `transform`/`opacity` animation. When the browser does not support View Transitions, or when **prefers-reduced-motion** is enabled, page switches are instant — no partial or half-animated states.

| Browser | View Transitions | Fallback |
|---------|-----------------|----------|
| Chrome 111+ | ✓ Animated | — |
| Firefox 130+ | ✓ Animated | — |
| Safari 18+ | ✓ Animated | — |
| Older browsers | — | Instant (hidden attr toggle) |
| prefers-reduced-motion: reduce | — | Instant |

## Schema

Snapshot **schema_version** `2` includes retrieval mode, Hive hub stats, access histograms, memory-group counts, and a `scorecard` (operator pass/warn/fail checks). No raw memory text is included. See `docs/planning/brain-visual-implementation-plan.md`, `docs/guides/visual-snapshot.md`, and article source in `brain-visual-help.js`.

## Brand

CSS tokens, typography, and logo usage follow the **NLT Labs** design language. The canonical brand style sheet is an internal NLT Labs asset (not redistributed here). A gap matrix and redistribution notes are at [`docs/design/nlt-brand/README.md`](../../docs/design/nlt-brand/README.md).

## Motion system — manual test checklist

Motion tokens (`--dur-*`, `--ease-*`) are defined in `:root` and default to **instant / linear** (zero-duration, static-first). Non-zero durations are restored inside `@media (prefers-reduced-motion: no-preference)` only, following [WCAG 2.3.3 / Technique C39](https://www.w3.org/WAI/WCAG22/Techniques/css/C39.html).

**Test: reduced-motion OFF (motion enabled)**

1. macOS → System Settings → Accessibility → Display → **Reduce motion: off**
   Windows → Settings → Accessibility → Visual effects → **Animation effects: on**
2. Open `index.html` in a browser (served over HTTP).
3. Scroll down past the hero section — each `dash-section` and the trust bento should fade and slide up gently as it enters the viewport.
4. Open the **?** help drawer — it should slide in from the right (`transform: translateX`).
5. Hover a **tile** — it should lift slightly (`translateY(-4px)`).
6. Click a nav link — the View Transitions API should animate the page switch with a short `transform`/`opacity` cross-fade (Chrome 111+ / Firefox 130+ / Safari 18+).

**Test: reduced-motion ON (motion disabled)**

1. macOS → System Settings → Accessibility → Display → **Reduce motion: on**
   Windows → Settings → Accessibility → Visual effects → **Animation effects: off**
2. Reload `index.html`.
3. All sections should be **immediately visible** with no fade or slide — no partial/half-animated states.
4. The help drawer should open/close **without** a slide animation (instant show/hide).
5. Tile hover should produce **no transform** (border/colour change only via instant tokens).
6. Click a nav link — the page switch must be **instant** (no View Transitions animation, even if the browser supports it).

**Expected non-motion interactions that are always present (both states):**

- Border colour on tile and nav-link hover (instant under reduced motion).
- Theme toggle border highlight on hover.
- Bar fill widths (rendered at full width without animation under reduced motion).

**Test: keyboard navigation**

1. Focus the browser viewport (click once anywhere on the page).
2. Press **Tab** once — the skip-to-content link should appear and be focused.
3. Press **Enter** on the skip link — focus should jump to the main content area.
4. Continue **Tab** through the page — focus order should be: skip link → top bar → side-nav links → page content.
5. Press a **nav link** with Enter or Space — the target page should load and focus should move to the main content.
6. Verify no **focus traps** — Tab should always be able to exit any interactive region.
7. Open the **?** help drawer with keyboard (Tab to a `?` button, press Enter) — Escape should close it and return focus to the trigger.
