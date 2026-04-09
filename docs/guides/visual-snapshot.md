# Visual snapshot (`brain-visual.json`)

Export a **versioned JSON snapshot** of store health, tier mix, and related signals for static dashboards and the brain-visual demo surface.

## CLI

From the project directory:

```bash
tapps-brain visual export
tapps-brain visual export -o ./out/brain-visual.json
tapps-brain visual export --skip-diagnostics   # faster; omits circuit/score fields
tapps-brain visual export --privacy strict     # redact path + tampered keys in JSON
tapps-brain visual export --privacy local      # include tag + memory_group detail (local only)
```

**Snapshot JSON `schema_version`:** `2` is current (retrieval mode, Hive hub slice, `access_stats`, `memory_group_count`, optional `tag_stats`, **`scorecard`** operator checks for pass/warn/fail and issue templates). Older `1` files still load in the demo with reduced panels.

Implementation: `src/tapps_brain/visual_snapshot.py` · CLI entry: `tapps_brain/cli.py` (`visual export`).

### In-dashboard help (brain-visual demo)

The static demo loads `examples/brain-visual/brain-visual-help.js`. Help entries are keyed as:

- **`scorecard:<id>`** — matches each `ScorecardCheck.id` from `_build_scorecard` (e.g. `diagnostics_circuit`, `retrieval_stack`, `diagnostics_bento` for the bento Diagnostics tile).
- **`concept:<id>`** — cross-cutting topics (fingerprint, privacy tiers, KPI strip, issue/ticket copy helpers, etc.).

Expand coverage by adding objects to `HELP_SCORECARD` or `HELP_CONCEPTS` and wiring `data-help` on a pill. See `examples/brain-visual/README.md` for operator-facing notes.

## JSON shape

The payload is produced by `build_visual_snapshot()` and serialized with `snapshot_to_json()`. For field-level design and frontend contracts, see:

- `docs/planning/brain-visual-implementation-plan.md`
- `examples/brain-visual/README.md`

## PNG capture (headless)

Export a static PNG poster of the dashboard — useful for slides, README headers, or visual regression baselines.

### Setup

```bash
uv sync --extra visual
playwright install chromium
```

### Usage

```bash
# 1. Generate the snapshot JSON
tapps-brain visual export -o brain-visual.json

# 2. Capture PNG (defaults: 1280×900, light theme)
tapps-brain visual capture --json brain-visual.json --output brain-visual.png

# Dark theme, wider viewport
tapps-brain visual capture --json brain-visual.json --output brain-visual-dark.png \
    --theme dark --width 1440 --height 960

# Custom HTML path (if not running from repo root)
tapps-brain visual capture --json brain-visual.json \
    --html /path/to/examples/brain-visual/index.html \
    --output brain-visual.png
```

### Manual checklist

- [ ] `uv sync --extra visual && playwright install chromium` completed without errors.
- [ ] `tapps-brain visual export -o brain-visual.json` produces a valid JSON file.
- [ ] `tapps-brain visual capture --json brain-visual.json --output out.png` exits 0 and writes a PNG.
- [ ] Open `out.png` — KPI strip, scorecard rows, tier chart, and fingerprint are all visible.
- [ ] Repeat with `--theme dark` — dark background renders correctly.
- [ ] Verify no memory body text appears in the PNG (only aggregated stats).

### Programmatic use

```python
from pathlib import Path
from tapps_brain.visual_snapshot import capture_png

capture_png(
    html_path=Path("examples/brain-visual/index.html"),
    json_path=Path("brain-visual.json"),
    output=Path("out/brain-visual.png"),
    theme="dark",
    width=1440,
)
```

`capture_png` raises `RuntimeError` with an install hint when `playwright` is not available, so it is safe to call conditionally.

## Related

- `MemoryStore.health()` / MCP `tapps_brain_health` — overlapping health data inside the live store API.
- Regression tests: `tests/unit/test_visual_snapshot.py`
