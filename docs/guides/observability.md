# Observability

tapps-brain exposes structured **metrics**, **health**, **audit**, **diagnostics**, and **feedback** surfaces through `MemoryStore` APIs, CLI commands, and MCP tools/resources. `MemoryStore.health()` / `maintenance health` / `memory://health` include **`profile_seed_version`** when the active profile sets `seeding.seed_version`. See `docs/engineering/call-flows.md` for where these run in recall and maintenance paths.

---

## HTTP probe endpoints (liveness / readiness)

The HTTP adapter (EPIC-060) exposes two lightweight probe endpoints designed
for Kubernetes `livenessProbe` / `readinessProbe` configuration:

| Endpoint | DB call | Success | Degraded |
|----------|---------|---------|---------|
| `GET /health` | **No** | 200 `{"status":"ok"}` | — (process restart required) |
| `GET /ready` | **Yes** | 200 `{"status":"ready", "migration_version": N}` | **503** `{"status":"degraded"}` |

**503 vs 500:** `/ready` returns **503** when the Postgres database is
unreachable or no DSN is configured (expected degraded state — Kubernetes
should hold traffic and retry).  It returns **500** only on an unexpected code
bug.  `/health` never returns 503 or 500 — any non-200 means the HTTP server
itself has crashed.

Probe routes (`/health`, `/ready`, `/metrics`) are always public — no
`Authorization` header required, even when `TAPPS_BRAIN_HTTP_AUTH_TOKEN` is set.

See [`docs/operations/k8s-probes.md`](../operations/k8s-probes.md) for full
Kubernetes manifest examples and tuning guidelines.

---

## Health checks

The `run_health_check()` function in `src/tapps_brain/health_check.py` produces a machine-readable `HealthReport` covering three sub-systems. The report is designed to complete in under 2 seconds on a Raspberry Pi 5.

### HealthReport fields

| Field | Type | Description |
|---|---|---|
| `status` | `ok` / `warn` / `error` | Roll-up of all sub-system statuses. |
| `generated_at` | ISO-8601 string | UTC timestamp of the report. |
| `elapsed_ms` | float | Wall-clock time to generate the report. |
| `store` | `StoreHealth` | Local memory store health (see below). |
| `hive` | `HiveHealth` | Hive shared store health (see below). |
| `integrity` | `IntegrityHealth` | Integrity verification results. |
| `errors` | list[str] | Error-level messages (drives `status=error`). |
| `warnings` | list[str] | Warning-level messages (drives `status=warn`). |

**Exit codes:** `HealthReport.exit_code()` returns 0 (ok), 1 (warn), or 2 (error).

### StoreHealth fields

| Field | Description |
|---|---|
| `entries` / `max_entries` | Current and maximum entry count. |
| `max_entries_per_group` | Per-`memory_group` cap when a profile sets one (STORY-044.7). |
| `schema_version` | Current private-memory schema migration version (integer, from `private_schema_version` table). |
| `last_migration_version` | Highest applied private migration version. |
| `tiers` | Dict mapping tier name to entry count. |
| `gc_candidates` / `consolidation_candidates` | Counts of entries eligible for GC or consolidation. |
| `vector_index_enabled` / `vector_index_rows` | Whether the pgvector HNSW index is active and its approximate row count. |
| `retrieval_effective_mode` | Machine-readable mode: `bm25_only`, `hybrid_pgvector_hnsw`, `hybrid_pgvector_empty`, `hybrid_on_the_fly_embeddings`, or `unknown`. |
| `retrieval_summary` | One-line human-readable explanation of the active retrieval stack. |
| `save_phase_summary` | Save-phase p50 latencies from in-process metrics (empty if none). |
| `profile_seed_version` | Profile seed recipe label when set. |
| `pool_saturation` / `pool_idle` | Fraction of Postgres private-backend pool in use (0.0–1.0); idle connection count. `null` for in-memory backends. |

### HiveHealth fields

| Field | Description |
|---|---|
| `connected` | `true` when the Postgres Hive connection was successfully opened **and** queried. |
| `hive_reachable` | `true` when the Hive DSN is configured and the Postgres server was reachable. Distinguishes "DSN missing" from "DSN present but connection failed". |
| `namespaces` | Sorted list of namespace names with entries. |
| `entries` / `agents` | Total entry count and registered agent count. |

**Interpretation:** When `hive_reachable=true` but `connected=false`, the Hive database file exists but could not be opened (check encryption key or file corruption). When both are `false`, the Hive has never been initialized on this machine.

### IntegrityHealth fields

| Field | Description |
|---|---|
| `corrupted_entries` | Number of entries whose HMAC hash does not match. |
| `orphaned_relations` | Relations pointing to memory keys that no longer exist. |
| `expired_entries` | Entries past their `valid_at` date. |

### Latency percentiles

`StoreHealth.save_phase_summary` reports p50 latencies for save operations collected by the in-process metrics timer. For full latency histograms (p50, p95, p99), use `tapps-brain store metrics` or the `MemoryStore.get_metrics()` API which returns a `MetricsSnapshot` with per-operation histogram breakdowns.

---

## Rate limiting

The sliding-window rate limiter (`src/tapps_brain/rate_limiter.py`) monitors write frequency to detect anomalous bursts. It operates in **warn-only mode** -- writes are never blocked, but warnings are emitted and anomaly counts are tracked.

### Default limits

| Limit | Default | Description |
|---|---|---|
| `writes_per_minute` | 20 | Per-minute sliding window. |
| `writes_per_session` | 100 | Cumulative session-lifetime cap. |

### Batch exemptions

The following `batch_context` values are exempt from rate limiting:

- `import_markdown` -- bulk Markdown import
- `memory_relay` -- relay sync
- `seed` -- profile seeding
- `federation_sync` -- cross-project federation
- `consolidate` -- auto-consolidation merges

### Behavior on limit exceeded

When a limit is exceeded the limiter:

1. Logs a `rate_limit_minute_exceeded` or `rate_limit_session_exceeded` structured warning via structlog.
2. Increments `RateLimiterStats.minute_anomalies` / `session_anomalies` counters (surfaced in `memory_health()`).
3. Returns a `RateLimitResult` with `minute_exceeded=True` or `session_exceeded=True` and a human-readable `message`.
4. The `allowed` field is always `True` (warn-only; writes proceed).

### 429-style error payload

When rate limits are exceeded the `RateLimitResult.message` field contains a description suitable for returning as a 429-style error body in MCP tool responses. Example:

```
Rate limit warning: 25 writes in last minute (limit: 20)
```

### Future: per-agent keys for MCP

Per-agent rate-limit keys are planned so that each MCP agent identity (from Hive agent registration) gets its own sliding window. This prevents a single noisy agent from consuming the entire write budget. Track progress in EPIC-047.

---

## Integrity

The integrity module (`src/tapps_brain/integrity.py`) provides tamper detection for stored memory entries using HMAC-SHA256.

### Hash format

- **Algorithm:** HMAC-SHA256
- **Key:** A per-installation 256-bit random key stored at `~/.tapps-brain/integrity.key` (created on first use, file permissions `0600`).
- **Canonical form:** `key|value|tier|source` encoded as UTF-8, where `|` is the literal pipe separator.
- **Output:** Hex-encoded 64-character digest string stored in `integrity_hash` column.

### Canonicalization rules

1. Fields are concatenated in the fixed order: `key`, `value`, `tier`, `source`.
2. The pipe `|` separator is safe because keys are validated slugs (no pipes) and `tier`/`source` are constrained enum strings.
3. **Known limitation:** if `value` contains a literal `|` followed by a valid `tier|source` suffix, a collision is theoretically possible but not exploitable in practice because `tier` and `source` take a small set of fixed enum values.

### Verification

`verify_integrity_hash()` uses `hmac.compare_digest()` for constant-time comparison, preventing timing side-channel attacks.

### CLI: verify-integrity

Run a full integrity sweep from the command line:

```bash
# Human-readable output
tapps-brain maintenance verify-integrity --project-dir .

# JSON output for automation
tapps-brain maintenance verify-integrity --project-dir . --json
```

**Output fields:**

| Field | Description |
|---|---|
| `total` | Total entries scanned. |
| `verified` | Entries with matching HMAC hash. |
| `tampered` | Entries with mismatched hash (exit code 1). |
| `no_hash` | Entries without a stored hash (pre-v8 or NULL). |
| `tampered_keys` | List of keys with integrity failures. |
| `missing_hash_keys` | List of keys missing a hash. |

**Exit codes:** 0 when all entries verify, 1 when tampered entries are found.

---

## Feedback signals

The feedback module (`src/tapps_brain/feedback.py`) collects structured quality signals about memory retrieval and content.

### FeedbackEvent schema

| Field | Type | Description |
|---|---|---|
| `id` | UUID string | Auto-generated unique identifier. |
| `event_type` | string | Object-Action snake_case name (open enum). Must match `[a-z][a-z0-9]*(_[a-z][a-z0-9]*)+`. |
| `entry_key` | string or null | Related memory entry key. |
| `session_id` | string or null | Calling session identifier. |
| `utility_score` | float or null | Numeric signal in [-1.0, 1.0]. |
| `details` | dict | Arbitrary additional metadata. |
| `timestamp` | ISO-8601 string | UTC time of recording. |

### Built-in event types

| Event type | Description | Typical utility_score |
|---|---|---|
| `recall_rated` | User rated a recall result. | 1.0 (helpful), 0.0 (irrelevant/outdated) |
| `gap_reported` | User indicated missing knowledge. | -- |
| `issue_flagged` | User flagged a quality issue. | -- |
| `implicit_positive` | Recall followed by reinforce within window. | 1.0 |
| `implicit_negative` | Recall NOT followed by reinforce within window. | -0.1 |
| `implicit_correction` | Recall followed by store with overlapping content. | -- |

### Schema registry for custom events

Custom event types are registered via `FeedbackConfig.custom_event_types` in the profile YAML:

```yaml
profile:
  feedback:
    custom_event_types:
      - deploy_completed
      - pr_review_requested
    strict_event_types: true
```

When `strict_event_types` is `true` (the default), `FeedbackStore.record()` rejects any event type not in the combined set of built-in + custom types.

### Retention

The implicit feedback window (`implicit_feedback_window_seconds`, default 300 s / 5 minutes) controls how long the system waits before emitting `implicit_negative`. Feedback events are stored in the `feedback_events` Postgres table (migration 003, scoped to `(project_id, agent_id)`) and are retained indefinitely (no automatic purge). Diagnostics history has a configurable `retention_days` (default 90, range 1-3650).

---

## Diagnostics / SLO scorecard

The diagnostics module (`src/tapps_brain/diagnostics.py`) computes a multi-dimensional quality scorecard for the memory store.

### Dimensions and default weights

| Dimension | Weight | Description |
|---|---|---|
| `retrieval_effectiveness` | 0.22 | Hit rate + mean confidence, blended with `recall_rated` feedback when available. |
| `freshness` | 0.18 | Exponential decay score based on entry age relative to tier half-life. |
| `completeness` | 0.12 | Fraction of entries with non-empty value and source_agent. |
| `duplication` | 0.15 | 1 minus the ratio of consolidation candidates to total entries. |
| `staleness` | 0.15 | 1 minus the ratio of GC candidates to total entries. |
| `integrity` | 0.18 | Ratio of verified entries to (verified + tampered). |

Weights are re-normalized to sum to 1.0. Profile YAML can override individual weights via `diagnostics.dimension_weights`. Custom dimensions can be added via `diagnostics.custom_dimension_paths` (importable dotted paths to `HealthDimension` implementations).

### Composite score and grades

The composite score is a weighted sum in [0.0, 1.0]. CLI letter grades:

| Grade | Composite range |
|---|---|
| A | >= 0.85 |
| B | >= 0.70 |
| C | >= 0.55 |
| D | >= 0.40 |
| F | < 0.40 |

### Circuit breaker

The four-state circuit breaker (`CircuitBreaker`) transitions based on composite score:

| State | Composite | Effect |
|---|---|---|
| `closed` | >= 0.6 | Normal operation. Hive recall multiplier = 1.0. |
| `degraded` | >= 0.3 | Reduced trust. Hive recall multiplier = 0.5. |
| `open` | < 0.3 | Quality too low. Hive recall multiplier = 0.0. Auto-remediation may trigger. |
| `half_open` | (probe) | After cooldown, probes re-check quality. Hive recall multiplier = 0.5. |

**Circuit state and MCP tool errors:** When the circuit is `open`, the Hive recall multiplier drops to 0.0, effectively disabling Hive-sourced results in MCP `memory_recall` responses. MCP tools surface the circuit state in the diagnostics JSON export so operators can correlate tool-level errors with quality degradation.

### Auto-remediation (when OPEN)

When the circuit breaker is `open`, the following tier-1 remediations may fire (each with a 1-hour cooldown):

- **consolidate** -- runs periodic consolidation scan when `duplication.score < 0.5`.
- **gc** -- runs garbage collection when `staleness.score < 0.5`.
- **integrity_alert** -- flags an alert when `integrity.score < 0.8`.

### Anomaly detection (EWMA)

`AnomalyDetector` uses Exponential Weighted Moving Average with configurable parameters:

- `lam=0.2` -- smoothing factor
- `min_obs=20` -- minimum observations before alerting
- `warn_sigma=2.0` / `crit_sigma=3.0` -- z-score thresholds
- `confirm_window=3` -- consecutive breaches required to fire

Alerts include `threshold_warning` and `threshold_critical` levels with z-score and affected dimension name.

### JSON export (dashboard schema v1)

`DiagnosticsReport.model_dump(mode="json")` produces the canonical JSON export. Key top-level fields:

```json
{
  "composite_score": 0.82,
  "dimensions": {
    "retrieval_effectiveness": {"name": "...", "score": 0.9, "raw_details": {}},
    "freshness": {"name": "...", "score": 0.85, "raw_details": {}},
    "completeness": {"name": "...", "score": 0.78, "raw_details": {}},
    "duplication": {"name": "...", "score": 0.92, "raw_details": {}},
    "staleness": {"name": "...", "score": 0.88, "raw_details": {}},
    "integrity": {"name": "...", "score": 1.0, "raw_details": {}}
  },
  "recorded_at": "2026-04-04T...",
  "recommendations": ["..."],
  "hive_diagnostics": {},
  "hive_composite_score": null,
  "circuit_state": "closed",
  "gap_count": 3,
  "correlation_adjusted": false,
  "anomalies": []
}
```

### Diagnostics history

`DiagnosticsHistoryStore` persists snapshots to the `diagnostics_history` Postgres table (migration 004, scoped to `(project_id, agent_id)`). It supports:

- `record(report)` -- append a snapshot
- `history(limit=100)` -- recent snapshots (newest first)
- `prune_older_than(days)` -- retention cleanup
- `rolling_average(dimension, window=20)` -- windowed average for trend analysis

### Correlation-adjusted weights

When enough history rows exist (>= 20), `adjust_weights_for_correlation()` detects highly correlated dimension pairs (Pearson r > 0.7) and down-weights them by 30% to reduce double-counting.

---

## Distributed tracing (OpenTelemetry)

The optional `[otel]` extra installs types and helpers in `src/tapps_brain/otel_exporter.py` (`create_exporter`, `OTelExporter`).

### Meter and span naming

The OTel meter is named `tapps_brain`. Counters and histograms are created lazily from `MetricsSnapshot` data:

- **Counters** mirror in-memory collector counters (e.g. `store.save`, `store.recall`). Only deltas since the last export are sent (matches OTel `Counter.add()` contract).
- **Histograms** use unit `ms` by default (latency). A single observation of `stats.mean` is recorded per export cycle so the OTel SDK can aggregate.

### Sampling strategy

The exporter records all observations without client-side sampling -- the assumption is that the OTel collector or backend handles sampling policy. Errors from the OTel SDK are silently suppressed so an unavailable collector never crashes the caller.

**Status:** the exporter module is **not** initialized from `MemoryStore`, the Typer CLI, or `mcp_server.py`. Nothing turns OTel "on" at process start today; only unit tests exercise the module. Installing `tapps-brain[otel]` alone does not attach spans to store operations. Product wiring (CLI flag, env gate, or store hooks) is tracked under **EPIC-032**.

### Pin rationale

The `[otel]` extra pins `opentelemetry-api` and `opentelemetry-sdk` to `>=1.20,<3`. This range:

- **Floor (`>=1.20`):** The minimum version that supports the Metrics API (`create_counter`, `create_histogram`) used by `OTelExporter`. Earlier versions lack stable metrics support.
- **Ceiling (`<3`):** Allows both the current 1.x stable series and the anticipated 2.x series. The OTel Python SDK follows [SemVer](https://semver.org/); a major bump (3.0) could break the meter/counter/histogram API surface we depend on, so we cap there.
- **No minor pin:** Within a major series, the OTel SDK maintains backward compatibility. Pinning to a narrow minor range would block security patches and force unnecessary dependency conflicts for consumers who also use OTel.

If OpenTelemetry releases a 3.x that breaks compatibility, a tapps-brain patch release will adjust the pin and adapt `otel_exporter.py`.

**Developers:** see `tests/unit/test_otel_exporter.py` for intended usage patterns once wired.

---

## Flywheel (continuous improvement)

The flywheel module (`src/tapps_brain/flywheel.py`) closes the loop between feedback signals, confidence scores, knowledge gaps, and quality reports.

### Bayesian confidence updates

`FeedbackProcessor.process_feedback()` scans unprocessed feedback events and adjusts entry confidence using a Beta-posterior model:

1. **Beta mean:** `(positive + 0.5) / (positive + negative + 1.0)` (Jeffreys prior).
2. **K-factor:** `base_K * tier_volatility[tier]` controls how aggressively confidence moves. Default `base_K=1.0`.
3. **Tier volatility:** `architectural=0.3`, `pattern=0.5`, `procedural=0.7`, `context=1.0`. Higher-tier entries are more stable.
4. **Clamping:** confidence is clamped to `[min_confidence, 1.0]` (default `min_confidence=0.05`).

### Cursor idempotency

The processor tracks a `(timestamp, id)` cursor persisted via `flywheel_meta_set("feedback_cursor", ...)`. On each run it skips events at or before the cursor, ensuring:

- **Idempotent re-runs:** calling `process_feedback()` twice with the same store state applies each event exactly once.
- **Incremental processing:** only new events since the last cursor position are considered.

The cursor is stored as a JSON object `{"ts": "...", "id": "..."}` and compared with a lexicographic `(timestamp, id)` tuple.

### Knowledge gaps

`GapTracker.analyze_gaps()` clusters `gap_reported` feedback and zero-result recall signals:

- **Jaccard clustering** (default): greedy pairwise clustering with `jaccard_threshold=0.6`.
- **HDBSCAN clustering** (optional, requires `hdbscan` + `sentence-transformers`): embedding-based semantic clustering.
- **Priority scoring:** `count * tier_weight * trend_factor`. Trend compares recent 30-day vs previous 30-day volumes (1.5x for increasing, 0.7x for decreasing).

### LLM judge safety

The flywheel processes feedback events deterministically without invoking any LLM judge. All confidence adjustments are computed from the Bayesian model with fixed parameters. This design prevents prompt injection attacks from influencing confidence scores -- there is no LLM call surface in the feedback-to-confidence path. Red-team prompts embedded in feedback event `details` fields are treated as opaque JSON and never executed or interpreted.

### Quality reports

`generate_report()` assembles a Markdown quality report from diagnostics, feedback, and gap data. Built-in report sections (in priority order):

1. `health_summary` (priority 10) -- composite score and circuit state
2. `dimension_breakdown` (priority 20) -- per-dimension scores
3. `anomaly_alerts` (priority 30) -- EWMA anomaly alerts
4. `feedback_summary` (priority 40) -- event type counts
5. `knowledge_gaps` (priority 50) -- top gap patterns
6. `recommendations` (priority 100) -- actionable items

Custom sections can be registered via `FlywheelConfig.custom_report_sections` or `ReportRegistry.register()`.

### Hive cross-project feedback

`aggregate_hive_feedback()` scans Hive-level feedback events and:

- Aggregates ratings by `(namespace, entry_key)`.
- Clusters cross-project knowledge gaps.
- Identifies issue hotspots (entries flagged by >= 2 projects).
- `process_hive_feedback()` lowers Hive entry confidence by `penalty_factor=0.85` when `>= threshold` (default 3) projects report negative ratings.
