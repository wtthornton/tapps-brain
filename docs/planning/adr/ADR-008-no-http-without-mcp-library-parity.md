# ADR-008: No new public HTTP routes without MCP + library parity

## Status

Accepted (2026-04-10)

## Context

tapps-brain is an **agent-first** memory system. The canonical product surface is
`AgentBrain` (Python library) + MCP tools. HTTP endpoints exist only to serve
infrastructure needs: liveness, readiness, metrics, and a small number of
orchestration hooks (STORY-060.3 / STORY-060.4).

Without a written constraint, there is a well-known pattern of endpoint creep:
every convenience "just needs one quick POST" until the HTTP surface duplicates the
full memory model in REST and diverges from the MCP surface.

### Current HTTP surface (baseline, v3.0)

| Route | Protection | Capability |
|-------|-----------|-----------|
| `GET /` | Public | Liveness alias |
| `GET /health` | Public | Liveness probe |
| `GET /ready` | Public | Readiness probe + migration version |
| `GET /metrics` | Public | Prometheus counters |
| `GET /info` | Auth-gated | Runtime info |
| `GET /openapi.json` | Public | OpenAPI spec |

Six routes. No memory CRUD.

## Decision

**No new public HTTP route may be added to `tapps-brain` unless the same capability
is also available via:**

1. **`AgentBrain` Python API** (or a successor public class), AND
2. **MCP tool** (or a documented rationale why MCP is not applicable, e.g. the
   route is infrastructure-only and has no semantic value as a tool call).

Infrastructure-only routes (`/health`, `/ready`, `/metrics`) are exempt from the
MCP requirement because they serve orchestrators, not agents.

### Rationale

- **Single source of truth:** features live in the Python library; HTTP and MCP are
  projections of that library, not independent surfaces.
- **Attack-surface control:** every extra route is a new auth boundary.
- **Documentation tractability:** ≤ 10 documented routes can be audited; REST parity
  with the full memory model cannot.

### Enforcement

1. **CODEOWNERS** — `src/tapps_brain/http_adapter.py` (and any future `http/`
   subtree) is owned by `@wtthornton`. A PR to that path requires explicit
   approval and an ADR or inline justification table for each new route.
2. **PR checklist** — the CONTRIBUTING.md checklist includes a step: *"If you added
   an HTTP route, confirm library + MCP parity (or documented exemption)."*
3. **OpenAPI snapshot test** — the existing snapshot test in
   `tests/unit/test_http_adapter.py` will fail if a route is added without updating
   the snapshot, prompting the author to justify the addition.

## Consequences

- **Adding HTTP routes is intentionally friction-ful.** A PR that adds a route
  without the above will be blocked in review.
- **Probe-only orchestrators** (K8s, Nomad, etc.) are unaffected; their routes are
  already present and exempt.
- **Future capabilities** (e.g. a `POST /recall` convenience route) must be
  implemented in `AgentBrain` and exposed as an MCP tool *first*; the HTTP route is
  then a thin wrapper — never the primary surface.

## Related

- EPIC-060 (Agent-First Core & Minimal Runtime API)
- STORY-060.4 (`http_adapter.py` auth + OpenAPI)
- ADR-007 (Postgres-only backends — same "keep it simple" philosophy)
