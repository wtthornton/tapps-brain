---
id: EPIC-076
title: "Dev/deploy stack isolation and brain-ops hardening"
status: active
priority: high
created: 2026-06-09
target_date: 2026-06-23
linear_epic: TAP-3176
tags: [docker, devops, mcp, testing, docs]
depends_on: [EPIC-073]
blocks: []
---

# EPIC-076: Dev/deploy stack isolation and brain-ops hardening

## Context

**Incident (2026-06-09):** After Cursor MCP restart, `make brain-healthcheck` reported
Postgres `password authentication failed for user "tapps_runtime"` and MCP initialize
failures. Root cause was **not** a stale `TAPPS_BRAIN_RUNTIME_PASSWORD` — the dev
quick-start Postgres (`docker-compose.yml`) and the deployed hive stack
(`docker/docker-compose.hive.yaml`) both use Docker Compose project name
`tapps-brain` and service hostname `tapps-brain-db`.

When `make brain-up` runs while the hive stack is up, container `tapps-brain-dev-db`
joins network `tapps-brain_default` with DNS alias `tapps-brain-db`. `tapps-brain-http`
then connects to the **dev** database (`tapps/tapps`, `tapps_brain_dev`) instead of
the hive database (`tapps_runtime`, `tapps_brain`). Symptoms mimic credential drift:

- `/ready` → 503 (`db_error`)
- MCP `initialize` → 500 (profile resolver → pool exhaustion after prolonged failures)
- `project register` → `PoolTimeout`

Repair required stopping the dev DB, `make hive-up`, project re-registration, and
`docker restart tapps-brain-http`.

Secondary gaps surfaced during the same session:

| Gap | Impact |
|-----|--------|
| No guard when `brain-up` and `hive-up` both target `tapps-brain` network | Silent production outage |
| `brain-healthcheck` warns on `memory_*` tools when `X-Brain-Profile: coder` | False warnings; masks real failures |
| `scripts/wire-repo-to-brain.sh` documented but not implemented | Manual 7-step setup error-prone |
| Project registration not part of `hive-deploy` | Strict mode (`TAPPS_BRAIN_STRICT=1`) blocks MCP until manual `project register` |
| `tests/integration/test_profile_filter.py` golden tool counts stale post EPIC-075 | CI/integration drift |
| No runbook for “MCP 500 after DB outage” | Long recovery; restart required |

## Success Criteria

- [x] `make brain-up` and `make hive-up` cannot silently hijack each other's Postgres hostname on the same host (STORY-076.1 / TAP-3177)
- [x] `make brain-healthcheck` exits 0 on a healthy hive stack with `coder` profile wiring (no false `memory_*` warnings) (STORY-076.3 / TAP-3178)
- [ ] `make hive-deploy` leaves MCP callable for a registered project without manual CLI steps (for this repo: `tapps-brain`)
- [ ] Documented recovery path for pool-exhaustion / degraded `/ready` after transient DB failures
- [ ] `test_profile_filter.py` counts match current tool registry

## Stories

### STORY-076.1: Eliminate dev/hive Docker DNS collision

**Status:** done  
**Linear:** [TAP-3177](https://linear.app/tappscodingagents/issue/TAP-3177)  
**Effort:** M  
**Depends on:** none  
**Context refs:** `docker-compose.yml`, `docker/docker-compose.hive.yaml`, `Makefile`  
**Verification:** `docker network inspect` shows distinct DB hostnames; `curl /ready` stays 200 after `make brain-up` while hive stack runs

#### Why

Two compose files must coexist on one developer machine without cross-wiring.

#### Acceptance Criteria

- [ ] Dev quick-start uses a **different Compose project name** (e.g. `tapps-brain-dev`) **or** renames its service hostname so it never publishes DNS alias `tapps-brain-db` on `tapps-brain_default`
- [ ] `TAPPS_DEV_DSN` / `Makefile` targets updated if host/port/project changes
- [ ] `AGENTS.md` and `docs/guides/postgres-dsn.md` document the split
- [ ] CI unchanged (service container, not local compose collision)

#### Recommended approach

Prefer **separate Compose project** for dev (`docker compose -p tapps-brain-dev …`) so hive keeps `tapps-brain_default` and AgentForge DNS expectations (`tapps-brain-db`) stay stable. Dev DB can keep publishing `localhost:5432` under project `tapps-brain-dev`.

---

### STORY-076.2: Mutual-exclusion guard in Makefile

**Status:** done  
**Linear:** [TAP-3179](https://linear.app/tappscodingagents/issue/TAP-3179)  
**Effort:** S  
**Depends on:** STORY-076.1  
**Verification:** `make brain-up` prints clear error when hive `tapps-brain-db` is healthy; `make hive-up` warns when dev DB would conflict

#### Acceptance Criteria

- [ ] `brain-up` preflight: if hive `tapps-brain-db` container is running, abort with actionable message (or `--force` to continue when 076.1 guarantees isolation)
- [ ] `hive-up` / `hive-deploy` preflight: if dev DB still holds alias `tapps-brain-db` on `tapps-brain_default`, abort with fix instructions
- [ ] `make help` mentions the constraint in one line

---

### STORY-076.3: Profile-aware `brain-healthcheck`

**Status:** done  
**Linear:** [TAP-3178](https://linear.app/tappscodingagents/issue/TAP-3178)  
**Effort:** S  
**Depends on:** EPIC-073 (done)  
**Context refs:** `scripts/brain-healthcheck.sh`, `.mcp.json`, `.cursor/mcp.json`  
**Verification:** `bash scripts/brain-healthcheck.sh` exits 0 with coder profile; still validates `full` when header absent

#### Acceptance Criteria

- [ ] Read `X-Brain-Profile` from `.mcp.json` (and `.cursor/mcp.json` when present); default check set = `coder` tools (`brain_recall`, `brain_remember`, …) not legacy `memory_*` list
- [ ] When profile is `full`, retain current `memory_*` expectations
- [ ] Optional `--profile` CLI flag overrides detection
- [ ] Summary distinguishes **warnings** (optional tools) from **failures**

---

### STORY-076.4: Post-outage recovery — pool saturation and `/ready`

**Status:** planned  
**Effort:** M  
**Depends on:** none  
**Context refs:** `src/tapps_brain/postgres_connection.py`, `src/tapps_brain/http_adapter.py`, `docker/docker-compose.hive.yaml` healthcheck  
**Verification:** simulate DB unreachable 60s → restore → `/ready` 200 without manual `docker restart` (or document single-command recovery)

#### Acceptance Criteria

- [ ] Investigate `psycopg_pool.TooManyRequests` after sustained connection failures; add bounded wait / pool reset / circuit behavior **or** document that `docker restart tapps-brain-http` is the supported recovery and add it to `brain-healthcheck` failure hints
- [ ] `/ready` body includes distinguishable `db_error` vs `pool_saturated` (if code change)
- [ ] `docs/guides/hive-deployment.md` § Troubleshooting covers the 2026-06-09 incident pattern

---

### STORY-076.5: Implement `scripts/wire-repo-to-brain.sh`

**Status:** planned  
**Effort:** M  
**Depends on:** STORY-076.1  
**Context refs:** `docs/guides/mcp-client-repo-setup.md` (installer outline)  
**Verification:** dry-run on temp dir produces `.env`, `.envrc`, `.mcp.json`; idempotent re-run

#### Acceptance Criteria

- [ ] Args: `<project-slug>` `[--profile repo-brain|path]` `[--agent-id NAME]` `[--dry-run]`
- [ ] Steps 1–7 from `mcp-client-repo-setup.md` automated (register, `.env`, gitignore, `.envrc`, `.mcp.json`, optional `CLAUDE.md` block append)
- [ ] Refuses to overwrite existing `.env` without `--force`
- [ ] Prints "restart MCP client" reminder and runs `make brain-healthcheck` when not `--dry-run`

---

### STORY-076.6: Register default project in `hive-deploy`

**Status:** planned  
**Effort:** S  
**Depends on:** STORY-076.5 (optional; can inline for `tapps-brain` only)  
**Verification:** fresh `make hive-deploy` → `brain-healthcheck` project registration OK without manual `docker exec`

#### Acceptance Criteria

- [ ] `hive-deploy` / post-migrate hook registers `tapps-brain` (this repo) or reads `TAPPS_BRAIN_DEFAULT_PROJECT_ID` from `docker/.env.example`
- [ ] Idempotent: re-register does not fail
- [ ] Documented in `docker/README.md` and `hive-deployment.md`

---

### STORY-076.7: Refresh `test_profile_filter.py` golden counts

**Status:** planned  
**Effort:** S  
**Depends on:** EPIC-075 (done)  
**Context refs:** `tests/integration/test_profile_filter.py`  
**Verification:** `pytest tests/integration/test_profile_filter.py -v -m requires_postgres`

#### Acceptance Criteria

- [ ] Update registered/callable tool count assertions to match EPIC-075 registry (was 77→80 registered, 64→67 callable at time of incident)
- [ ] Prefer deriving expected counts from `ProfileRegistry` in-test rather than hard-coded integers (reduces future drift)

---

### STORY-076.8: Operator runbook and doc cross-links

**Status:** planned  
**Effort:** S  
**Depends on:** STORY-076.1, STORY-076.3  
**Verification:** docs review only

#### Acceptance Criteria

- [ ] `docs/guides/getting-started.md` — "Do not run `make brain-up` while hive stack is deployed" callout
- [ ] `docs/guides/hive-deployment.md` — troubleshooting table: DNS collision, pool exhaustion, strict registration
- [ ] `AGENTS.md` Makefile table — note dev vs deploy Postgres targets
- [ ] `docs/planning/next-session-prompt.md` — link EPIC-076 when filed in Linear

---

## Priority Order

| Order | Story | Rationale |
|-------|-------|-----------|
| 1 | 076.1 | Eliminates root cause of production MCP outage on dev machines |
| 2 | 076.2 | Cheap safety net until 076.1 is everywhere |
| 3 | 076.3 | Restores signal on `brain-healthcheck` (EPIC-073 rollout depends on it) |
| 4 | 076.6 | Removes manual registration step for dogfooding repo |
| 5 | 076.4 | Shortens incident recovery time |
| 6 | 076.5 | Scales client wiring beyond tapps-brain |
| 7 | 076.7 | CI hygiene |
| 8 | 076.8 | Keeps humans/agents out of the same trap |

## Out of Scope

- Changing AgentForge compose project naming (consumers expect `tapps-brain_default`)
- Merging dev and hive into one Postgres instance (different roles, schemas, and lifecycles)
- EPIC-073 Phase 3 default profile flip (`TAPPS_BRAIN_DEFAULT_PROFILE=coder`) — still gated on metrics per EPIC-073

## Linear

| Story | Linear ID |
|-------|-----------|
| Epic | [TAP-3176](https://linear.app/tappscodingagents/issue/TAP-3176) |
| 076.1 DNS isolation | [TAP-3177](https://linear.app/tappscodingagents/issue/TAP-3177) — Done |
| 076.2 Makefile guards | [TAP-3179](https://linear.app/tappscodingagents/issue/TAP-3179) |
| 076.3 Profile healthcheck | [TAP-3178](https://linear.app/tappscodingagents/issue/TAP-3178) |
| 076.4 Pool recovery | [TAP-3180](https://linear.app/tappscodingagents/issue/TAP-3180) |
| 076.5 wire-repo script | [TAP-3181](https://linear.app/tappscodingagents/issue/TAP-3181) |
| 076.6 hive-deploy register | [TAP-3182](https://linear.app/tappscodingagents/issue/TAP-3182) |
| 076.7 test_profile_filter | [TAP-3183](https://linear.app/tappscodingagents/issue/TAP-3183) |
| 076.8 Docs runbook | [TAP-3184](https://linear.app/tappscodingagents/issue/TAP-3184) |
