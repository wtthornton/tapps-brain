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

<a id="visual-dashboard-troubleshooting"></a>

## Visual dashboard troubleshooting

Use this runbook when the brain-visual dashboard badge shows **OFFLINE** or **ERROR**, panels are empty, or `/snapshot` fails. The nginx dashboard (`tapps-visual`, default `:8088`) proxies `/snapshot` to `tapps-brain-http`; distinguish **visual container down** from **brain upstream slow** with `GET /healthz` on each service.

| Symptom | Likely cause | Remediation |
|---------|--------------|-------------|
| **OFFLINE** / network error | `tapps-visual` not running or wrong URL | `docker ps --filter name=tapps-visual`; `curl -sS http://localhost:8088/healthz` → `{"ok":true,"service":"tapps-visual"}` |
| **ERROR · timeout** / **504** | Brain snapshot build exceeded nginx `proxy_read_timeout` (30s) or brain-http unhealthy | `docker logs tapps-brain-http --tail 50`; `curl -sS http://localhost:8080/healthz`; cold builds target ≤25s — see Prometheus `tapps_brain_snapshot_build_duration_seconds` |
| **ERROR · auth** / **401** or **403** | `TAPPS_BRAIN_AUTH_TOKEN` mismatch between visual nginx and brain-http | Align token in `docker/.env`, then recreate both services |
| **ERROR · no store** / **503** | No `MemoryStore` on brain-http | Verify `TAPPS_BRAIN_DATABASE_URL` and migrate sidecar; check startup logs |
| Empty panels but HTTP 200 | Stale browser cache or unexpected JSON shape | Probe `/snapshot` manually (below); confirm `schema_version >= 2` |

### Quick probes

Visual nginx liveness (no brain upstream):

```bash
curl -sS http://localhost:8088/healthz
# {"ok":true,"service":"tapps-visual"}
```

Brain health and snapshot (replace `<token>` with `TAPPS_BRAIN_AUTH_TOKEN` from `docker/.env`):

```bash
curl -sS http://localhost:8080/healthz | jq .
docker logs tapps-brain-http --tail 50
curl -sS -H "Authorization: Bearer <token>" http://localhost:8080/snapshot | head -c 400
```

After changing `TAPPS_BRAIN_AUTH_TOKEN`, restart both containers so nginx injects the new bearer:

```bash
docker compose -p tapps-brain -f docker/docker-compose.hive.yaml up -d --force-recreate tapps-visual tapps-brain-http
```

### Snapshot SLO metrics (STORY-078.6)

When `tapps-brain-http` exposes `/metrics`, scrape:

- `tapps_brain_snapshot_build_duration_seconds` — cold `build_visual_snapshot` latency histogram (buckets: 0.1, 0.5, 1, 2, 5, 10, 30s)
- `tapps_brain_snapshot_cache_hits_total` — TTL cache hits (15s default; no histogram observe on hit)

Example Prometheus alert (p95 build latency > 5s for 5 minutes):

```yaml
- alert: TappsBrainSnapshotBuildSlow
  expr: |
    histogram_quantile(
      0.95,
      sum(rate(tapps_brain_snapshot_build_duration_seconds_bucket[5m])) by (le)
    ) > 5
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: Visual snapshot cold build p95 > 5s
    description: Check brain-http logs and DB latency; nginx proxy_read_timeout is 30s.
```

See also: [hive-deployment.md § Visual dashboard](hive-deployment.md#visual-dashboard), [docker/README.md](../docker/README.md).
