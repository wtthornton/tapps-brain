# Token rotation & secret recovery

How to rotate the bearer/admin tokens that gate the tapps-brain HTTP adapter, and how to recover them from a running container if the local `.env` files are lost.

## Secrets at a glance

| Variable | Lives in | What it protects | Rotate if leaked? |
|---|---|---|---|
| `TAPPS_BRAIN_AUTH_TOKEN` | `.env` (repo root) + `docker/.env` | Data plane at `:8080` (`/mcp/`, `/v1/*`) | **Yes** |
| `TAPPS_BRAIN_ADMIN_TOKEN` | `docker/.env` | Operator MCP transport at `:8090` | **Yes** |
| `TAPPS_BRAIN_DB_PASSWORD` | `docker/.env` | Postgres owner role `tapps` (DDL only) | Only on host compromise |
| `TAPPS_BRAIN_RUNTIME_PASSWORD` | `docker/.env` | Postgres role `tapps_runtime` (DML only) | Only on host compromise |

The two tokens at the top are reachable over loopback. The two Postgres passwords are only used inside the docker network — they never leave the bridge.

## When to rotate

- A token landed in a chat transcript, log, screen recording, or pastebin.
- A backup containing `docker/.env` or `.env` went somewhere you don't control.
- You're handing the machine off, or rotating creds on a schedule (90-day default is reasonable for a single-user dev stack).
- Suspected unauthorized access — check `tapps-brain-http` logs for unexpected `Authorization: Bearer` requests.

## Rotation procedure

**Do this from a regular terminal, not from an AI assistant session.** The point of rotation is to make the values in any captured transcript dead — generating new ones inside the same transcript defeats the goal.

```bash
cd /path/to/tapps-brain

# 1. Generate new tokens. The values stay on your terminal scrollback.
openssl rand -hex 32     # → new TAPPS_BRAIN_AUTH_TOKEN
openssl rand -hex 32     # → new TAPPS_BRAIN_ADMIN_TOKEN

# 2. Edit both files. Use an editor — do NOT `echo` or `cat <<EOF` the
#    token onto stdout, which would record it in shell history.
$EDITOR docker/.env       # replace TAPPS_BRAIN_AUTH_TOKEN + TAPPS_BRAIN_ADMIN_TOKEN
$EDITOR .env              # replace TAPPS_BRAIN_AUTH_TOKEN (must match docker/.env)

# 3. Recycle the services that read the tokens. No need to restart the DB.
docker compose -p tapps-brain -f docker/docker-compose.hive.yaml \
  up -d --force-recreate tapps-brain-http tapps-visual

# 4. Verify the new token authenticates.
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  -H "Authorization: Bearer <new-auth-token>" \
  http://127.0.0.1:8080/mcp/
# Expect: 406 (auth passed, GET isn't valid MCP — that's fine).
# 401 = the new token didn't propagate; re-check both .env files and re-recreate.

# 5. Confirm the OLD token is dead.
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  -H "Authorization: Bearer <old-auth-token>" \
  http://127.0.0.1:8080/mcp/
# Expect: 401.
```

After rotation, clear the old tokens from any client that hard-coded them: AgentForge `.env`, IDE MCP settings, CI/CD secret stores, dashboards, etc. Anything that talks to `:8080` needs the new value.

### Postgres password rotation (rare)

`TAPPS_BRAIN_DB_PASSWORD` and `TAPPS_BRAIN_RUNTIME_PASSWORD` are container-internal. Rotating them is real work because the live `tapps-brain-http` connection pool needs the new runtime password and the migrate sidecar grants need re-running. Only do this if you suspect the docker network or host is compromised.

```bash
# Edit docker/.env to set new values, then:
docker compose -p tapps-brain -f docker/docker-compose.hive.yaml down
docker compose -p tapps-brain -f docker/docker-compose.hive.yaml up -d
# The migrate sidecar will run on startup and rewrite role passwords
# idempotently against the new values.
```

## Recovery — token loss with containers still running

If `.env` and `docker/.env` are gone (deleted, wiped, lost laptop restored from a stale backup) **but the tapps-brain stack is still running**, the tokens are still in container memory. Read them back live:

```bash
# Pull the data-plane token + admin token from the http service.
docker exec tapps-brain-http env | \
  grep -E '^(TAPPS_BRAIN_AUTH_TOKEN|TAPPS_BRAIN_ADMIN_TOKEN|TAPPS_BRAIN_DATABASE_URL)='

# The DATABASE_URL embeds the runtime role password:
#   postgres://tapps_runtime:<password>@tapps-brain-db:5432/tapps_brain

# Pull the Postgres owner password from the DB container.
docker exec tapps-brain-db env | grep '^POSTGRES_PASSWORD='
```

Reconstruct `docker/.env` from `docker/.env.example` filling in the four values above, and `.env` from `.env.example` using only `TAPPS_BRAIN_AUTH_TOKEN` plus `TAPPS_BRAIN_AGENT_ID` / `TAPPS_BRAIN_PROJECT_DIR` for your environment. `chmod 600` both files.

After recovery, **rotate** — the values were just on stdout. Treat recovery as a one-step bridge to the rotation procedure above, not a stopping point.

## Recovery — containers also gone

If the containers were never persisted (no volumes, no `docker/.env` backup, no password manager entry), the tokens are unrecoverable. Re-bootstrap:

1. `cp docker/.env.example docker/.env` and fill in fresh values from `openssl rand`.
2. `cp .env.example .env` and set `TAPPS_BRAIN_AUTH_TOKEN` to match `docker/.env`.
3. `docker compose -p tapps-brain -f docker/docker-compose.hive.yaml up -d`.
4. Memory in the Postgres volume survives across container recreations (private + Hive tables). Memory in the local `.tapps-brain/` SQLite mirror does not, but that store was retired in ADR-007 stage 2 — only the Postgres data matters now.

## Pre-flight defenses

- **Keep `.env` and `docker/.env` in a password manager** (`pass`, `1Password CLI`, Bitwarden) so recovery is one command instead of a hunt. See `op inject` / `pass show` workflows.
- **Both files are gitignored** (`.gitignore:6`). Never commit them. CI should fail if either appears in a diff.
- **`chmod 600 .env docker/.env`** — these never need to be world- or group-readable.
- The shipped `.gitignore` covers `.env`, `.env.local`, `docker/secrets/`. If you add another secret file, add it to `.gitignore` *before* writing the secret in.

## See also

- `docs/guides/hive-deployment.md` — full stack bring-up
- `docs/guides/hive-tls.md` — TLS termination in front of `:8080`
- `docker/.env.example` — canonical secret list with regeneration commands
- `.env.example` — client-side template
