# Visual snapshot (`brain-visual.json`)

Export a **versioned JSON snapshot** of store health, tier mix, and related signals for static dashboards and the brain-visual demo surface.

## CLI

From the project directory:

```bash
tapps-brain visual export
tapps-brain visual export -o ./out/brain-visual.json
tapps-brain visual export --skip-diagnostics   # faster; omits circuit/score fields
```

Implementation: `src/tapps_brain/visual_snapshot.py` · CLI entry: `tapps_brain/cli.py` (`visual export`).

## JSON shape

The payload is produced by `build_visual_snapshot()` and serialized with `snapshot_to_json()`. For field-level design and frontend contracts, see:

- `docs/planning/brain-visual-implementation-plan.md`
- `examples/brain-visual/README.md`

## Related

- `MemoryStore.health()` / MCP `tapps_brain_health` — overlapping health data inside the live store API.
- Regression tests: `tests/unit/test_visual_snapshot.py`
