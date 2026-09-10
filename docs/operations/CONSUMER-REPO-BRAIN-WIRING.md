# Consumer repo: verify tapps-mcp ↔ tapps-brain wiring

Operator and agent checklist for wiring a **consumer LLM coding repo** to the shared tapps-brain memory service via tapps-mcp (bridge-only — agents do not call tapps-brain MCP directly from consumer `.mcp.json`).

**Brain deployment on this host:** [`docs/guides/hive-deployment.md`](../guides/hive-deployment.md), [`docs/guides/dev-docker-loop.md`](../guides/dev-docker-loop.md).

**Runtime troubleshooting:** [MEMORY_REFERENCE.md § Brain health diagnostics](../MEMORY_REFERENCE.md#brain-health-diagnostics).

**Direct brain MCP (this repo / coordinators):** [`docs/guides/mcp-client-repo-setup.md`](../guides/mcp-client-repo-setup.md).

---

## Architecture (non-negotiable)

- Consumer repo MCP config lists **tapps-mcp NLT servers** — not a parallel `tapps-brain` HTTP entry in `.mcp.json` / `.cursor/mcp.json`.
- Memory flows: agent → `uv run tapps-mcp memory` CLI (or `tapps_memory` on `nlt-memory`) → tapps-mcp BrainBridge → tapps-brain HTTP (`http://127.0.0.1:8080`).
- Brain credentials live on the **tapps-mcp env block** / operator secrets — not duplicated in agent prompts.
- Skill to use in consumer repos: `tapps-memory` (not direct brain tools).

`tapps_init` scaffolds HTTP bridge env by default. This guide targets **shared-brain / multi-repo** HTTP wiring.

---

## Prerequisites (host-level, once per machine)

1. tapps-brain HTTP stack is running:

   ```bash
   curl -fsS http://127.0.0.1:8080/healthz | jq '{ok, brain_version, db_ok}'
   ```

   - `brain_version` must be **≥ 3.24.0** (floor enforced by tapps-mcp BrainBridge).

2. Bearer token from brain deployment (`TAPPS_BRAIN_AUTH_TOKEN` in `docker/.env`, or `tapps-brain token create`).

---

## Per-repo setup checklist

### A. Bootstrap tapps-mcp

From the consumer repo root:

1. `tapps_init` (or `tapps-mcp upgrade --host auto --force`).
2. Confirm: `AGENTS.md`, `.tapps-mcp.yaml`, `.cursor/mcp.json`, `tapps-memory` skill.
3. **Regression:** no direct `tapps-brain` MCP server entry. `tapps-mcp doctor` fails on stray entries.

### B. Register this repo on the brain

Project slug must match `X-Project-Id` / `memory.brain_project_id`.

```bash
docker exec tapps-brain-http tapps-brain project register <project_id> \
  --profile /usr/local/lib/python3.13/site-packages/tapps_brain/profiles/repo-brain.yaml \
  --notes "Consumer: <repo-name> via tapps-mcp"
docker exec tapps-brain-http tapps-brain project list | grep <project_id>
```

See [mcp-client-repo-setup.md](../guides/mcp-client-repo-setup.md) for slug rules.

### C. Secrets (gitignored)

**Preferred:** shared operator secrets in `~/.tapps-operator.env` (Context7, brain bearer). Cursor NLT serve wrappers source this file before project `.env`.

**Per-repo `.env`** (`chmod 600`): project-owned keys + optional overrides:

```bash
TAPPS_BRAIN_AUTH_TOKEN=<same token as brain container>
TAPPS_MCP_MEMORY_BRAIN_HTTP_URL=http://127.0.0.1:8080
TAPPS_MCP_MEMORY_BRAIN_AUTH_TOKEN=<same token>
TAPPS_MCP_MEMORY_BRAIN_PROJECT_ID=<project_id>
```

### D. `.tapps-mcp.yaml` memory block

```yaml
memory:
  brain_http_url: http://127.0.0.1:8080
  brain_project_id: <project_id>
```

### E. Verification

```bash
tapps-mcp doctor
uv run tapps-mcp memory save --key wiring-smoke --tier context --value "smoke"
uv run tapps-mcp memory search --query "wiring smoke"
```

Call `tapps_session_start()` → `data.brain_bridge_health.ok == true`.

From this repo against the live stack: `make brain-smoke-live`.

---

## Tenant-scope enforcement (TAP-7243)

Two independent, off-by-default flags on the `tapps-brain-http` container refuse a
data-plane request **before any store is touched** when it would otherwise land in a
literal placeholder or an unregistered tenant:

| Flag | Axis | Refuses when the resolved id is... |
|------|------|-------------------------------------|
| `TAPPS_BRAIN_STRICT_PROJECTS=1` | `X-Project-Id` | absent, one of the literals `default` / `repo-brain` / `api` / `main` (container-profile ids, not real tenants), or not a registered row in `project_profiles` |
| `TAPPS_BRAIN_STRICT_AGENT_ID=1` | `X-Agent-Id` (or the higher-precedence `X-Tapps-Agent`) | absent, or the literal `unknown` |

Both flags are read at request time (no restart needed to toggle) and apply to every
`/v1/*` write route plus the global-scope reads `/v1/recall` and `/v1/kg/neighbors` — a
recall/neighbors call with no project is a global read the tenant contract forbids.
**With both flags unset, behaviour is byte-identical to pre-TAP-7243** — this is a
deploy-time opt-in, not a default.

Registration status (`approved` true/false) is **not** part of this gate — a project
registered via lax-mode auto-registration (`approved=false`) still resolves fine under
`TAPPS_BRAIN_STRICT_PROJECTS=1`; only an *absent* row is refused. Approve a row with
`tapps-brain project approve <slug>` for other reasons (e.g. admin visibility), not to
satisfy this gate.

**One envelope for both axes** — `HTTP 400`:

```json
{
  "ok": false,
  "code": "tenant_project_literal",
  "category": "user_input",
  "retryable": false,
  "remediation": "'default' is a placeholder/container-profile id, not a tenant project. Set X-Project-Id to your project's registered, approved id.",
  "gate": "tenant_scope"
}
```

`code` is one of: `tenant_project_missing`, `tenant_project_literal`,
`tenant_project_unregistered`, `tenant_agent_missing`, `tenant_agent_literal`. Match on
`code`, never on `remediation` prose.

**Deploy wiring** — both flags reach the `tapps-brain-http` container only through the
`environment:` allowlist in `docker/docker-compose.hive.yaml` (there is no `env_file:`
passthrough). Set `TAPPS_BRAIN_STRICT_PROJECTS=1` / `TAPPS_BRAIN_STRICT_AGENT_ID=1` in
`docker/.env` — appending them anywhere else (shell export, a different `.env`) does not
reach the container. Default `0` in both the compose file and `docker/.env.example`
preserves pre-TAP-7243 behaviour.

---

## Failure remediation

| Symptom | Fix |
|---------|-----|
| `brain_auth_failed` | Token in `.env` + direnv; restart Cursor; check `TAPPS_MCP_MEMORY_BRAIN_AUTH_TOKEN` |
| CLI `memory` 401 | Export `TAPPS_MCP_MEMORY_BRAIN_AUTH_TOKEN` in shell |
| `403` / `out_of_profile` | Set `TAPPS_BRAIN_PROFILE=full` in tapps-mcp env |
| version below floor | Upgrade brain image (`make dev-deploy` in tapps-brain repo) |
| project not registered | `tapps-brain project register <slug>` |
| duplicate MCP servers | Remove direct `tapps-brain` from `.mcp.json`; run `tapps_upgrade` |
| `400 tenant_project_missing` / `tenant_project_literal` / `tenant_project_unregistered` | Set `X-Project-Id` to your project's real, registered slug — see [Tenant-scope enforcement](#tenant-scope-enforcement-tap-7243) |
| `400 tenant_agent_missing` / `tenant_agent_literal` | Set `X-Agent-Id` (or `X-Tapps-Agent`) to a stable logical agent name, not `unknown` |

Full matrix: [MEMORY_REFERENCE.md](../MEMORY_REFERENCE.md#troubleshooting-matrix).

---

## tapps-brain repo (this project)

This repository **develops** tapps-brain. It uses NLT MCP servers (`nlt-build`, `nlt-memory`, `nlt-project-docs`, …) on ports 8760–8765. Direct brain HTTP at `:8080` is for integration tests and coordinator workflows — see [mcp-client-repo-setup.md](../guides/mcp-client-repo-setup.md).

Do **not** commit `.env`. Commit only safe config when the user requests it.
