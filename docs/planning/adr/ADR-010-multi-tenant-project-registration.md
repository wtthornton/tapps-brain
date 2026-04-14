# ADR-010: Multi-tenant project identification and profile registration over MCP

## Status

Proposed (2026-04-14)

## Context

`tapps-brain` is deployed as a single shared Docker service exposing an HTTP
adapter (:8088) and an MCP endpoint. Multiple client projects (Alpaca, the
`tapps-brain` repo itself, future tenants) will connect to the **same**
deployed instance.

Each project needs its own memory **profile** — tier half-lives, recall
token budgets, limits, importance tags, hybrid-fusion settings. Until now,
profile selection has been filesystem-based ([profile.py:644-672](../../../src/tapps_brain/profile.py#L644-L672)):

1. `{project_dir}/.tapps-brain/profile.yaml` (project-local)
2. `~/.tapps-brain/profile.yaml` (user-global)
3. Built-in `repo-brain` default

This resolution order only works for single-tenant local/CLI use. In a
multi-tenant deployment:

- The container has one filesystem — it can serve at most one "project profile" that way.
- Clients connecting over MCP have no channel to declare *which* project they are.
- Memory rows in Postgres are not partitioned by project, so profiles would leak across tenants even if selection worked.

The MCP spec (`2025-06-18`) provides no first-class "workspace" or
"tenant" field in the `initialize` handshake — only
`{protocolVersion, capabilities, clientInfo: {name, version}}`. Peers solve
this three different ways:

| Project | Strategy |
|---|---|
| Zep / Graphiti MCP | `--group-id` server **startup flag** — one process per tenant |
| Mem0 / OpenMemory | Per-call **tool arguments** (`user_id`, `agent_id`) |
| Databricks managed MCP | Per-call `_meta` parameters |

None fits tapps-brain cleanly: we want one deployment, configured-once
per project, overridable per call.

## Decision

Adopt a **hybrid transport-level project ID + server-side profile registry**:

1. **Data partitioning.** Every private memory row carries a
   `project_id TEXT NOT NULL` column. Unique key becomes
   `(project_id, agent_id, key)`. This is a precondition for anything
   that follows — profile selection without data isolation is a security
   hole.

2. **Project identity on the wire.**
   - **Streamable HTTP / SSE transports:** clients send
     `X-Tapps-Project: <project_id>` in connection headers (set in
     `.mcp.json` `headers`).
   - **stdio transport:** clients set `TAPPS_BRAIN_PROJECT=<project_id>`
     in the launched process `env` (set in `.mcp.json` `env`).
   - **Per-call override:** optional `_meta.project_id` on any MCP tool
     call or HTTP request body overrides the session-level ID. Useful for
     monorepo clients serving several sub-projects from one connection.
   - **Fallback chain:** header → env → `_meta` → the literal
     `"default"` project.

3. **Server-side profile registry.** A new Postgres table holds one
   profile per project:

   ```sql
   CREATE TABLE project_profiles (
       project_id   TEXT PRIMARY KEY,
       profile      JSONB NOT NULL,      -- full MemoryProfile serialization
       approved     BOOLEAN NOT NULL DEFAULT FALSE,
       source       TEXT NOT NULL DEFAULT 'auto',  -- 'auto' | 'admin' | 'import'
       created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
       updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
   );
   ```

4. **First-connection flow.**
   - Server receives a request with an unknown `project_id`.
   - If `TAPPS_BRAIN_STRICT_PROJECTS=1` → reject with 403 / MCP error
     `-32002 project_not_registered`.
   - Otherwise → auto-insert a row cloned from `repo-brain` with
     `approved=false, source='auto'`, log at `INFO`, serve the request.
     Admins review and approve later.

5. **Admin surface.** Profile authoring happens out-of-band:
   - HTTP: `POST /admin/projects` with `{project_id, profile_yaml, approved}`
     (requires admin token).
   - CLI: `tapps-brain project register <id> --profile ./profile.yaml`
     and `tapps-brain project approve <id>`.
   - `.tapps-brain/profile.yaml` keeps its existing YAML shape — it is
     now a **seed document format** consumed by the admin CLI, not a
     file the server discovers at runtime.

6. **Strict mode default for production.** Deployed services set
   `TAPPS_BRAIN_STRICT_PROJECTS=1`; unknown clients get a clear
   registration error. Local/dev deployments leave it off for
   self-service onboarding.

7. **Forward-compatibility with MCP OAuth.** When the Nov-2025 MCP OAuth
   flow lands in clients we use, `project_id` moves into OAuth token
   claims and the header becomes a dev-mode fallback. The resolution
   chain absorbs this change without touching tool schemas.

## Consequences

- **Data isolation is real.** Adding `project_id` to the private-memory
  key space closes a tenancy hole that exists today — any client talking
  to the deployed service can read any other client's memories.
- **Client `.mcp.json` must declare project identity.** This is a
  breaking change for early adopters; documented in the MCP and
  agent-integration guides. No silent migration — missing project ID
  either falls to `"default"` (dev) or errors (strict mode).
- **Filesystem `profile.yaml` at the project root is deprecated for
  runtime resolution.** The file still exists as an authoring format for
  `tapps-brain project register`. Runtime resolution order collapses to:
  *(1) registered project profile → (2) built-in `repo-brain` default*.
- **Admins own the registry.** New projects don't ship profiles; they
  register them. This matches the trust model of a shared deployment
  (clients can't silently replace another project's profile).
- **stdio and HTTP reach parity.** Both transports carry identity through
  a stable channel (env / headers). No tool-schema changes required.
- **Observability improves.** Every log line, diagnostic row, and
  feedback event now carries `project_id` natively.

## Rejected alternatives

1. **Push model — client sends full profile on a `register_project` tool
   call.** Any client can claim to be any project and overwrite config.
   Wrong trust model for a shared deployment.
2. **One server process per project (Zep model).** Strong isolation but
   defeats "one deployment" goal; operational overhead scales with
   tenants; shared Postgres still needs `project_id` tagging anyway.
3. **`project_id` as a required argument on every tool call (Mem0
   model).** Unergonomic — clients would thread the same literal through
   hundreds of call sites — and doesn't solve connection-scoped
   rate-limit / logging / auth.
4. **Stuff profile into `clientInfo.title` / capabilities negotiation.**
   Abuse of an advisory field; no size guarantees; re-sent on every
   reconnect.

## Supersedes / relates to

- **ADR-007** (Postgres-only persistence plane) — this ADR extends the
  `private_memories` schema with `project_id` and a sibling
  `project_profiles` table.
- **ADR-008** (MCP–HTTP parity) — resolution chain is identical across
  both transports so parity is preserved.
- **EPIC-010** (Configurable memory profiles) — profile content model is
  unchanged; only the *delivery channel* changes.
- Tracked for implementation as **EPIC-069**.

## Sources

- [MCP Schema Reference 2025-06-18 — `InitializeRequest`, `Implementation`](https://modelcontextprotocol.io/specification/2025-06-18/schema)
- [Databricks managed MCP — `_meta` parameter conventions](https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp-meta-param)
- [Zep Graphiti MCP server — `--group-id` multi-tenant flag](https://help.getzep.com/graphiti/getting-started/mcp-server)
- [Mem0 MCP integration — user/agent scopes as tool args](https://docs.mem0.ai/platform/features/mcp-integration)
- [MCP OAuth checklist (Nov 2025 spec)](https://www.mcpjam.com/blog/mcp-oauth-guide)
