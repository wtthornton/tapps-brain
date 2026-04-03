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

## Related

- `MemoryStore.health()` / MCP `tapps_brain_health` — overlapping health data inside the live store API.
- Regression tests: `tests/unit/test_visual_snapshot.py`
