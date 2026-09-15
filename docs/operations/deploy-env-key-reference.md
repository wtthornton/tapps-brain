# Deploy-Time Env File — Key Reference (TAP-7539)

**Audience:** Operators relocating or auditing the live `docker/.env` deploy file.
**Purpose:** Document every key the running `tapps-brain-http` container reads from its
deploy-time env file, by name and purpose only — **never** by value. This file is
documentation; it carries no secrets and no defaults that would work in production.

> This is a supplement to [`docker/.env.example`](../../docker/.env.example) and the
> "Environment Variables" table in [`docker/README.md`](../../docker/README.md). Those
> two already cover the day-one required keys (`TAPPS_BRAIN_DB_PASSWORD`,
> `TAPPS_BRAIN_RUNTIME_PASSWORD`, `TAPPS_BRAIN_AUTH_TOKEN`, `TAPPS_BRAIN_ADMIN_TOKEN`,
> `TAPPS_BRAIN_ALLOWED_ORIGINS`). This file adds the tenancy/strictness keys that a
> relocation must carry forward byte-for-byte, since they are not secrets but they do
> change runtime behavior if dropped or defaulted differently at the new location.

## Why this file exists

TAP-7539: the live deploy root is currently under `/tmp` (subject to a 30-day
`systemd-tmpfiles-clean.timer` sweep — see `/usr/lib/tmpfiles.d/tmp.conf`). Before an
operator can relocate the deploy directory to a durable home, the exact set of keys the
new `docker/.env` must carry needs to be enumerated in one place, so nothing gets
silently dropped or silently re-defaulted by the compose file during the copy.

## Required keys

| Key | Secret? | Purpose | Safe default | Compose fallback if unset |
|-----|---------|---------|---------------|----------------------------|
| `TAPPS_BRAIN_DB_PASSWORD` | **Yes** | Postgres owner-role (`tapps`) password, used by the DB container init and the migrate sidecar. | None — must be present and non-empty. | None — `docker-compose.hive.yaml` fails fast (`:?set TAPPS_BRAIN_DB_PASSWORD in docker/.env`) if unset. |
| `TAPPS_BRAIN_RUNTIME_PASSWORD` | **Yes** | Password for the DML-only `tapps_runtime` role the brain logs in as. | None — must be present and non-empty. | Required; no default. |
| `TAPPS_BRAIN_AUTH_TOKEN` | **Yes** | Bearer token for the public data plane (`/mcp/`, `/v1/*`) on `:8080`. | None. | Required; no default. |
| `TAPPS_BRAIN_ADMIN_TOKEN` | **Yes** | Bearer token for the operator MCP on `:8090` (loopback-only by default). | None. | Required; no default. |
| `TAPPS_BRAIN_ALLOWED_ORIGINS` | No | Comma-separated browser origins. Required because compose sets `TAPPS_BRAIN_STRICT=1`; a missing value crash-loops the brain. | Local dev: `http://127.0.0.1:8088,http://localhost:8088` | None — required. |
| `TAPPS_BRAIN_STRICT_PROJECTS` | No | Refuses writes with an unrecognized `project_id` when `1`. | `0` (permissive) | `docker-compose.hive.yaml:120` defaults to `0` if unset. |
| `TAPPS_BRAIN_STRICT_AGENT_ID` | No | Refuses writes whose `agent_id` resolves to the anonymous placeholders `unknown`/`default` when `1` (TAP-6696). | `0` (permissive) | `docker-compose.hive.yaml:121` defaults to `0` if unset. |
| `TAPPS_BRAIN_PER_TENANT_AUTH` | No | Requires `X-Project-Id` and per-project token rotation when `1`. | `1` (compose default) — **note:** `docker/.env:53` also reads `1`, matching the default. The live appliance was observed briefly reporting `0` at one inspection; that was stale runtime state from a container that outlived a since-superseded `.env` edit, not a real `.env` override — see the relocation runbook's "Known divergence" section for the full account. Recreating the container (as relocation does) resolves it; the `.env` file itself needs no edit. | `docker-compose.hive.yaml:169` defaults to `1` if unset. |

## Non-exhaustive — see also

The four day-one secrets plus `TAPPS_BRAIN_ALLOWED_ORIGINS` are already documented with
inline `openssl` generation commands in `docker/.env.example` and the "Before You
Deploy" section of `docker/README.md`. The "Full feature promotion" table in
`docker/README.md` documents the remaining reference-stack toggles (reranker, OTel,
idempotency, embeddings, MCP tool eager-loading). This file exists only to give the
three tenancy/strictness keys above — which the measured live-deploy state (TAP-7539)
showed diverging from their compose defaults — an explicit purpose/default writeup, so
a relocation copies them forward deliberately rather than by accident.

## Handling `TAPPS_BRAIN_DB_PASSWORD` and other secrets during relocation

- Never print, echo, log, or paste the value of any key marked **Secret? Yes** above,
  in a commit, a PR body, a runbook, or a terminal transcript that gets pasted anywhere.
- When verifying a relocated `.env` file, check *presence and non-emptiness* only, e.g.
  `test -s docker/.env && grep -q '^TAPPS_BRAIN_DB_PASSWORD=.' docker/.env && echo present`
  — report `present=yes` / `present=no`, never the matched line's value.
