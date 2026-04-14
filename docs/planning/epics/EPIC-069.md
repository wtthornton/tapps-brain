# Epic 69: Multi-tenant project registration and profile delivery over MCP

<!-- docsmcp:start:metadata -->
**Status:** Proposed
**Priority:** P0 — Blocker for every downstream MCP tenant (Alpaca, openclaw, etc.)
**Estimated LOE:** ~2.5 weeks (1 developer)
**Dependencies:** ADR-007 (Postgres-only persistence), ADR-008 (MCP–HTTP parity), ADR-010 (this design)

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that one shared `tapps-brain` deployment can serve many
client projects (Alpaca, tapps-brain itself, future tenants) with
per-project profiles and real data isolation — without running a separate
server process per tenant. This closes a tenancy hole, replaces a
filesystem-based profile resolver that cannot work in a shared deployment,
and gives clients a clean, MCP-native way to declare who they are.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Ship a hybrid project-identity model: clients declare `project_id` via
transport-level headers / env vars (MCP `_meta` override per call),
server resolves profile from a Postgres `project_profiles` registry,
every memory row is partitioned by `project_id`, and admins author
profiles out-of-band via a CLI that consumes the existing
`profile.yaml` format.

<!-- docsmcp:end:goal -->

## Stories

### Story 69.1 — Postgres schema: `project_id` on private memory + `project_profiles` table
Add `project_id TEXT NOT NULL` column to `private_memories`; change unique
key to `(project_id, agent_id, key)`. Add `project_profiles(project_id PK,
profile JSONB, approved BOOL, source TEXT, created_at, updated_at)`.
Migration `003_project_partitioning.sql`. Backfill existing rows with
`project_id='default'`.

**Acceptance:**
- Migration runs green against an empty DB and a seeded-from-001 DB.
- Inserts without `project_id` are rejected by the DB.
- `SELECT DISTINCT project_id FROM private_memories` returns only
  `'default'` after backfill.

### Story 69.2 — Profile registry module + resolver refactor
New `tapps_brain/project_registry.py`:
`get_profile(project_id) -> MemoryProfile | None`,
`register_profile(project_id, profile, source='admin', approved=True)`,
`auto_create(project_id, template='repo-brain')`. Refactor
`store.py :: _resolve_profile` to call the registry first, fall back to
`repo-brain` built-in. Filesystem resolution (`.tapps-brain/profile.yaml`)
is removed from runtime — file is still parseable for admin CLI import.

**Acceptance:**
- Unit tests: known project → registered profile; unknown + strict →
  error; unknown + lax → auto-create + serve `repo-brain`.
- Store opens against a registered tenant and honors its profile.

### Story 69.3 — Transport-layer project resolution
`tapps_brain/project_resolver.py :: resolve_project_id(request) -> str`
with precedence `_meta.project_id` → `X-Tapps-Project` header →
`TAPPS_BRAIN_PROJECT` env → `"default"`. Wire into both the MCP server
(`mcp_server.py`) and the HTTP adapter (`http_adapter.py`). Attach
resolved ID to every log line via `structlog` context.

**Acceptance:**
- All four resolution sources covered by unit tests.
- Integration test: HTTP request with `X-Tapps-Project: alpaca` hits a
  different profile than one without.
- MCP tool call with `_meta.project_id=override` overrides the
  session-level ID.

### Story 69.4 — Strict-mode rejection + structured error codes
`TAPPS_BRAIN_STRICT_PROJECTS=1` makes unknown project IDs a hard error.
HTTP: 403 with `{error: "project_not_registered", project_id}`. MCP:
JSON-RPC error code `-32002` with the same payload. Strict mode is the
documented default for production deployments.

**Acceptance:**
- Strict + unknown → structured error on both transports.
- Non-strict + unknown → auto-registration + `approved=false` row +
  `INFO` log event `project.auto_registered`.

### Story 69.5 — Admin CLI + HTTP surface for profile authoring
`tapps-brain project register <id> --profile ./profile.yaml`,
`tapps-brain project list`, `tapps-brain project approve <id>`,
`tapps-brain project show <id>`. Mirror on HTTP as
`POST /admin/projects` (requires `TAPPS_BRAIN_ADMIN_TOKEN`).
Profile YAML format stays unchanged — `profile.yaml` becomes a seed
document.

**Acceptance:**
- `project register` parses a valid profile.yaml and stores it as
  `approved=true, source='admin'`.
- `project approve` flips `approved` on an auto-registered row.
- Admin endpoint rejects missing/incorrect tokens with 401.

### Story 69.6 — Client integration docs + `.mcp.json` patterns
Rewrite [docs/guides/mcp.md](../../guides/mcp.md) and
[docs/guides/agent-integration.md](../../guides/agent-integration.md)
with working `.mcp.json` snippets for HTTP (headers) and stdio (env)
transports. Update [docs/guides/getting-started.md](../../guides/getting-started.md)
and `README.md`. Add a "Register your project" walkthrough.

**Acceptance:**
- Every doc example includes `X-Tapps-Project` or
  `TAPPS_BRAIN_PROJECT`.
- Getting-started has a 60-second "register + connect" flow.
- `docs-mcp docs_check_drift` passes on all touched pages.

### Story 69.7 — Observability: `project_id` in logs, diagnostics, feedback
Thread `project_id` through structured logs, `diagnostics_history`,
`feedback_events`, and the `/snapshot` dashboard endpoint. Add a
per-project filter to the brain-visual dashboard Hive Hub view.

**Acceptance:**
- Every log entry from save/recall paths carries `project_id`.
- `/snapshot?project=alpaca` returns only that tenant's data.
- Dashboard filter toggle works against multi-tenant seed data.

### Story 69.8 — Data isolation hardening + RLS follow-through
Add row-level security policies keyed on `project_id`. Service role can
see all; tenant role sees only its own. Integration tests prove a
compromised tenant connection cannot read another tenant's rows.

**Acceptance:**
- RLS policies applied in migration.
- Integration test `test_tenant_isolation.py` green.
- ADR-009 (RLS) decision revisited and either adopted or explicitly
  deferred per tenant threat model.

## Out of scope

- OAuth-based project claims — deferred until MCP OAuth clients are
  widely deployed. Resolution chain already leaves a seam for it.
- Per-project **rate limits** — existing `SlidingWindowRateLimiter` is
  reused; project-scoped quotas are a follow-up.
- Cross-project federation/sharing — the `hive` plane remains the
  sharing channel; this EPIC is about **private** memory partitioning.

## Risks

- **Migration blast radius.** Adding a NOT NULL column to a populated
  table needs the backfill to land atomically. Mitigation: default the
  column to `'default'` in the migration and tighten after backfill.
- **Client breakage.** Any existing client not passing `project_id`
  lands on `"default"`. Mitigation: ship strict mode off by default for
  one release, warn loudly, flip in the following release.
- **Admin-token management.** A new shared secret to rotate.
  Mitigation: document rotation and scope the token to the `/admin/*`
  routes only.
