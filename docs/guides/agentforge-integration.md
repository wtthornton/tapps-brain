# AgentForge Integration Guide (v3)

How any agent host — AgentForge, custom orchestrators, or bare Python scripts —
connects to tapps-brain's Postgres-backed memory.

> **v3 only:** This guide assumes Postgres exclusively.  There is no SQLite
> Hive or SQLite Federation in v3.  See
> [ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md) for rationale.

---

## What's new in v3.25.0 for AgentForge

The EPIC-078 release closes the REST DX gaps exposed during AgentForge 4.37.0
integration: same-transaction edge key resolution, batch entity resolve, and
explicit consumer contracts for remember tiers and experience wire format.

| Surface | Change | Why AgentForge cares |
|---|---|---|
| `POST /v1/experience` `EdgeSpec` | **`subject_key` / `object_key`** (and optional **`subject_ref` / `object_ref`**) resolve against entities upserted in the same request — no pre-resolved UUIDs required. (TAP-3248) | Task-completion events can post `entities[{key,type}]` + `edges[{subject_key,object_key,predicate}]` in one round trip. |
| `POST /v1/kg/resolve_entities` | Batch REST endpoint wrapping `resolve_entity_refs` — returns **`entity_ids`** (input order) + per-ref **`results`**. (TAP-3249) | Stop piggybacking key→UUID resolution on `POST /v1/kg/neighbors`. |
| Consumer contract docs | Remember tiers, experience v3.22+ wire format, neighbors `entity_refs` ordering. (TAP-3250) | Surfaces pitfalls AgentForge patched locally (`invalid_tier`, EdgeSpec UUIDs, EvidenceSpec XOR). |

---

## REST consumer contracts (HTTP-only integrators)

These contracts were validated during AgentForge 4.37.0 integration with
tapps-brain-http 3.24.0. Brain behaviour was correct; the gaps were API/DX and
documentation clarity.

### `/v1/remember` — valid `MemoryTier` values

`POST /v1/remember` accepts **`architectural`**, **`pattern`**, **`procedural`**, and
**`context`** only. There is **no `cache` tier** — HTTP response caching belongs in
your client: store fetch results in a **`context`-tier** memory entry with
`metadata` (e.g. `{"http_cache": true, "etag": "...", "fetched_at": "..."}`) via
`/v1/remember`, not as a brain tier label. Posting `tier: "cache"` returns a
validation error (`invalid_tier`).

### `/v1/experience` — v3.22+ wire format

Since **v3.22.4** (TAP-2865/2866/2868):

- **`EntitySpec`** accepts `key`/`type` shorthand → coerced to `canonical_name` /
  `entity_type` (TAP-2675).
- **`EdgeSpec`** accepts pre-resolved UUIDs **or** `subject_key`/`object_key` referencing
  entities in the same event (TAP-3248). Typed disambiguation:
  `subject_ref`/`object_ref` with `entity_type`+`canonical_name` or `type`/`id`.
- **`EvidenceSpec`** requires **exactly one** of `edge_id` or `entity_id` (XOR). Both
  set or neither set → spec skipped, **200 + `warnings`**, not 500 (TAP-2868).
- Malformed side-effects are **non-fatal**: core event commits; inspect
  `warnings: [{kind, index, errors}]` (TAP-2866). Log warnings in your bridge
  (TAP-3196).

Pre-v3.22 clients that posted edges without UUIDs received opaque 500s; upgrade to
3.22.4+ and either supply UUIDs, use key shorthand (3.25+), or accept warnings
while fixing payloads.

### `/v1/kg/neighbors` — `entity_refs` vs `entity_ids`

- Pass **`entity_ids`**: array of UUID strings only. Non-UUID values → **422**
  (`validation_error`), not 500.
- Pass **`entity_refs`**: array of `{entity_type, canonical_name}` or `{type, id}`
  objects; the brain resolves each ref and merges UUIDs before the graph query
  (TAP-3161). Response **`entity_ids`** mirrors the resolved order (explicit UUIDs
  first, then resolved refs).
- For **resolve-only** callers (no neighbourhood needed), use
  **`POST /v1/kg/resolve_entity`** (single) or **`POST /v1/kg/resolve_entities`**
  (batch, TAP-3249) — do not misuse `/v1/kg/neighbors`.

### Related fixes (cross-links)

| Ticket | What |
|---|---|
| [TAP-2865](https://linear.app/tappscodingagents/issue/TAP-2865) | Typed 422 for malformed experience bodies (no masked 500). |
| [TAP-2866](https://linear.app/tappscodingagents/issue/TAP-2866) | Resilient side-effect coercion → 200 + `warnings`. |
| [TAP-3196](https://linear.app/tappscodingagents/issue/TAP-3196) | Log experience write warnings in consumer bridges. |
| [TAP-2725](https://linear.app/tappscodingagents/issue/TAP-2725) | Singular `POST /v1/kg/resolve_entity`. |

---

## What's new in v3.24.0 for AgentForge

The 2026-06-09 release adds a **read path** for `experience_events` so integrators
can query stored metrics without scraping Postgres. Full detail in
[`CHANGELOG.md`](../../CHANGELOG.md#3240--2026-06-09).

| Surface | Change | Why AgentForge cares |
|---|---|---|
| `brain_query_events` MCP tool | New tool — filter by required `event_type`, optional `since`/`until`, optional `entity_id` (`payload.file_path` or `subject_key`). Returns full `payload` JSONB round-trip. `limit` default 100, cap 500. (TAP-3157) | `BrainBridge.query_events()` can migrate off direct SQL for `quality_metric` history. Registered in `full`, `operator` (deferred), `reviewer`. |
| `POST /v1/experience:query` | REST mirror of `brain_query_events` with the same body + `X-Project-Id`. | HTTP-only consumers get metrics without the Python wheel. |
| `EntitySpec` shorthand | `type` → `entity_type`, `id` → `canonical_name` on experience/KG specs. (TAP-3159) | tapps-mcp `quality_metric` writes can use compact entity refs. |
| Migration 023 | Index on `(project_id, event_type, event_time DESC)`. | Keeps file-scoped metric queries fast at scale. |

**Smoke from repo root** (live stack on `:8080`):

```bash
make brain-smoke-live     # canonical post-deploy HTTP gate (record + query)
make brain-healthcheck    # live MCP round-trip (server-mode OK if IDE is bridge-only)
```

---

## What's new in v3.22.4 for AgentForge

The 2026-06-05 release closes the `POST /v1/experience` masked-500 incident
end-to-end. Existing 2xx clients are unaffected; failing payloads now get a
typed error or a 200-with-warning instead of an opaque 500. Per-ticket detail in
[`CHANGELOG.md`](../../CHANGELOG.md).

| Surface | Change | Why AgentForge cares |
|---|---|---|
| `POST /v1/experience` (+ `:batch`) | **Resilient writes** (TAP-2866/2867/2868): a malformed `edges` / `evidence` spec (missing edge UUIDs, evidence with neither/both of `edge_id` / `entity_id`, null `utility_score`) is skipped — the core event still commits and the response is **`200` with a `warnings` array** (`{kind, index, errors}`). | `record_event` no longer fails closed on one bad side-effect. Inspect `warnings` to fix the offending spec; the event is already saved. |
| `POST /v1/experience` malformed body | Returns a typed **422** (`{error, field, detail, errors}`), never a masked 500 (TAP-2865). | Distinguish a client payload bug (4xx) from a brain outage (5xx) without parsing an opaque error. |
| `GET /healthz?deep=1` | New deep probe (TAP-2866): adds `experience_writable` + `experience_detail`, ANDed into `ok`. | A broken core write path can no longer read green. Use `?deep=1` in deployment smoke tests. |
| `/metrics` | New `tapps_brain_http_errors_total{path,status}` counter + `tapps_brain_experience_writable` gauge (TAP-2866). | Alert on `tapps_brain_http_errors_total{status=~"5.."} > 0` to catch a silently-failing data-plane endpoint. |

---

## What's new in v3.19.0 for AgentForge

The 2026-05-17 release lands seven AgentForge-facing contract enrichments. Every
change is a **strict superset** of the previous wire contract — existing 2xx
clients keep working; AgentForge `BrainBridge` gains the fields it has been
asking for. Full per-ticket detail in [`CHANGELOG.md`](../../CHANGELOG.md#3190--2026-05-17).

| Surface | Change | Why AgentForge cares |
|---|---|---|
| `GET /v1/tools/list` | Adds `ETag` (weak), `Cache-Control: public, max-age=300`, `X-Brain-Version`, `X-Catalog-Generated-At` headers + 304 on matching `If-None-Match`. (TAP-1971) | `BrainBridge` stops re-parsing the tool catalog on every probe. Send `If-None-Match: <etag>` and short-circuit on `304 Not Modified`. |
| `out_of_profile` denial `data` | Now carries `suggested_profile: "<name>" \| null` — the smallest profile that exposes the denied tool, excluding the caller's current profile. (TAP-1972) | Agents can self-route: on `-32602` / 403 with `reason: out_of_profile`, switch `X-Brain-Profile` to `data.suggested_profile` and retry instead of grepping `mcp_profiles.yaml`. |
| `brain_record_events_batch` MCP tool | New tool — single round-trip for N events, **per-event transactions** (one bad event does not abort the rest). Response: `{succeeded:[{index,result}], failed:[{index,error,detail}], count, succeeded_count, failed_count}`. Cap at 200 events. (TAP-1973) | Backfill / migration workloads collapse from N round-trips to 1. AgentForge's catalogue-conversion path can ingest 100+ entries in one call. |
| `brain_record_feedback` edge response | When `edge_id` is set, response now includes top-level `confidence: float`, `helpful_count: int`, `misleading_count: int`, and `flagged_for_review: bool`. Memory-feedback path unchanged. (TAP-1975) | Agents emitting `edge_misleading` decide whether the edge crossed their retry threshold without a follow-up `brain_get_neighbors` read. |
| `brain_record_event` / `brain_get_neighbors` / `brain_record_feedback` | Malformed `*_json` arguments now return structured `{"error": "bad_json", "field": "<name>", "detail": "<msg>"}` instead of silently falling back to `{}` / `[]`. Empty string and `"{}"` / `"[]"` still map to empty payloads (back-compat). (TAP-1967/1968/1969) | Operator typos are no longer silent no-ops. See [errors.md § bad_json envelope](errors.md). |
| `GET /healthz` body | Now returns `{ok, db_ok, mcp_ok, queue_depth, circuit_state, brain_version}` instead of `{status, detail}`. HTTP 200/503 semantics unchanged — `curl -f /healthz` still flips correctly. (TAP-1970) | `BrainBridge` / `tapps doctor` can distinguish "DB unreachable" from "MCP cold-starting" from "drain queue flooded" without scraping `/metrics`. |
| Default MCP tool catalog | The `full` and `operator` profiles' default `tools/list` returns **8 tools** (`brain_recall`, `brain_remember`, `brain_status`, `brain_get_neighbors`, `brain_explain_connection`, `memory_search`, `memory_find_related`, `hive_search`). Non-daily-driver tools are `defer_loading: true` — still callable via `tools/call`. Anthropic Tool Search BETA reaches deferred tools on demand via the `advanced-tool-use-2025-11-20` header. (TAP-1985) | AgentForge agents see a smaller eager catalog → smaller context window per probe. Deferred tools remain callable; no behaviour change for code that calls tools by name. |

### Minimum BrainBridge changes to take advantage

Most of the above is free — `BrainBridge` continues to work without code
changes. The two surfaces worth wiring explicitly:

1. **ETag short-circuit on `/v1/tools/list`** — cache the last `ETag` value per
   brain URL; on subsequent probes send `If-None-Match: "<etag>"` and treat
   `304 Not Modified` as "catalog unchanged, reuse the cached payload."
2. **`suggested_profile` self-routing** — on `-32602` (MCP) or 403 (REST) where
   `error.data.reason == "out_of_profile"` and `data.suggested_profile` is set,
   retry the call with `X-Brain-Profile: <suggested_profile>` instead of failing
   to the user.

The structured `bad_json` envelope, edge-feedback state, and phased `/healthz`
shape are response enrichments — read what's useful, ignore the rest.

---

## Architecture overview

```mermaid
sequenceDiagram
    participant Agent
    participant AgentBrain
    participant Postgres

    Agent->>AgentBrain: AgentBrain(agent_id, project_dir, hive_dsn)
    Note over AgentBrain: reads env vars,<br>validates DSN,<br>opens connection pool

    Agent->>AgentBrain: brain.remember("fact", tier="architectural")
    AgentBrain->>Postgres: INSERT private memory row<br>(project_id, agent_id)
    Postgres-->>AgentBrain: ok
    AgentBrain-->>Agent: entry key

    Agent->>AgentBrain: brain.recall("query")
    AgentBrain->>Postgres: BM25 + pgvector search<br>(private + hive rows)
    Postgres-->>AgentBrain: ranked rows
    AgentBrain-->>Agent: list[dict]

    Agent->>AgentBrain: brain.close()
    AgentBrain->>Postgres: release connection pool
```

Every agent talks to **one `AgentBrain` instance** per session.
`AgentBrain` owns the connection pool; there is no global singleton.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Postgres 15+ with **pgvector** | See [Hive deployment guide](hive-deployment.md) for Docker Compose setup |
| `tapps-brain` Python package | `pip install tapps-brain` or `uv add tapps-brain` |
| Database user with DML permissions | `tapps_runtime` role recommended — see [DB roles runbook](../operations/db-roles-runbook.md) *(EPIC-063 — coming soon)* |
| pgvector extension created | `CREATE EXTENSION IF NOT EXISTS vector;` |

---

## Step-by-step integration

### 1 — Stand up Postgres

Use the repo's Compose file (requires Docker):

```bash
# From the tapps-brain repo root (or copy docker/docker-compose.hive.yaml)
docker compose up -d postgres
```

Or set an existing Postgres DSN in your environment.

### 2 — Run migrations

```bash
# Applies hive + private-memory migrations; safe to re-run (idempotent)
TAPPS_BRAIN_DATABASE_URL=postgres://tapps:tapps@localhost:5432/tapps \
TAPPS_BRAIN_HIVE_AUTO_MIGRATE=true \
python -c "from tapps_brain import AgentBrain; AgentBrain(agent_id='migrate', project_dir='.')"
```

Or use the CLI:

```bash
tapps-brain maintenance migrate
```

### 3 — Configure environment variables

Set these before starting your agent host process:

```bash
# Required (v3)
export TAPPS_BRAIN_AGENT_ID="agentforge-main"
export TAPPS_BRAIN_PROJECT_DIR="/home/user/my-project"
# Single DSN — Hive + Federation inherit. In production use the DML-only
# tapps_runtime role (see docs/guides/hive-deployment.md).
export TAPPS_BRAIN_DATABASE_URL="postgres://tapps_runtime:$RT_PW@localhost:5432/tapps_brain"

# Recommended for production
export TAPPS_BRAIN_STRICT=1          # raise BrainConfigError if DSN is missing

# Optional — Hive groups and expert domains
export TAPPS_BRAIN_GROUPS="security,testing"
export TAPPS_BRAIN_EXPERT_DOMAINS="sql,python"
```

See the [**environment variable reference**](postgres-dsn.md) for the complete table
(all variables, defaults, and required/optional for prod vs dev).
A ready-made template is in [`.env.example`](../../.env.example) at the repo root.

### 4 — Initialize AgentBrain in your host

```python
import os
from tapps_brain import AgentBrain, BrainConfigError

try:
    brain = AgentBrain(
        agent_id=os.environ["TAPPS_BRAIN_AGENT_ID"],
        project_dir=os.environ["TAPPS_BRAIN_PROJECT_DIR"],
        # hive_dsn falls back to TAPPS_BRAIN_HIVE_DSN env var automatically
    )
except BrainConfigError as exc:
    raise SystemExit(f"tapps-brain config error: {exc}") from exc
```

Use `AgentBrain` as a **context manager** to ensure the connection pool is
released when the host process exits:

```python
with AgentBrain(
    agent_id="agentforge-main",
    project_dir="/home/user/my-project",
) as brain:
    # host start-up code here
    ...
```

### 5 — First `remember` and `recall`

```python
# Save a fact to private (agent-scoped) memory
key = brain.remember(
    "AgentForge routes prompts to specialist agents via catalogue lookup",
    tier="architectural",
)

# Share with the Hive (all agents in the project can read this)
brain.remember(
    "Security agent handles SQL injection and OWASP top-10 checks",
    tier="pattern",
    share=True,
)

# Recall relevant memories for a prompt
results = brain.recall("how to handle security review requests?", max_results=5)
for r in results:
    print(r["value"])
```

### 6 — Verify health

After startup, check the readiness endpoint (if you have the HTTP adapter
running — see [HTTP adapter reference](http-adapter.md)):

```bash
curl -s http://localhost:8080/healthz | jq .
# v3.19.0+ phased payload (TAP-1970):
# {
#   "ok":            true,
#   "db_ok":         true,
#   "mcp_ok":        true,
#   "queue_depth":   0,
#   "circuit_state": "closed",
#   "brain_version": "3.19.0"
# }
```

HTTP 200/503 semantics are unchanged from the pre-v3.19.0 `{status, detail}`
body — Docker healthchecks (`curl -f /healthz`) still flip correctly. The
phased body lets clients tell "DB unreachable" apart from "MCP cold-starting"
apart from "drain queue flooded" without scraping `/metrics`.

Or call `brain.store.health()` directly in Python:

```python
report = brain.store.health()
print(report.hive_migration_version)
print(report.pool_saturation)
```

---

## Per-agent isolation

Each agent in AgentForge that runs concurrently should have its own
`AgentBrain` instance with a **unique `agent_id`**:

```python
agent_brains: dict[str, AgentBrain] = {}

def get_brain(agent_name: str) -> AgentBrain:
    if agent_name not in agent_brains:
        agent_brains[agent_name] = AgentBrain(
            agent_id=agent_name,
            project_dir=PROJECT_DIR,
        )
    return agent_brains[agent_name]
```

Private memory rows are stored with a `(project_id, agent_id)` composite key
in Postgres — agents cannot read each other's private rows.  Rows shared with
`share=True` or `share_with="hive"` are visible to all agents under the same
`project_id`.

---

## Hive — cross-agent shared memory

If your AgentForge instance runs multiple specialist agents that need to share
knowledge (e.g. security findings, design decisions):

```python
# Agent "security" publishes a finding
security_brain.remember(
    "POST /api/orders does not validate user ownership — IDOR risk",
    tier="pattern",
    share_with="security",   # visible to all agents in the "security" group
)

# Agent "code-review" recalls it
results = code_review_brain.recall("IDOR order endpoint")
# → surfaces the security finding
```

Set `TAPPS_BRAIN_GROUPS` to the groups your agent host participates in.
Set `TAPPS_BRAIN_EXPERT_DOMAINS` to auto-publish relevant memories to the Hive.

See [Hive guide](hive.md) for the full propagation model.

---

## Connecting to an existing tapps-brain deployment

If there's already a tapps-brain stack running on the host (the default `-p tapps-brain` compose project — `tapps-brain-http` container, `tapps-brain_default` network), AgentForge just needs to join that network and point its clients at the brain's HTTP URL. **Do not** connect to the Postgres directly; all memory goes through `http://tapps-brain-http:8080/mcp/` + `/v1/*`.

```yaml
# In AgentForge's compose file — join the brain's network:
networks:
  tapps-brain-net:
    external: true
    name: ${TAPPS_BRAIN_NETWORK:-tapps-brain_default}

services:
  agentforge-main:
    networks: [agentforge-net, tapps-brain-net]
    environment:
      TAPPS_BRAIN_HTTP_URL: http://tapps-brain-http:8080
      TAPPS_BRAIN_AUTH_TOKEN: ${TAPPS_BRAIN_AUTH_TOKEN}
```

`TAPPS_BRAIN_AUTH_TOKEN` comes from `tapps-brain/docker/.env` (the value of the same-named variable) — propagate it to AgentForge's `.env` so the two services agree. `TAPPS_BRAIN_HIVE_DSN` is not needed; Hive is a feature of the brain and reached through the same HTTP surface.

For full brain setup, external networks, and troubleshooting see
[hive-deployment.md](hive-deployment.md).

---

## Non-goals

This integration guide intentionally **does not** cover:

- **Full REST memory API** — tapps-brain exposes `AgentBrain` (Python) and MCP
  tools as the primary memory surface.  There is no duplicate REST memory API
  for list/get/create/delete operations.  Use the MCP server for IDE-connected
  agents; use `AgentBrain` for in-process Python agents.  See
  [HTTP adapter ADR](../planning/adr/ADR-008-no-http-without-mcp-library-parity.md)
  for the parity requirement.
- **AgentForge internal routing** — how AgentForge selects agents for a prompt
  is out of scope; this guide covers only the memory integration layer.
- **SQLite Hive** — removed in v3.  Use a Postgres DSN.
- **Credential storage** — secret injection at agent subprocess execution time
  is an AgentForge concern, not a tapps-brain concern.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `BrainConfigError: DSN required in strict mode` | `TAPPS_BRAIN_STRICT=1` and no DSN set | Set `TAPPS_BRAIN_DATABASE_URL` |
| `BrainConfigError: DSN must be postgres:// ...` | `sqlite://` or empty DSN passed | Use a `postgres://` or `postgresql://` DSN |
| `BrainTransientError: connection refused` | Postgres not running or wrong host | Check `docker compose ps`; verify DSN host/port |
| Pool exhaustion (`pool_saturation ≥ 0.9`) | Too many concurrent agents per pool | Increase `TAPPS_BRAIN_POOL_MAX_SIZE`; or give each agent its own pool |
| Hive recalls empty despite `share=True` | Migration not applied to Hive DB | Set `TAPPS_BRAIN_HIVE_AUTO_MIGRATE=true` and restart |
| Private rows not persisted | `TAPPS_BRAIN_DATABASE_URL` not set | Set the DSN; without it, AgentBrain uses in-memory only |

---

## Related guides

| Guide | What it covers |
|-------|----------------|
| [agent-integration.md](agent-integration.md) | Full `AgentBrain` API reference, env vars, exception taxonomy, v3 breaking changes |
| [hive-deployment.md](hive-deployment.md) | Postgres + pgvector Docker Compose setup, external networks, migration |
| [hive.md](hive.md) | Hive concepts: propagation, groups, expert domains |
| [postgres-dsn.md](postgres-dsn.md) | DSN format, connection pool env vars, health JSON |
| [mcp.md](mcp.md) | MCP server setup — primary tool surface for IDE-connected agents |
| [ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md) | Rationale for Postgres-only backend |
| [fleet-topology.md](fleet-topology.md) | **Deploying at scale** — N FastAPI containers sharing one brain sidecar, wire contract (`X-Project-Id`, bearer tokens), deployment checklist, token lifecycle |
