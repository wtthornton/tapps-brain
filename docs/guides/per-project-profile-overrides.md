# Per-project profile overrides

Memory tuning — `conflict_check`, `limits`, scoring weights — is resolved **per project**,
not per server. This guide covers how that resolution works, how to apply an override for
one consumer without touching any other, and the two things that will otherwise waste an
hour.

## Two different things are both called "profile"

This is the single most common source of confusion, and it has already cost one consumer a
blocked week.

| | `TAPPS_BRAIN_DEFAULT_PROFILE` | `MemoryProfile` |
|---|---|---|
| What it selects | Which **MCP tools** are visible | Memory **behaviour**: `conflict_check`, `limits`, scoring, seeding |
| Where it lives | `ProfileRegistry` — a name → frozenset of tool names | `project_profiles` table (ADR-010), or `profile.yaml` on disk |
| Scope | Process-wide | **Per project** |
| Set via | Env var on the container | `POST /admin/projects`, or a repo-local `profile.yaml` |

Inspecting a running container and finding only `TAPPS_BRAIN_DEFAULT_PROFILE` says nothing
about memory tuning. It is the tool-visibility knob.

## How per-project resolution works

`MemoryStore._resolve_profile` (`src/tapps_brain/store.py`) resolves in this order:

1. An explicit `profile=` constructor argument.
2. The **project registry** — when `TAPPS_BRAIN_PROJECT` and `TAPPS_BRAIN_DATABASE_URL`
   are both set, `ProjectRegistry.resolve(project_id)` returns that project's
   `MemoryProfile`. A registered row is used at **any** `approved` value.
3. Filesystem / built-in defaults via `resolve_profile(project_root)`.

The HTTP and MCP paths set `TAPPS_BRAIN_PROJECT` per request from the caller's project id
before constructing the store (`mcp_server/context.py`), and cache one `MemoryStore` per
`(project_id, agent_id)`. Unknown projects are auto-registered with the `repo-brain`
profile and `approved=false`, which is why a consumer that has ever called the server
already has a row you can edit.

## Applying an override

Worked example: raising the `context` supersede threshold for one consumer whose key-space
holds independent facts that sit around 0.6 similarity.

```bash
# 1. Read the current profile
curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://127.0.0.1:8080/admin/projects/nlt-ideas-scout | jq .profile > /tmp/profile.json

# 2. Merge the override
jq '.conflict_check.per_tier = {"context": 0.95}' /tmp/profile.json > /tmp/profile-new.json

# 3. Re-register, admin-owned and approved
curl -sS -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --slurpfile p /tmp/profile-new.json \
        '{project_id: "nlt-ideas-scout", profile: $p[0], approved: true,
          source: "admin", notes: "per_tier.context 0.95 — independent-fact key-space"}')" \
  http://127.0.0.1:8080/admin/projects

# 4. RESTART — see below. The override is not live until you do.
docker compose -f docker/docker-compose.hive.yaml restart tapps-brain-http

# 5. Confirm it persisted
curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://127.0.0.1:8080/admin/projects/nlt-ideas-scout | jq .profile.conflict_check
```

## The two traps

**1. The store cache has no TTL.** `_StoreCache` is a bounded LRU keyed by
`(project_id, agent_id)`, and the profile is bound once at `MemoryStore` construction.
A registry write does **not** affect an already-cached store. Until `tapps-brain-http`
restarts (or the entry is LRU-evicted, which will not happen for an active project), the
old threshold stays in force — and a verification run in that window looks exactly like
the change did nothing. Always restart, then verify.

**2. Re-register rather than hand-editing the JSONB.** A row written by auto-registration
carries `source=auto, approved=false`. Going through `POST /admin/projects` with
`approved=true` and a `notes` field leaves an auditable record of who changed a consumer's
memory semantics and why. Direct `UPDATE`s against `project_profiles` do not.

## Verifying an override actually took

Do not verify by reading the registry — that only proves the write landed, not that the
running store picked it up. Exercise the behaviour instead. For a supersede threshold:

1. Save three entries with distinct keys and distinct-but-thematically-similar values in
   the affected tier.
2. Confirm all three are recallable and every save reported an empty `invalidated`.
3. Confirm same-key replacement still works, so you have not traded one problem for
   another.

## See also

- [`hive-deployment.md`](hive-deployment.md) — the admin endpoints and their auth
- [`ADR-010-multi-tenant-project-registration.md`](../planning/adr/ADR-010-multi-tenant-project-registration.md) — the project registry design
- `src/tapps_brain/profile.py` — `ConflictCheckConfig`, including `per_tier`

`$ADMIN_TOKEN` above is the value of `TAPPS_BRAIN_ADMIN_TOKEN` on the server. When that
variable is unset the `/admin/*` routes return 503 rather than running unauthenticated
(EPIC-069).
