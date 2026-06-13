# Docker tier defaults

Generic postures + one machine profile. Same compose file; tier = env overlay.

| Tier | File | Use | Coding stack | Runtime stack |
|------|------|-----|--------------|---------------|
| **cheap** | `cheap.env` | Minimal cost | Optional throwaway | No |
| **balanced** | `balanced.env` | Generic coding default | Yes | No |
| **quality** | `quality.env` | Production runtime | No | Yes |
| **it13** | `it13.env` | **it13 workstation** (reviewed lab) | Yes — **this host** | No |

**Machine profile:** [it13.md](./it13.md) — topology, log notes, redeploy, and maintenance for the unified dev/POC box (`hostname it13`). IT13 extends balanced (offline HF cache, documented co-located stacks).

## Two deployments (recommended)

**Yes — run two brains**, not one shared instance:

| | **Coding** | **Runtime** |
|---|------------|-------------|
| Tier | `balanced` | `quality` |
| Compose project | `tapps-brain` | `tapps-brain-runtime` (separate host or ports) |
| Port (example) | `8080` | `8081` or prod host |
| DB volume | `tapps-brain-pgdata` | separate volume |
| Agents | You + IDE | Many runtime projects |

Isolation: no dev memory pollution, different auth, different pool sizing.

## Setup

```bash
# 1. Secrets (once per stack)
cp docker/.env.example docker/.env
# fill REPLACE_ME_* in docker/.env

# 2. Tier overlay
cat docker/defaults/it13.env >> docker/.env        # it13 workstation (recommended on it13)
# cat docker/defaults/balanced.env >> docker/.env  # generic coding
# cat docker/defaults/quality.env >> docker/.env   # runtime (other host/stack)

# 3. Deploy
make hive-deploy
```

Or: `make brain-env-init TIER=it13` (or `balanced` / `cheap` / `quality`)

## Client headers

| Tier | `X-Brain-Profile` |
|------|-------------------|
| cheap / balanced / it13 | `coder` |
| quality | `coder` (agents) — operator tools on `:8090` only |
