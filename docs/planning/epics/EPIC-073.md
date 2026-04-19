---
id: EPIC-073
title: "Per-profile MCP tool filtering — reduce agent-visible tool count from 55"
status: in_progress
priority: high
created: 2026-04-17
target_date: 2026-04-26
linear_epic: TAP-563
tags: [mcp, brain-api, backend, performance, security]
---

# EPIC-073: Per-profile MCP tool filtering

## Context

Today tapps-brain exposes **55 MCP tools** on `tapps-brain-mcp` and 68 on
`tapps-brain-operator-mcp`. Every repo-embedded coding agent sees all 55 tools,
even if it only needs the 6-tool `brain_*` facade. This wastes context tokens
(full schema must be loaded by the client) and increases attack surface
(a prompt-injected agent can call `memory_delete` or `agent_delete`).

The solution is per-profile tool filtering:

* A `ProfileRegistry` maps profile names → frozensets of allowed tool names
  (loaded from `config/mcp_profiles.yaml`).
* A `ProfileResolver` resolves the active profile per-request from the
  `X-Brain-Profile` header, agent-registry lookup (cached 60 s), or server
  default (`TAPPS_BRAIN_DEFAULT_PROFILE`, defaulting to `"full"`).
* A `ToolFilter` wraps `_tool_manager.list_tools` and `_tool_manager.call_tool`
  to hide and enforce the resolved profile per-request.

## Stories

| Story | Title | Status |
|---|---|---|
| STORY-073.1 (TAP-564) | Profile registry + YAML config | Done |
| STORY-073.2 (TAP-565) | Per-request profile resolution | Done |
| STORY-073.3 (TAP-566) | Filter tools/list and enforce tools/call | Done |
| STORY-073.4 (TAP-567) | Observability: metrics + structured logs | In Review |
| STORY-073.5 (TAP-568) | Client setup docs + .mcp.json snippets | Done |
| STORY-073.6 (TAP-569) | Contract tests + rollout plan | In Progress |

## Profiles

| Profile | Tools | Intended consumer |
|---|---|---|
| `full` | 55 | Default fallback — zero breaking change for existing clients |
| `operator` | 68 | Human operator / `tapps-brain-operator-mcp` |
| `coder` | 15 | Repo-embedded coding agents (facade + hooks + quality loop) |
| `reviewer` | 8 | Read-only PR/code review agents |
| `seeder` | 6 | Bulk ingestion pipeline agents |

## Rollout Plan

### Phase 1: Ship with `TAPPS_BRAIN_DEFAULT_PROFILE=full` (zero breaking change)

**Goal:** Deploy the profile-filtering code with no visible behavior change.

* The default profile is `"full"` (55 tools, same as pre-EPIC-073).
* No `X-Brain-Profile` header → same tool list as today.
* All existing clients continue to work without any changes.
* Container deploys with `TAPPS_BRAIN_DEFAULT_PROFILE=full` (or unset, since
  `"full"` is the hardcoded fallback).

**Verification:**
* `brain-healthcheck` exits 0.
* `tools/list` returns 55 tools for any client without the header.
* Prometheus metric `mcp_profile_resolution_source_total{source="default"}` is
  the dominant label — all legacy clients hit this path.

### Phase 2: Opt-in for new clients

**Goal:** New repo-embedded agents adopt profiles explicitly.

* Update `.mcp.json` in consuming repos to include `"X-Brain-Profile": "coder"`.
* Monitor `mcp_profile_resolution_source_total{source="header"}` — this should
  grow as clients are updated.
* Monitor `mcp_tools_list_total{profile}` — confirms which profiles are active.
* The coder profile (15 tools) reduces per-request schema load by ~73%.

**Adoption signal:** `source=default` counter drops as more clients opt in.

### Phase 3: Flip the deployed default (future, after adoption threshold)

**Goal:** Make `coder` the default for tapps-brain-mcp (standard server).

Flip **only after** `mcp_profile_resolution_source_total{source="default"}` drops
below **N requests/day** (operator to define N based on fleet size).

```bash
# In the container's environment:
TAPPS_BRAIN_DEFAULT_PROFILE=coder
```

* Any legacy client that never set the header will then see only 15 tools.
* Deploy with a phased rollout (one region / one environment first).
* Roll back by resetting the env var to `full`.

### Rollback procedure

```bash
# Revert to pre-EPIC-073 behaviour at any phase:
TAPPS_BRAIN_DEFAULT_PROFILE=full
# Redeploy the container — no code change required.
```

## Key implementation files

| File | Purpose |
|---|---|
| `src/tapps_brain/mcp_server/profile_registry.py` | ProfileRegistry: YAML → frozenset map |
| `src/tapps_brain/mcp_server/profile_resolver.py` | ProfileResolver: per-request resolution |
| `src/tapps_brain/mcp_server/tool_filter.py` | install_tool_filter: wrap list_tools + call_tool |
| `src/tapps_brain/mcp_server/mcp_profiles.yaml` | Profile YAML config (bundled package data) |
| `src/tapps_brain/http_adapter.py` | ProfileResolutionMiddleware: sets REQUEST_PROFILE contextvar |
| `src/tapps_brain/metrics.py` | Prometheus counters for profile resolution |
| `tests/integration/test_profile_filter.py` | Contract tests (STORY-073.6) |
| `tests/fixtures/profile_tool_sets/*.txt` | Golden tool-set files per profile |
| `docs/guides/mcp-client-repo-setup.md` | Client setup docs (STORY-073.5) |

## Success Criteria

- [ ] All integration tests in `test_profile_filter.py` pass.
- [ ] Coverage for `tool_filter.py`, `profile_resolver.py`, `profile_registry.py` at 100%.
- [ ] `brain-healthcheck` exits 0 at every phase.
- [ ] Rollout plan checked in (this document).
- [ ] Prometheus dashboards show `mcp_profile_resolution_source_total` and
      `mcp_tools_list_total{profile}` populated after first deployment.

## Observability

Metrics emitted by the profile-filtering stack (STORY-073.4):

| Metric | Labels | Description |
|---|---|---|
| `mcp_tools_list_total` | `profile` | Incremented per tools/list call |
| `mcp_tools_call_total` | `profile`, `tool`, `allowed` | Incremented per tools/call (allowed or denied) |
| `mcp_profile_resolution_source_total` | `source` | Resolution path: `header` / `registry` / `default` |
| `mcp_profile_resolver_cache_hits_total` | — | Agent-registry cache hits |
| `mcp_profile_resolver_cache_misses_total` | — | Agent-registry cache misses |

All counters are exposed on `/metrics` (Prometheus text format).

## ADR references

* ADR-007: Postgres-only persistence (all backends require a `postgres://` DSN)
* EPIC-062: Operator-tool separation (standard vs operator server)
* EPIC-057: AgentBrain facade (`brain_*` tools)
