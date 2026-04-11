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
