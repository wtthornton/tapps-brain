# Brain visual demo (static dashboard)

1. From a project with tapps-brain installed (`uv sync --extra cli`):

   ```bash
   tapps-brain visual export -o brain-visual.json
   ```

   - `--skip-diagnostics` — faster; omits diagnostics block.
   - `--privacy strict` — redacts `store_path` and tampered key list in JSON.
   - `--privacy local` — includes tag frequencies and named `memory_group` counts (do not share publicly).

2. Open `index.html` in a browser and use **Load snapshot** to pick `brain-visual.json`, or serve this folder so `brain-visual.json` loads automatically. Serving over HTTP also loads `scorecard-derive.js` (fallback if an older JSON lacks the `scorecard` array) and `brain-visual-help.js` (the **?** pills open detailed articles for scorecard rows and concepts).

3. **Scorecard & tickets:** the page shows pass/warn/fail rows from the export’s `scorecard` field; use **Copy GitHub issue (Markdown)** to paste into GitHub/Jira.

Snapshot **schema_version** `2` adds retrieval mode, Hive hub stats, access histograms, memory-group counts, and a **`scorecard`** (operator pass/warn/fail checks). No raw memory text is included. See `docs/planning/brain-visual-implementation-plan.md` and `docs/guides/visual-snapshot.md`.
