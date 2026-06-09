# Epic 78: Code Review Security & HTTP Layer Hardening

<!-- docsmcp:start:metadata -->
**Status:** Proposed
**Priority:** High
**Estimated LOE:** M

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that the tapps-brain HTTP/MCP surface cannot drift between duplicate middleware implementations and cannot be deployed without authentication in production.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Eliminate shadowed middleware duplicates in http_adapter.py, align operator MCP auth with constant-time comparison, and enforce fail-closed auth/CORS defaults for production deployments.

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

Jun 2026 code review found http_adapter.py redefines McpTenantMiddleware and OriginAllowlistMiddleware after importing the refactored versions from http/middleware.py (F811). The monolithic duplicates run in production while the decomposed helpers in middleware.py are dead code. serve.py operator MCP auth uses string equality instead of hmac.compare_digest (TAP-544 regression). Auth and CORS pass-through when env vars are unset is documented as dev-only but not enforced at deploy time.

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] - [ ] http_adapter.py uses only the canonical middleware classes from http/middleware.py with no local F811 redefinitions
- [ ] Operator MCP bearer check in serve.py uses hmac.compare_digest
- [ ] Production/docker startup fails or emits blocking error when TAPPS_BRAIN_AUTH_TOKEN is unset
- [ ] Docker production profile documents or enforces TAPPS_BRAIN_ALLOWED_ORIGINS
- [ ] Regression tests cover middleware wiring and operator MCP timing-safe auth

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

### 78.1 -- http_adapter.py: remove shadowed McpTenantMiddleware duplicate

**Points:** TBD

Describe what this story delivers...

**Tasks:**
- [ ] Implement http_adapter.py: remove shadowed mcptenantmiddleware duplicate
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** http_adapter.py: remove shadowed McpTenantMiddleware duplicate is implemented, tests pass, and documentation is updated.

---

### 78.2 -- http_adapter.py: remove shadowed OriginAllowlistMiddleware duplicate

**Points:** TBD

Describe what this story delivers...

**Tasks:**
- [ ] Implement http_adapter.py: remove shadowed originallowlistmiddleware duplicate
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** http_adapter.py: remove shadowed OriginAllowlistMiddleware duplicate is implemented, tests pass, and documentation is updated.

---

### 78.3 -- serve.py: constant-time operator MCP bearer compare

**Points:** TBD

Describe what this story delivers...

**Tasks:**
- [ ] Implement serve.py: constant-time operator mcp bearer compare
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** serve.py: constant-time operator MCP bearer compare is implemented, tests pass, and documentation is updated.

---

### 78.4 -- auth.py: fail-closed when auth token unset in prod

**Points:** TBD

Describe what this story delivers...

**Tasks:**
- [ ] Implement auth.py: fail-closed when auth token unset in prod
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** auth.py: fail-closed when auth token unset in prod is implemented, tests pass, and documentation is updated.

---

### 78.5 -- docker: enforce CORS allowlist in production deploy

**Points:** TBD

Describe what this story delivers...

**Tasks:**
- [ ] Implement docker: enforce cors allowlist in production deploy
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** docker: enforce CORS allowlist in production deploy is implemented, tests pass, and documentation is updated.

---

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Document architecture decisions for **Code Review Security & HTTP Layer Hardening**...

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- Full http_adapter.py decomposition beyond middleware deduplication; per-tenant auth redesign; rate limiting on /mcp (separate epic if needed).

<!-- docsmcp:end:non-goals -->

<!-- docsmcp:start:refs -->
## Refs

TAP-544

<!-- docsmcp:end:refs -->
