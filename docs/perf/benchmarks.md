# Performance Benchmarks

Benchmark suite for tapps-brain HTTP adapter hot paths (TAP-1855).

## Running benchmarks

```bash
# All benchmarks
pytest tests/benchmarks/ -v --benchmark-only

# tools/list route only
pytest tests/benchmarks/test_http_adapter_tools_list.py -v --benchmark-only

# With JSON output (for baselining)
pytest tests/benchmarks/test_http_adapter_tools_list.py -v --benchmark-only \
  --benchmark-json=docs/perf/baseline-$(date +%Y-%m-%d).json
```

## Release gate

The benchmark is invocable from the release gate (skipped by default):

```bash
# Run benchmark step in release-ready.sh
SKIP_BENCHMARKS=0 bash scripts/release-ready.sh
```

Or directly with mean-regression detection against a stored baseline:

```bash
pytest tests/benchmarks/ -v --benchmark-only --benchmark-fail=mean:1.5x
```

`--benchmark-fail=mean:1.5x` fails if mean latency exceeds 1.5× the stored
baseline. To save a new baseline for comparison:

```bash
pytest tests/benchmarks/test_http_adapter_tools_list.py -v --benchmark-only \
  --benchmark-save=tools-list-baseline
```

Saved baselines live under `.benchmarks/` (git-ignored). The human-readable
reference baseline lives in `docs/perf/` (committed).

## Latency gates (TAP-1855)

| Route | p95 limit | p99 limit | Typical mean |
|-------|-----------|-----------|--------------|
| `GET /v1/tools/list` | 200 ms | 500 ms | ~0.55 ms |

Limits are deliberately conservative. The route serves from an in-memory
`bytes` buffer built at ASGI lifespan startup — no Python serialisation per
request, no database call. Measured latency (~0.5 ms) has ~400× headroom
against the 200 ms gate.

A regression crossing these limits almost certainly indicates an accidental
eager import, a synchronous DB call inserted on the hot path, or a structural
change that bypassed the snapshot cache.

## Refreshing the baseline

When a deliberate change meaningfully shifts latency (new middleware, changed
serialisation):

1. Run the benchmark on the same host/environment as the previous baseline.
2. Capture the JSON output:
   ```bash
   pytest tests/benchmarks/test_http_adapter_tools_list.py -v --benchmark-only \
     --benchmark-json=docs/perf/baseline-$(date +%Y-%m-%d).json
   ```
3. Commit the new baseline file and remove the old one (or keep both for diff history).
4. Update the "Typical mean" table above.
5. If the new baseline warrants updating the p95/p99 limits, edit them in
   `tests/benchmarks/test_http_adapter_tools_list.py` (`_P95_LIMIT_S` / `_P99_LIMIT_S`).

## Benchmark files

| File | Description |
|------|-------------|
| `tests/benchmarks/test_http_adapter_tools_list.py` | `/v1/tools/list` warm-path latency gate |
| `tests/benchmarks/test_decay_perf.py` | Exponential vs power-law decay at 10k scale |
| `tests/benchmarks/test_benchmarks.py` | Core store CRUD + retrieval benchmarks |
| `docs/perf/baseline-2026-05-15.json` | Reference numbers for diff comparison |
