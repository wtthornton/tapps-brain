# AgentForge Integration Guide (v3)

How any agent host — AgentForge, custom orchestrators, or bare Python scripts —
connects to tapps-brain's Postgres-backed memory.

> **v3 only:** This guide assumes Postgres exclusively.  There is no SQLite
> Hive or SQLite Federation in v3.  See
> [ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md) for rationale.

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
export TAPPS_BRAIN_DATABASE_URL="postgres://tapps:tapps@localhost:5432/tapps"

# Required if using Hive (cross-agent memory sharing)
export TAPPS_BRAIN_HIVE_DSN="postgres://tapps:tapps@localhost:5432/tapps_hive"

# Recommended for production
export TAPPS_BRAIN_STRICT=1          # raise BrainConfigError if DSN is missing
export TAPPS_BRAIN_HIVE_AUTO_MIGRATE=true  # apply pending migrations on startup

# Optional — Hive groups and expert domains
export TAPPS_BRAIN_GROUPS="security,testing"
export TAPPS_BRAIN_EXPERT_DOMAINS="sql,python"
```

See [Agent integration guide — environment variables](agent-integration.md#environment-variables)
for the full table, including pool sizing and health JSON fields.

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
curl -s http://localhost:9090/ready | jq .
# {
#   "status": "ok",
#   "hive_migration_version": 3,
#   "pool_saturation": 0.12
# }
```

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

## Connecting to an existing Hive

If you have a separate tapps-brain Hive running (e.g. `docker-tapps-hive-db-1`
on an external network), bridge the network once and point your DSN at it:

```bash
# If using Docker networks — bridge your container to the Hive network
docker network connect docker_default my-agentforge-container

# Then in your .env:
TAPPS_BRAIN_HIVE_DSN=postgres://tapps:tapps@docker-tapps-hive-db-1:5432/tapps_hive
```

For full Hive setup, external networks, and troubleshooting see
[hive-deployment.md](hive-deployment.md).

---

## Non-goals

This integration guide intentionally **does not** cover:

- **Full REST memory API** — tapps-brain exposes `AgentBrain` (Python) and MCP
  tools as the primary memory surface.  There is no duplicate REST memory API
  for list/get/create/delete operations.  Use the MCP server for IDE-connected
  agents; use `AgentBrain` for in-process Python agents.  See
  [HTTP adapter ADR](../planning/adr/ADR-009-no-http-without-mcp-library-parity.md)
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
