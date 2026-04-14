# EPIC-069 — next-session resumption prompt

Drop this into a fresh Claude Code session to pick up where 2026-04-14 left off.
Self-contained — it assumes no prior conversation context.

---

```
Continue EPIC-069 (multi-tenant project registration). Read these first so you
have the design in your head:

- docs/planning/adr/ADR-010-multi-tenant-project-registration.md   # decision
- docs/planning/epics/EPIC-069.md                                   # stories
- src/tapps_brain/project_registry.py                               # registry
- src/tapps_brain/project_resolver.py                               # precedence
- src/tapps_brain/migrations/private/008_project_profiles.sql       # schema

What's already shipped (DO NOT redo):
- Migration 008 (project_profiles table) — auto-discovered as schema v8
- ProjectRegistry (get/list_all/register/approve/delete/resolve)
- Project resolver with precedence: _meta > X-Tapps-Project > TAPPS_BRAIN_PROJECT > "default"
- MemoryStore honors TAPPS_BRAIN_PROJECT env and consults the registry
- Admin CLI: `tapps-brain project register|list|show|approve|delete`
- HTTP admin surface: GET/POST /admin/projects, POST /admin/projects/{id}/approve,
  DELETE /admin/projects/{id}, gated by TAPPS_BRAIN_ADMIN_TOKEN
- Full test coverage for the above (346 passed in last sweep)

What's left (work these in order; each is independently shippable):

1. STORY-069.3 finish — per-call project_id override in MCP dispatch.
   Today the MCP server creates ONE store bound to project_dir at startup.
   Clients connecting stdio already declare identity via TAPPS_BRAIN_PROJECT
   in .mcp.json env, so stdio works end-to-end. The gap is HTTP/SSE MCP
   where one server instance serves many tenants — needs per-call store
   routing when _meta.project_id appears. Grep src/tapps_brain/mcp_server.py
   for `_get_store` and `project_dir` — design a LRU-cached
   `_get_store_for_project(project_id)` and thread it through tool
   dispatch. Flag the fact that connection pools must not multiply
   without bound.

2. STORY-069.4 — strict-mode structured errors. When
   TAPPS_BRAIN_STRICT_PROJECTS=1 and an unknown project_id hits the
   server, ProjectNotRegisteredError is raised from
   MemoryStore._resolve_profile_from_registry. Currently this bubbles as
   a 500 on HTTP and an unstructured error on MCP. Map it to:
     - HTTP: 403 {"error": "project_not_registered", "project_id": "..."}
     - MCP: JSON-RPC error code -32002 with same payload
   Add tests that set TAPPS_BRAIN_STRICT_PROJECTS=1 and hit both transports
   with an unregistered ID.

3. STORY-069.7 — project_id in logs/diagnostics/snapshot. Grep for
   `structlog.get_logger` and `bind(` in save/recall/feedback paths —
   thread project_id into the bound context. Update
   src/tapps_brain/visual_snapshot.py so /snapshot accepts ?project=<id>
   and filters diagnostics_history and feedback_events accordingly. Add
   a project filter to examples/brain-visual/.

4. STORY-069.8 — RLS + live-Postgres integration tests. Add a migration
   009_project_rls.sql enabling RLS on private_memories and
   project_profiles with policies keyed on current_setting('app.project_id').
   Write tests/integration/test_tenant_isolation.py that proves a
   compromised connection with one project_id cannot read another's rows.
   Requires TAPPS_TEST_POSTGRES_DSN — follow the pattern in existing
   integration tests. Revisit ADR-009 (RLS ship-vs-defer) with the new
   tenancy model as context.

Constraints from project memory (MEMORY.md auto-loads):
- ADR-007 (Postgres-only) and ADR-008 (MCP-HTTP parity) are binding.
- No new public HTTP routes without MCP + library parity — admin routes
  are internal and have CLI parity, which is the documented position.
- Don't check PyPI for deploy status (tapps-brain isn't on PyPI).
- Deployed brain is the target; local src is for dev/tests only.

Validation contract before marking any story done:
- `uv run pytest tests/unit -q` must stay green (target: 2656+ passed, 0 failed)
- New behavior covered by new tests (mock-cm pattern for DB code; live
  127.0.0.1 HttpAdapter for HTTP; FastMCP test client for MCP)
- If you touch docs, verify `docs-mcp docs_check_drift` passes

Start by reading this prompt back, listing the 4 stories with your
prioritization/risk call, and pick one to start — do not do all four in
one shot. After any file edit, show the diff and the test you added.
```
