# Agent integration guide

This page is the **operator contract** for AI agents using tapps-brain: the
`AgentBrain` Python API surface, environment-variable reference, and how to
handle empty recall.

---

## AgentBrain — Python API (EPIC-057, v3)

`AgentBrain` is the recommended entry point for Python-based agents.  It wraps
`MemoryStore` and `HiveBackend` creation, env-var resolution, and lifecycle
management into a single five-method facade.  Agents and LLMs use this class
directly — they never import `MemoryStore` or backend factories.

### Quick start

```python
from tapps_brain import AgentBrain

with AgentBrain(agent_id="frontend-dev", project_dir="/app") as brain:
    brain.remember("Use Tailwind for styling", tier="architectural")
    results = brain.recall("how to style components?")
    brain.learn_from_success("Styled the sidebar component")
```

---

### Constructor

```python
AgentBrain(
    agent_id: str | None = None,
    project_dir: str | Path | None = None,
    *,
    groups: list[str] | None = None,
    expert_domains: list[str] | None = None,
    profile: str = "repo-brain",
    hive_dsn: str | None = None,
    encryption_key: str | None = None,
)
```

All parameters are optional; most can be supplied via environment variables
instead (see [Environment variables](#environment-variables) below).

| Parameter | Type | Env var | Description |
|-----------|------|---------|-------------|
| `agent_id` | `str \| None` | `TAPPS_BRAIN_AGENT_ID` | Agent identity — scopes private memory rows and Hive propagation. |
| `project_dir` | `str \| Path \| None` | `TAPPS_BRAIN_PROJECT_DIR` | Project root. Defaults to `cwd`. Used to derive the stable `project_id`. |
| `groups` | `list[str] \| None` | `TAPPS_BRAIN_GROUPS` (CSV) | Group memberships for Hive group propagation. |
| `expert_domains` | `list[str] \| None` | `TAPPS_BRAIN_EXPERT_DOMAINS` (CSV) | Expert domains for auto-publish to Hive. |
| `profile` | `str` | — | Built-in profile name (default `"repo-brain"`). See [Profile catalog](profile-catalog.md). |
| `hive_dsn` | `str \| None` | `TAPPS_BRAIN_HIVE_DSN` | Hive shared-store DSN. Must be `postgres://` or `postgresql://`. |
| `encryption_key` | `str \| None` | — | Optional SQLCipher key (v2 legacy compat). |

---

### Public methods

#### `remember(fact, *, tier, share, share_with) → str`

Save a memory. Returns the generated key (SHA-256 slug).

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `fact` | `str` | required | The content to store. |
| `tier` | `str` | `"procedural"` | Memory tier (`"architectural"`, `"pattern"`, `"procedural"`, `"context"`). |
| `share` | `bool` | `False` | If `True`, propagate to all declared groups on the Hive. |
| `share_with` | `str \| list[str] \| None` | `None` | Target group name, `"hive"` (global), or list of group names. |

```python
key = brain.remember("Use Tailwind for styling", tier="architectural")
brain.remember("PR #42 merged", share=True)               # → all groups
brain.remember("CSS decision", share_with="frontend")      # → one group
brain.remember("Design token", share_with="hive")          # → global Hive
```

---

#### `recall(query, *, max_results, scope) → list[dict]`

Recall memories matching `query`. Returns a list of result dicts (keys:
`key`, `value`, `tier`, `confidence`, `tags`).

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `query` | `str` | required | Free-text search query. |
| `max_results` | `int` | `5` | Maximum number of results to return. |
| `scope` | `str` | `"all"` | Result scope filter (passed through to the retriever). |

```python
results = brain.recall("how to style components?", max_results=10)
for r in results:
    print(r["value"])
```

The most recently returned keys are tracked internally and used by
`learn_from_success` to reinforce the right entries.

---

#### `forget(key) → bool`

Archive a memory by key. Returns `True` if found, `False` if not found.

```python
removed = brain.forget("tailwind-styling-abc123")
```

---

#### `set_task_context(task_id, session_id) → None`

Set the current task context for subsequent `learn_from_success` /
`learn_from_failure` calls. Optional — both methods also accept `task_id`
directly.

| Argument | Type | Description |
|----------|------|-------------|
| `task_id` | `str` | Opaque task identifier (stored as a tag on success/failure entries). |
| `session_id` | `str \| None` | Optional session identifier. |

---

#### `learn_from_success(task_description, *, task_id, boost) → None`

Record a successful task outcome. Saves the experience and reinforces any
recently recalled memories (from the last `recall` call).

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `task_description` | `str` | required | What was accomplished. |
| `task_id` | `str \| None` | `None` | Override the task ID from `set_task_context`. |
| `boost` | `float` | `0.1` | Confidence boost applied to recalled entries. |

```python
brain.set_task_context("TASK-42")
results = brain.recall("sidebar styling")
# ... do the work ...
brain.learn_from_success("Styled the sidebar with Tailwind")
```

---

#### `learn_from_failure(description, *, task_id, error) → None`

Record a failed task outcome to avoid repeating mistakes.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `description` | `str` | required | What failed. |
| `task_id` | `str \| None` | `None` | Override the task ID from `set_task_context`. |
| `error` | `str \| None` | `None` | Optional error string appended to the stored value. |

```python
brain.learn_from_failure(
    "Tried CSS Grid but it broke IE11 compat",
    error="Grid gaps not supported in IE11",
)
```

---

#### `close() → None`

Close the underlying store and Hive backend. Called automatically when used
as a context manager.

```python
brain = AgentBrain(agent_id="planner", project_dir="/app")
try:
    brain.remember("planning fact")
finally:
    brain.close()
```

---

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `agent_id` | `str \| None` | The resolved agent identity. |
| `store` | `MemoryStore` | The underlying `MemoryStore` (advanced use only). |
| `hive` | `HiveBackend \| None` | The Hive backend, or `None` when no DSN is set. |
| `groups` | `list[str]` | Declared group memberships. |
| `expert_domains` | `list[str]` | Declared expert domains. |

---

### Context manager

`AgentBrain` implements the context manager protocol. Use it with `with` to
ensure the store and Hive connection are closed cleanly:

```python
with AgentBrain(agent_id="planner", project_dir="/app") as brain:
    brain.remember("Use Postgres for shared state")
    results = brain.recall("database choice")
# store + Hive closed automatically
```

---

## Environment variables

All agent-identity and connection variables are listed below. For connection
pool sizing, health JSON fields, and DSN format details, see
[PostgreSQL DSN & Connection Pool Reference](postgres-dsn.md).

| Variable | Example | Required (prod) | Description |
|----------|---------|-----------------|-------------|
| `TAPPS_BRAIN_AGENT_ID` | `claude-code` | ✅ | Agent identity string. Scopes private memory rows and Hive propagation. |
| `TAPPS_BRAIN_PROJECT_DIR` | `/home/user/myrepo` | ✅ | Project root — used to derive the stable `project_id` hash. Defaults to `cwd`. |
| `TAPPS_BRAIN_DATABASE_URL` | `postgres://tapps:s3cr3t@db:5432/tapps` | ✅ (v3) | Unified v3 DSN for private memory + Hive fallback. `postgres://` or `postgresql://` required. |
| `TAPPS_BRAIN_HIVE_DSN` | `postgres://tapps:s3cr3t@db:5432/tapps_hive` | ✅ if using Hive | Hive shared-store DSN. Falls back to `TAPPS_BRAIN_DATABASE_URL`. |
| `TAPPS_BRAIN_FEDERATION_DSN` | `postgres://tapps:s3cr3t@db:5432/tapps_fed` | if using federation | Cross-project federation DSN. |
| `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` | `true` | ✅ first deploy | Set `true` to run pending Hive schema migrations on startup. |
| `TAPPS_BRAIN_GROUPS` | `dev-pipeline,frontend-guild` | if using groups | Comma-separated group memberships for Hive propagation. |
| `TAPPS_BRAIN_EXPERT_DOMAINS` | `css,react` | if using expert publish | Comma-separated expert domains for auto-publish to Hive. |
| `TAPPS_BRAIN_STRICT` | `1` | ✅ production | When `1`, a missing DSN raises an error instead of silently skipping Postgres. |

> **v3 note:** tapps-brain v3 requires a Postgres DSN for any shared
> (Hive/Federation) or private-backend storage. There is no SQLite fallback
> for shared stores. Without a DSN, `AgentBrain` operates in local-only mode
> using only in-memory state (no persistence). Set `TAPPS_BRAIN_STRICT=1` in
> production to catch misconfigured deployments at startup.

---

## Declaring groups and expert domains

```python
brain = AgentBrain(
    agent_id="css-specialist",
    project_dir="/app",
    groups=["dev-pipeline", "frontend"],
    expert_domains=["css", "tailwind"],
)
```

Or via environment variables:

```bash
export TAPPS_BRAIN_GROUPS="dev-pipeline,frontend"
export TAPPS_BRAIN_EXPERT_DOMAINS="css,tailwind"
```

---

## Testing

In unit tests, pass `project_dir=tmp_path` (from pytest) and no `hive_dsn`.
The store operates in memory without Postgres:

```python
def test_my_agent(tmp_path):
    with AgentBrain(agent_id="test", project_dir=tmp_path) as brain:
        brain.remember("test fact")
        results = brain.recall("test")
        assert len(results) >= 1
```

For integration tests that exercise Hive or private-backend persistence, set
`TAPPS_BRAIN_DATABASE_URL` to a real Postgres DSN (e.g. from the
`docker-compose.yml` service container):

```python
import os, pytest

@pytest.mark.integration
def test_hive_roundtrip(tmp_path):
    dsn = os.environ["TAPPS_BRAIN_DATABASE_URL"]
    with AgentBrain(agent_id="test", project_dir=tmp_path, hive_dsn=dsn) as brain:
        brain.remember("shared fact", share=True)
        results = brain.recall("shared")
        assert any("shared fact" in r["value"] for r in results)
```

---

## Versions and profile

| Signal | Where |
|--------|--------|
| **PyPI / package version** | `importlib.metadata.version("tapps-brain")`, CLI `tapps-brain --version`, or `StoreHealthReport.package_version` from `maintenance health` / `memory://stats` / `memory://health` |
| **Hive schema version** | `StoreHealthReport.hive_migration_version`, `/ready` health endpoint |
| **Active profile** | `StoreHealthReport.profile_name`, MCP `profile_info`, resource `memory://agent-contract` |
| **Profile seed recipe label** | `StoreHealthReport.profile_seed_version` (when `profile.seeding.seed_version` is set) |

Always pin the **package version** in your repo's `AGENTS.md` (or equivalent)
so agents do not follow stale instructions.

---

## Writing memory

| Path | Command / tool |
|------|----------------|
| **MCP** | `memory_save` — primary path for assistants |
| **CLI** | `tapps-brain memory save KEY "value" [--tier …] [--tag …] [--group …]` |
| **Bulk file** | `tapps-brain import data.json` — array of entries |
| **Python** | `AgentBrain.remember(...)` or `MemoryStore.save(...)` |

---

## Reading / recall

| Path | Use when |
|------|-----------|
| `memory_search` | Full-text search with optional tier/scope/group filters |
| `memory_recall` | Ranked, injection-oriented bundle (`memory_section` + `memories`) |
| `memory_list` / `memory_get` | Browse or fetch by key |
| `AgentBrain.recall(...)` | Python API — ranked results as list of dicts |

### Empty `memory_recall`

When `memory_count` is `0`, check **`recall_diagnostics`** in the JSON response:

| `empty_reason` | Meaning |
|----------------|---------|
| `engagement_low` | Injection disabled for low engagement (orchestrator / client config) |
| `search_failed` | Retriever raised; check server logs |
| `store_empty` | No entries in the visible store |
| `group_empty` | No entries in the requested `group` (project-local `memory_group`) |
| `no_ranked_matches` | Store has rows but retriever returned nothing for this query |
| `below_score_threshold` | Candidates existed but all below the composite score cutoff |
| `rag_safety_blocked` | Candidates existed but values failed RAG safety checks |
| `post_filter_excluded` | Local/Hive results removed by orchestrator scope/tier/branch/dedupe filters |

Fields **`retriever_hits`** and **`visible_entries`** add context (see `RecallDiagnostics` in `models.py`).

---

## Tiers vs profile layers

- **Canonical enum tiers** (`architectural`, `pattern`, `procedural`, `context`, …) are always valid for decay and storage.
- **Profile layer names** (e.g. `identity`, `long-term`, `short-term` on `personal-assistant`) are also valid **when that profile is active**.
- Saves normalize aliases via `tier_normalize` (e.g. `long-term` → `architectural` where applicable).

See [Memory scopes](memory-scopes.md) and [Profile catalog](profile-catalog.md).

---

## Machine-readable surfaces

| Artifact | Purpose |
|----------|---------|
| `memory://agent-contract` | One JSON blob: versions, profile layers, canonical tiers, empty-reason codes, doc links |
| `docs/generated/mcp-tools-manifest.json` | `tool_count` / `resource_count`, tool names + resource URIs + short descriptions (regenerate: `python scripts/generate_mcp_tool_manifest.py`) |

---

---

## Exception taxonomy

tapps-brain raises a typed exception hierarchy so callers can distinguish
configuration bugs from transient infrastructure failures from input
validation errors.

```
BrainError                     # base — catch this to handle any tapps-brain error
├── BrainConfigError           # bad env/constructor config; operator fix required
├── BrainTransientError        # transient infra failure; retry may help
└── BrainValidationError       # invalid caller-supplied value; code fix required
    (also inherits ValueError)
```

Import from the package root:

```python
from tapps_brain import BrainError, BrainConfigError, BrainTransientError, BrainValidationError
```

### `BrainConfigError` — configuration problems

Raised at construction time or on first use when the agent cannot start due
to a misconfiguration that requires an operator fix:

| Situation | How to resolve |
|-----------|---------------|
| Missing `TAPPS_BRAIN_DATABASE_URL` with `TAPPS_BRAIN_STRICT=1` | Set the env var before starting |
| Non-Postgres DSN (e.g. `sqlite://…`) passed to `hive_dsn` | Use a `postgres://` or `postgresql://` DSN |
| Unknown profile name | Check [Profile catalog](profile-catalog.md) for valid names |

```python
from tapps_brain import AgentBrain, BrainConfigError

try:
    brain = AgentBrain(agent_id="planner", hive_dsn="sqlite:///bad.db")
except BrainConfigError as exc:
    # fix the DSN and restart — retrying without a fix won't help
    raise SystemExit(f"tapps-brain config error: {exc}") from exc
```

### `BrainTransientError` — transient infrastructure failures

Raised when an operation fails due to a transient infrastructure problem that
*may* resolve on retry:

| Situation | Recovery |
|-----------|----------|
| Postgres connection refused | Wait and retry with back-off |
| Connection pool exhausted | Retry; reduce concurrency; increase pool size |
| Network error during Hive propagation | Retry; alert if failures persist |

```python
from tapps_brain import AgentBrain, BrainTransientError
import time

brain = AgentBrain(agent_id="planner", project_dir="/app")
for attempt in range(3):
    try:
        brain.remember("Use Postgres for shared state")
        break
    except BrainTransientError:
        if attempt == 2:
            raise
        time.sleep(2 ** attempt)
```

### `BrainValidationError` — invalid caller-supplied values

Raised when a caller-supplied value fails validation.  These will not resolve
without a code change:

| Situation | Resolution |
|-----------|-----------|
| `tier` is not a canonical value | Use `"architectural"`, `"pattern"`, `"procedural"`, or `"context"` |
| `share_with` is an empty string | Pass `None` or a non-empty group name |
| `max_results` is non-positive | Use a positive integer |

`BrainValidationError` also inherits from `ValueError`, so existing code that
catches `ValueError` continues to work without changes.

### Catching all tapps-brain errors

```python
from tapps_brain import BrainError

try:
    brain.remember("some fact", tier="bad-tier")
except BrainError as exc:
    logger.error("tapps-brain error", error=str(exc), type=type(exc).__name__)
```

---

## v3 breaking changes

tapps-brain v3 is a **greenfield** release.  The following v2 behaviors and
APIs were removed or changed.

### Postgres required — no SQLite fallback for shared stores

v3 uses **PostgreSQL exclusively** for all shared (Hive, Federation) and
private-backend storage.  There is no SQLite fallback.

| v2 | v3 replacement |
|----|----------------|
| `HiveStore(hive_dir=…)` (SQLite) | `create_hive_backend("postgres://…")` |
| `FederatedStore(hub_dir=…)` (SQLite) | `create_federation_backend("postgres://…")` |
| `.tapps-brain/agents/<id>/memory.db` layout | Private rows keyed by `(project_id, agent_id)` in Postgres |
| `sqlite://` DSN accepted | **Rejected at startup** — must be `postgres://` or `postgresql://` |

See [ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md) for
rationale.

### Removed classes

| Removed | v3 replacement |
|---------|---------------|
| `HiveStore` | `PostgresHiveBackend` (via `create_hive_backend`) |
| `FederatedStore` | `PostgresFederationBackend` (via `create_federation_backend`) |
| `FederationConfig` | Constructor arguments to `create_federation_backend` |
| `SqliteHiveBackend` | `PostgresHiveBackend` |
| `SqliteFederationBackend` | `PostgresFederationBackend` |

### New required environment variables

| Variable | Required when |
|----------|--------------|
| `TAPPS_BRAIN_DATABASE_URL` | Private Postgres backend (v3 default) |
| `TAPPS_BRAIN_HIVE_DSN` | Cross-agent Hive sharing |
| `TAPPS_BRAIN_FEDERATION_DSN` | Cross-project federation |

Set `TAPPS_BRAIN_STRICT=1` in production so a missing DSN raises
`BrainConfigError` at startup instead of silently running without persistence.

### No local database files

v3 stores private agent memory in Postgres (`TAPPS_BRAIN_DATABASE_URL`).
There is no `.tapps-brain/agents/` directory created in the project root.
If you previously backed up or inspected SQLite `.db` files, switch to
Postgres tooling (`pg_dump`, `psql`).

### Migration path from v2

1. Stand up a Postgres instance with pgvector (see
   [Hive deployment guide](hive-deployment.md) or the repo `docker-compose.yml`).
2. Set `TAPPS_BRAIN_DATABASE_URL` to a `postgres://` DSN.
3. Replace any `HiveStore()` / `FederatedStore()` construction with factory
   calls from `backends.py`.
4. Remove any `sqlite://` DSNs from your config.
5. Set `TAPPS_BRAIN_HIVE_AUTO_MIGRATE=true` on first deploy.
6. Set `TAPPS_BRAIN_STRICT=1` to catch misconfiguration early.

---

## Related docs

- [PostgreSQL DSN & Connection Pool Reference](postgres-dsn.md) — full env-var table, pool sizing, health JSON
- [Hive guide](hive.md) — cross-agent memory sharing
- [MCP server](mcp.md) — setup and transport
- [OpenClaw](openclaw.md) — plugin and hooks
- [Getting started](getting-started.md)
- [Profile catalog](profile-catalog.md)
- [Memory scopes](memory-scopes.md)
- [ADR-007: Postgres-only, no SQLite](../planning/adr/ADR-007-postgres-only-no-sqlite.md)
