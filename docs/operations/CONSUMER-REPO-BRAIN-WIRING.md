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

## Failure remediation

| Symptom | Fix |
|---------|-----|
| `brain_auth_failed` | Token in `.env` + direnv; restart Cursor; check `TAPPS_MCP_MEMORY_BRAIN_AUTH_TOKEN` |
| CLI `memory` 401 | Export `TAPPS_MCP_MEMORY_BRAIN_AUTH_TOKEN` in shell |
| `403` / `out_of_profile` | Set `TAPPS_BRAIN_PROFILE=full` in tapps-mcp env |
| version below floor | Upgrade brain image (`make dev-deploy` in tapps-brain repo) |
| project not registered | `tapps-brain project register <slug>` |
| duplicate MCP servers | Remove direct `tapps-brain` from `.mcp.json`; run `tapps_upgrade` |

Full matrix: [MEMORY_REFERENCE.md](../MEMORY_REFERENCE.md#troubleshooting-matrix).

---

## tapps-brain repo (this project)

This repository **develops** tapps-brain. It uses NLT MCP servers (`nlt-build`, `nlt-memory`, `nlt-project-docs`, …) on ports 8760–8765. Direct brain HTTP at `:8080` is for integration tests and coordinator workflows — see [mcp-client-repo-setup.md](../guides/mcp-client-repo-setup.md).

Do **not** commit `.env`. Commit only safe config when the user requests it.
