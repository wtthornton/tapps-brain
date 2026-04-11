# Telemetry Policy — Allowed and Forbidden Attributes

> **Scope:** tapps-brain v3 (EPIC-061, STORY-061.6).  Applies to all OpenTelemetry
> spans, metric labels, structured log fields, and any diagnostic output emitted
> by tapps-brain in production.

---

## Why this policy exists

tapps-brain processes memory content that may include project secrets, personal
information, and proprietary code.  A single careless `span.set_attribute("query",
raw_query)` call can leak that content to every observability backend your
organisation uses — Grafana, DataDog, OTLP collectors, log aggregators, S3 audit
buckets.  This policy defines exactly **which attributes are safe** and which are
**unconditionally forbidden**.

High-cardinality metric labels (unbounded strings) also degrade Prometheus, Mimir,
and other time-series backends by creating label explosions.  The allowed set below
is low-cardinality by design.

---

## Allowed span attributes

The following attributes may be set on OTel spans.  All values are **bounded enums
or numeric counts** — never raw user-controlled strings.

| Attribute | Type | Allowed values |
|-----------|------|---------------|
| `memory.tier` | string (enum) | `"architectural"` \| `"pattern"` \| `"procedural"` \| `"context"` |
| `memory.scope` | string (enum) | `"project"` \| `"branch"` \| `"session"` |
| `memory.agent_scope` | string (enum) | `"private"` \| `"domain"` \| `"hive"` \| `"group:<name>"` ¹ |
| `operation.type` | string (enum) | `"remember"` \| `"recall"` \| `"search"` \| `"hive_propagate"` \| `"hive_search"` |
| `error.type` | string (enum) | `"content_blocked"` \| `"invalid_scope"` \| `"invalid_group"` \| `"write_rules_violation"` \| `"db_error"` |
| `result_count` | integer | ≥ 0 — number of recall results returned |
| `hive_memory_count` | integer | ≥ 0 — Hive results merged into recall |
| `hive.group_scoped` | string (bool) | `"true"` \| `"false"` (NOT the group name) |
| `service.name` | string (env) | Set via `OTEL_SERVICE_NAME`; defaults to `"tapps-brain"` |
| `service.version` | string (env) | Set via `OTEL_SERVICE_VERSION`; defaults to `""` |

¹ `group:<name>` encodes membership routing, not free-form user content; the group
name itself is an operator-configured identifier, not user-submitted text.  Review
new group-scoped attributes on a case-by-case basis.

### Canonical span names

Use only the constants from `tapps_brain.otel_tracer`:

| Constant | Value | Used for |
|----------|-------|---------|
| `SPAN_REMEMBER` | `tapps_brain.remember` | `MemoryStore.save` / `AgentBrain.remember` |
| `SPAN_RECALL` | `tapps_brain.recall` | `AgentBrain.recall` |
| `SPAN_SEARCH` | `tapps_brain.search` | Low-level retrieval |
| `SPAN_HIVE_PROPAGATE` | `tapps_brain.hive.propagate` | Hive write fan-out |
| `SPAN_HIVE_SEARCH` | `tapps_brain.hive.search` | Hive cross-agent search |

Do **not** invent span names outside this list without a code-review discussion and
a corresponding update to `docs/engineering/system-architecture.md`.

---

## Allowed metric dimensions

For OTel metric instruments (counters, histograms) the allowed label / attribute set
is the same bounded enum list above, minus `service.*` (those are resource attributes,
not metric dimensions).

The OTel exporter (`tapps_brain.otel_exporter`) applies this set; see its module
docstring for the authoritative table.

---

## Forbidden attributes — unconditionally prohibited

The following **must never** appear as span attribute values, metric label values,
or structured log field values in production telemetry output.

| Category | Examples | Reason |
|----------|---------|--------|
| **Memory content** | `memory.value`, `body`, `content` | Raw text; may contain secrets, PII, proprietary code |
| **Query strings** | `query.text`, `search_query` | User-controlled; unbounded; may contain credentials |
| **Entry keys** | `memory.key`, `entry_id` | User-controlled strings; unbounded cardinality |
| **Session identifiers** | `session_id`, `session_key` | May be treated as PII in regulated environments |
| **Agent identifiers** | `agent_id`, `agent_name` | Potentially user-controlled; unbounded cardinality |
| **DSN / credentials** | `database_url`, `auth_token`, `dsn` | Secrets — never in telemetry under any circumstances |
| **File system paths** | `project_dir`, `db_path` | May contain usernames or sensitive directory layouts |

### Why agent_id and session_id are forbidden as metric labels

These values are user-controlled and form unbounded cardinality spaces.  Attaching
them to metrics will over time create millions of unique time series, degrading or
crashing your metrics backend.  If per-agent visibility is needed, use log correlation
(structured logs with `agent_id` field) rather than metric dimensions.

---

## Log redaction rules

Structured log output from tapps-brain **must not** include raw memory content in
any field value, even at `DEBUG` level.  STORY-061.7 implements a log formatter
that strips or hashes `memory.body` fields.  Until that formatter is deployed,
apply the following rules manually:

1. **Do not log** `entry.content`, `entry.body`, `memory.value`, or any field that
   contains the raw text of a stored memory.
2. **Do not log** raw query strings passed to `recall()` or `search()`.
3. Logging entry *counts*, *tiers*, *scores*, and *timestamps* is safe.
4. Exception tracebacks are safe to log; exception *messages* should be reviewed
   to ensure they do not echo back user-supplied content.

---

## PR review checklist (observability changes)

Any pull request that **adds or modifies telemetry code** (spans, metrics, log
statements, or diagnostic output) must include the following review items.  Add
these to the PR description when applicable:

```
### Telemetry review (required for observability PRs)

- [ ] No raw memory content (`entry.content`, query strings) in span attributes
- [ ] No raw memory content in metric label values
- [ ] No raw memory content in structured log field values
- [ ] All new span names added to `SPAN_*` constants in `otel_tracer.py` and
      referenced in `docs/engineering/system-architecture.md`
- [ ] All new metric label keys are from the bounded allow-list in `otel_exporter.py`
- [ ] No new unbounded string labels (entry keys, session IDs, agent IDs, query text)
- [ ] DSN / secrets never appear in any telemetry path
- [ ] OTel Views registered (if new high-cardinality instrument added — consult
      STORY-061.7 implementation for pattern)
```

Copy this block into the PR description for any change touching:
- `src/tapps_brain/otel_tracer.py`
- `src/tapps_brain/otel_exporter.py`
- `src/tapps_brain/metrics.py`
- `src/tapps_brain/http_adapter.py` (metrics endpoint)
- `src/tapps_brain/store.py` (span calls)
- `src/tapps_brain/agent_brain.py` (span calls)
- Any new module that calls `start_span()` or sets span attributes

---

## Enforcement

1. **Code review** — the PR checklist above is the primary gate.
2. **STORY-061.7 log formatter** — strips `memory.body` from all log records in the
   Python logging pipeline (implementation: `tapps_brain.otel_exporter`).
3. **OTel Views** — STORY-061.7 registers Views that drop forbidden label keys from
   metric instruments before export.
4. **Unit tests** — STORY-061.7 includes a static test that asserts forbidden strings
   never appear in emitted log records under the test harness.

---

## Related

- [`docs/engineering/system-architecture.md`](../engineering/system-architecture.md) — canonical span names
- [`src/tapps_brain/otel_tracer.py`](../../src/tapps_brain/otel_tracer.py) — `SPAN_*` constants + `start_span()` docstring
- [`src/tapps_brain/otel_exporter.py`](../../src/tapps_brain/otel_exporter.py) — metric dimension allow-list
- [`docs/operations/k8s-probes.md`](k8s-probes.md) — liveness / readiness probes
- STORY-061.7 — log formatter + OTel Views enforcement
- STORY-061.8 — operator runbook (alert thresholds, dashboard guidance)
- [EPIC-061](../planning/epics/EPIC-061.md) — observability epic
- [EPIC-063](../planning/epics/EPIC-063.md) — trust boundaries (DSN hygiene, RLS)
