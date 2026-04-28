# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Release policy

tapps-brain targets a **biweekly minor release** cadence (approximately every 14 days). Patch releases ship as needed for security fixes and critical bugs. Every release must pass `bash scripts/release-ready.sh` (packaging, tests ≥95% coverage, ruff, mypy, OpenClaw plugin build) before tagging. The CHANGELOG entry for each version is the release notes — no separate release-notes doc.

---

## [3.14.3] — 2026-04-28

### Fixed

- **HTTP adapter no longer blocks the event loop on sync DB calls (TAP-1099).**
  Every `async def` `/v1/*` route in `src/tapps_brain/http_adapter.py` was
  calling into `services/memory_service.py` (`_ms.*`) and the idempotency
  store (`istore.check` / `istore.save`) — both of which are sync `def`
  functions that issue blocking `psycopg` round-trips. Under concurrent load
  (AgentForge dispatches ~50 simultaneous agent calls during busy windows),
  every `/v1/remember`, `/v1/recall`, `/v1/forget`, `/v1/reinforce`, batch,
  and `learn_*` request blocked the FastAPI event loop on a single in-flight
  DB query — concurrent requests serialized behind it, producing tail-latency
  cliffs that looked like Postgres slowness but were actually loop saturation.

  Every sync DB call inside an async route is now wrapped in
  `await asyncio.to_thread(...)` so the loop keeps serving concurrent
  requests while the worker thread runs the round-trip. Speedup ceiling moves
  from **1 in-flight call per worker** to **64 (CPython default executor
  size)**, which exceeds typical AgentForge concurrency without any change
  to `MemoryStore`.

  Routes touched (9 sync `_ms.*` sites + 5 `istore.check` + 5 `istore.save`,
  19 wrapping insertions total):

  * `/v1/remember`, `/v1/reinforce`, `/v1/forget`, `/v1/recall`,
    `/v1/learn_success`, `/v1/learn_failure`
  * `/v1/remember:batch`, `/v1/recall:batch`, `/v1/reinforce:batch`

  No behavior change to error handling, idempotency replay, response shape,
  or auth. `_idempotency_save()` (a sync helper that is currently unused) is
  deliberately left untouched.

  AgentForge architectural review (see TAP-824 thread) found that AgentForge
  migrated to HTTP transport in TAP-995 and never imports `AsyncMemoryStore`
  directly, so EPIC-072's framing — "thread-executor saturation in
  `asyncio.to_thread`" — does not apply to AgentForge's hot path. The
  immediate user-facing fix lives in this adapter, not in `aio.py`. The
  full async-native rewrite (TAP-824) stays deferred until TAP-825's
  benchmark proves the 64-thread ceiling is hit.

### Added

- **Static regression guard** — `tests/unit/test_http_adapter_to_thread.py`
  AST-walks every `/v1/*` async route and asserts every `_ms.*` and
  `istore.{check,save}` call is the target of `await asyncio.to_thread(...)`,
  not a bare invocation. Catches anyone unwrapping a route in a future edit.
  10 tests, all green locally.

### Changed

- Release plumbing only: `pyproject.toml`, `server.json`, both
  `openclaw.plugin.json` manifests, `openclaw-plugin/package.json` +
  `package-lock.json`, `openclaw-skill/SKILL.md`, `llms.txt`,
  `docs/contracts/openapi.json`, and the `install.pip` lower bound bumped
  3.14.2 → 3.14.3.

---

## [3.14.2] — 2026-04-28

### Fixed

- **Production hardening passthrough on the unified Docker stack (TAP-1076).**
  `docker/docker-compose.hive.yaml`'s `tapps-brain-http` `environment:` block
  now propagates `TAPPS_BRAIN_METRICS_TOKEN` (or `_TOKEN_FILE`) and `HF_TOKEN`
  alongside the existing `TAPPS_BRAIN_ALLOWED_ORIGINS`. All three default to
  empty so the dev stack still boots without them, but a production deploy
  that leaves any of them unset triggers the matching one-shot startup
  warning the runtime already emits (`http_adapter.allowed_origins_empty`,
  `http_adapter.metrics_unauthenticated`, the unauthenticated-HF-Hub
  warning). Empty `ALLOWED_ORIGINS` accepts every Origin (DNS-rebinding
  vector); unset `METRICS_TOKEN` leaves `/metrics` callable
  unauthenticated; unset `HF_TOKEN` rate-limits embedding-model rehydrate
  paths. The TAP-547 metrics-auth wiring shipped in 2026-04 — TAP-1076 only
  closes the deployment-side documentation/config gap. `docs/guides/hive-deployment.md`
  gains a "Production hardening checklist" section that lists all three
  with example values + the literal warning text. `docker/.env.example`
  is **not** updated in this release — the local `.claude/settings.json`
  sandbox denies reads on `.env.*` files, so the file's update is deferred
  to a follow-up that relaxes that pattern (residual TAP-1076 AC bullet).
- **HuggingFace Hub 404-probe noise muted during embedding-model load
  (TAP-1077).** `transformers` / `huggingface_hub` probe a handful of
  optional config files (`adapter_config.json`, `processor_config.json`,
  `preprocessor_config.json`, `video_preprocessor_config.json`,
  `additional_chat_templates`) that `BAAI/bge-small-en-v1.5` doesn't ship,
  emitting one `INFO HTTP Request: HEAD … 404 Not Found` line each. None
  are real errors but they obscured real failures during on-call review.
  `embeddings.SentenceTransformerProvider.__init__` now wraps the
  `SentenceTransformer(model_name, **kwargs)` call in a tightly-scoped
  context manager (`_suppress_huggingface_http_chatter`) that bumps the
  `httpx`, `huggingface_hub`, and `transformers` loggers to `WARNING` for
  the duration of the load and restores prior levels on exit. Two
  regression tests in `tests/unit/test_memory_embeddings.py`
  (`TestSuppressHuggingfaceHttpChatter`) pin the suppression-active and
  level-restored invariants.

---

## [3.14.1] — 2026-04-28

### Fixed

- **Pool reset callback now commits, ending the connection-warning storm.**
  `PostgresConnectionManager._reset_session_vars` (sync) and
  `_reset_session_vars_async` issue `RESET app.project_id; RESET app.agent_id;
  RESET app.is_admin; RESET tapps.current_namespace` via `cur.execute`, which
  on a non-autocommit connection opens an implicit transaction. Without an
  explicit commit the connection went back to `psycopg_pool` in `INTRANS`
  state and the pool **discarded every released connection as BAD**, logging
  `connection in transaction status INTRANS to the pool. Discarding it.`
  Both reset callbacks now `commit()` (sync) / `await commit()` (async) at
  the end so the RESETs persist past the next borrow and the connection is
  recycled instead of dropped. Confirmed in production: 176 warnings in
  ~13 h pre-fix, 0 post-fix on the rebuilt container. Two regression tests
  pin the commit (and confirm we never roll back, which would silently undo
  the RESETs and leak the previous borrower's session identity into the
  next borrow).
- **TAP-1075 — `SentenceTransformerProvider` calls `get_embedding_dimension`,
  not the deprecated alias.** sentence-transformers 5.4.0 renamed
  `get_sentence_embedding_dimension` → `get_embedding_dimension`; the old
  name emits a `DeprecationWarning` and is slated for removal in 6.x.
  `embeddings.SentenceTransformerProvider.__init__` now uses the new name,
  and the `sentence-transformers` floor is bumped to `>=5.4.0,<6` so the
  rename is guaranteed available at runtime. Regression test added in
  `tests/unit/test_memory_embeddings.py`.

---

## [3.14.0] — 2026-04-27

### Added

- **Async-native PostgreSQL pool on `PostgresConnectionManager`** (EPIC-072 STORY-072.1, TAP-822): adds `get_async_pool()`, `get_async_connection()`, `close_async()`, `get_async_pool_stats()`, and `is_async_open` alongside the existing sync pool. The async pool uses `psycopg_pool.AsyncConnectionPool` with `open=False` + explicit `await pool.open()` so it never blocks the event loop on first connect. Both pools share the same DSN + env-var configuration (`TAPPS_BRAIN_PG_POOL_*`) but their lifecycles are independent — opening one does not open the other. The non-privileged-role guard (`TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE`, TAP-512 / TAP-783) is enforced on the async path identically to the sync path.
- **`AsyncPostgresPrivateBackend`** (EPIC-072 STORY-072.2, TAP-823): an async-native `PrivateBackend` implementation with the same surface as `PostgresPrivateBackend` but every IO method as `async def`. Exported from `tapps_brain.__init__`. Suitable for callers that want to run private memory IO without `asyncio.to_thread()` thread dispatch (~64-thread executor cap on default CPython).
- **Async tenant contexts** on `PostgresConnectionManager`: `async_project_context(project_id)` and `async_admin_context()` mirror the sync `project_context` / `admin_context` so RLS on `private_memories` / `project_profiles` is enforced for async callers identically to sync.
- **`Retry-After` header on `/admin/*` 429 responses** (TAP-780): clients now see the configured rate-limit window (`TAPPS_BRAIN_ADMIN_RATE_WINDOW`, default 60s) instead of having to guess.
- **Automated release workflow** (TAP-992): `.github/workflows/release.yml` fires on `vX.Y.Z` tag push, builds wheel + sdist from the **tag** (never `main`), smoke-installs into a clean venv, and attaches both artifacts to a GitHub Release with notes auto-extracted from the matching `## X.Y.Z` block in this file. Replaces the manual `twine upload` flow that caused TAP-990's 3-day fix-to-consumer lag. `scripts/publish-checklist.md` rewritten to lead with the automated path; manual `twine` documented as fallback.

### Internal

- **Refactor**: extracted every SQL string from `postgres_private.py` (1 137 → 803 lines, -334) into `tapps_brain._postgres_private_sql` so the sync and async backends import the exact same queries. Two query builders (`build_search_sql`, `build_query_audit_sql`) handle conditional WHERE composition; `build_save_params` keeps the column list and entry-attribute list co-located. A `TestSqlSharedWithSyncBackend` unit-test class asserts the executed SQL IS the same module-level constant in the async backend — drift between sync and async will fail loudly. Zero behavior change; 181 sync-backend unit tests pass unchanged.

### Compatibility

- **No breaking change.** Existing sync `PostgresPrivateBackend`, `MemoryStore`, and `AsyncMemoryStore` (via `asyncio.to_thread`) all keep their current shapes. `AsyncPostgresPrivateBackend` is a new public class; callers that want the async-native path can opt in by constructing it directly. `AsyncMemoryStore` wiring through the new backend is the focus of an upcoming minor (EPIC-072 STORY-072.3 — gated behind `TAPPS_BRAIN_ASYNC_NATIVE` once load benchmarks confirm the speedup).

### Deferred to next minor (EPIC-072 follow-ups)

- STORY-072.3 — `AsyncMemoryStore` natively backed by `AsyncPostgresPrivateBackend` (drop `asyncio.to_thread`).
- STORY-072.4 — load smoke benchmark (p95 before/after).
- STORY-072.5 — HTTP / MCP adapter async wiring.
- STORY-072.6 — feature flag graduation + docs.

---

## [3.13.0] — 2026-04-27

### Added
- **`agent_scope` and `memory_group` parameters on the `brain_remember` wire** (TAP-989): the MCP tool, `TappsBrainClient.remember`, and `AsyncTappsBrainClient.remember` now accept `agent_scope` (one of `"private"` / `"domain"` / `"hive"` / `"group:<name>"`) and `memory_group` (project-local partition) directly, so HTTP / MCP callers can target Hive namespaces without going through the lossy `share` / `share_with` derivation. Closes the surface gap that made `agent_scope="domain"` unreachable from any `brain_remember` caller.
  - **Precedence rule:** an explicit `agent_scope` wins over the legacy `share` / `share_with` derivation. When `agent_scope` is empty (the default), the legacy params are derived as before for back-compat (`share=True` → `"group"`, `share_with="hive"` → `"hive"`, `share_with="<x>"` → `"group:<x>"`). Documented on the tool docstring and both client `remember()` docstrings.
  - **Validation:** an unknown scope value returns the existing `invalid_agent_scope` error envelope (with `valid_values`), matching the CLI / `/v1/remember` REST contract.
- **TAP-991 — default `Authorization` header pinned at `httpx.Client` / `httpx.AsyncClient` construction**: the bearer token is now set as a default header on the underlying httpx client, so any helper that calls `self._http_client.post(...)` directly inherits auth automatically. Defense-in-depth on top of the per-call `_build_headers` path — closes the regression class that produced TAP-747 (the `_async_do_initialize` helper was added later than `_build_headers` and silently skipped auth, causing 401 on every MCP session init for AgentForge). httpx merges request-level `headers=` over client-level defaults, so existing call sites that pass `Authorization` via `_build_headers` keep their current behaviour — no behaviour change for current callers.

### Compatibility
- **No breaking change.** Existing `brain_remember` callers that rely on `share` / `share_with` continue to work unchanged. The new kwargs default to empty strings; only callers that pass them explicitly see the new behaviour. Wheel APIs, MCP tools, REST endpoints all keep their current shapes.

---

## [3.12.0] — 2026-04-25

### Added
- **`/v1/recall`, `/v1/forget`, `/v1/learn_success`, `/v1/learn_failure`** — REST counterparts to the `brain_recall` / `brain_forget` / `brain_learn_success` / `brain_learn_failure` MCP tools (TAP-993). Closes the last surface gap that forced HTTP consumers (AgentForge, NLTlabsPE) to either reach in via the MCP `/mcp/` JSON-RPC transport or vendor the `tapps_brain` Python wheel for runtime work. With these in place every `AsyncTappsBrainClient` / `AgentBrain` operation has a one-to-one REST equivalent under `/v1/*` — the wheel can be dropped from any non-MCP consumer.
  - All four routes share the same shape as `/v1/remember` / `/v1/reinforce`: `Authorization: Bearer …`, `X-Project-Id` (required), `X-Agent-Id` (optional), 64 KiB body cap, flat error envelope (`{"error": "...", "detail": "..."}`).
  - `/v1/forget`, `/v1/learn_success`, `/v1/learn_failure` accept `X-Idempotency-Key` (UUID) when `TAPPS_BRAIN_IDEMPOTENCY=1` and replay within 24 h. `/v1/recall` is read-only and skips idempotency.
- `docs/guides/http-adapter.md` rewritten as the agent-facing summary of every public route, with body shapes and an explicit MCP-tool ↔ REST-route mapping for migration off the vendored wheel.

### Compatibility
- **No breaking change.** Existing `/v1/*` routes, MCP tools, wheel APIs (`AsyncTappsBrainClient`, `AgentBrain`) all keep their current shapes. The four new routes are additive; consumers that already work over MCP or the wheel keep working with no edits.
- The matching MCP tools (`brain_recall`, `brain_forget`, `brain_learn_success`, `brain_learn_failure`) are unchanged — call sites that prefer JSON-RPC over `/mcp/` keep working.

---

## [3.11.0] — 2026-04-25

### Changed (breaking — deployment shape only; Python API unchanged)
- **Docker stack unified to single-DSN** (78bae0d): full rewrite of `docker/docker-compose.hive.yaml`. One Postgres (`tapps-brain-db`) + one HTTP service (`tapps-brain-http`) serving private memory + Hive + Federation on the same `/mcp/` + `/v1/*` API at `:8080` (operator MCP on `:8090`). `TAPPS_BRAIN_HIVE_DSN` and `TAPPS_BRAIN_FEDERATION_DSN` are now **optional overrides** that default to `TAPPS_BRAIN_DATABASE_URL` when unset. `hive-` / `tapps-hive-`-prefixed service names are gone; secrets moved from `docker/secrets/*.txt` to `docker/.env` (template: `docker/.env.example`). **Operators upgrading from 3.10.x must rebuild the stack from `docker/.env.example`** — old `hive-*` service names and the parallel `HIVE_DSN` flow are removed.
- **Brain runs as `tapps_runtime` role by default** (46cb72a): `TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1` is no longer set in the shipped compose. The brain connects as the DML-only `tapps_runtime` role so RLS + ownership guards stay on; the privileged role is reserved for the one-shot migrate sidecar (CI / dev override is unchanged). New `docker/migrate-entrypoint.sh` applies Hive + private + federation migrations as the DB owner before the brain starts. Operators who hand-rolled their own compose flow with `ALLOW_PRIVILEGED_ROLE=1` against the brain service should remove it and switch the brain to the `tapps_runtime` DSN.

### Documentation
- Hive reframed as a **feature of tapps-brain**, not a separate service (9406ef9, 7777aee). `CLAUDE.md`, `docs/guides/hive-deployment.md`, `docs/guides/hive.md`, `docs/guides/postgres-dsn.md`, `docs/guides/deployment.md` updated. `TAPPS_BRAIN_HIVE_DSN` / `_FEDERATION_DSN` documented as optional advanced overrides. Kubernetes examples renamed `tapps-hive-*` → `tapps-brain-*` with owner vs runtime-role secret layout. Troubleshooting adds the `permission denied for schema public` row pointing at the migrate sidecar.

### Tooling
- Added `docs-mcp` MCP server alongside `tapps-mcp` and `tapps-brain` (963cb5f). MCP server `instructions` strings rewritten as structured trigger + benefit so connecting agents know when and why to call each tool — matches the template rolled out in `tapps-mcp` v3.2.5.
- `tapps_upgrade 2.4.0 → 3.3.0`: regenerated tapps-* agents (researcher, reviewer, validator, review-fixer), regenerated stop + task-completed hooks, added `agent-scope.md` rule, added `linear-issue` skill that routes through the `tapps_linear_snapshot_*` cache (f7f14ed).

### Compatibility
- **Python API surface unchanged.** Clients pinned to `tapps-brain==3.10.3` keep working as-is — the breaking change is **deployment-shape only** (compose file, service names, env-var defaults, role).
- New deployments should follow `docs/guides/hive-deployment.md` and `docker/.env.example`. Existing 3.10.x deployments require a stack rebuild — see Changed section above.

### Release-gate posture
- Same as 3.10.3: unit suite green (3738/3738 with live Postgres). The 9 pre-existing integration/compat failures (tenant isolation, RLS spike, session-context persistence, embedded compat parity) are unchanged from 3.10.3 and tracked separately per `.github/workflows/ci.yml` (CI gates only `tests/unit/`). None of the failing test files were modified between 3.10.3 and 3.11.0.

---

## [3.10.3] — 2026-04-21

### Fixed
- `TappsBrainClient` / `AsyncTappsBrainClient` MCP `initialize` handshake (TAP-747): send the same auth + tenant headers (`Authorization: Bearer …`, `X-Project-Id`, `X-Tapps-Agent`) as regular `tools/call` requests, POST to the canonical `/mcp/` URL (trailing slash), and treat any non-2xx response — including 3xx redirects — as a hard error. Before this patch the client posted a bare `Content-Type` + `Accept` pair to `/mcp`; against an auth-gated FastMCP deployment the request 307-redirected to `/mcp/`, the redirect hop had no auth and returned 401, and `raise_for_status()` (which doesn't raise on 3xx) silently returned `None` for the session id. Stateless tool calls continued to work only because `_post_tool` reattaches auth on each call. Any consumer that rewrites `/mcp` → `/mcp/` upfront to avoid the redirect (e.g. AgentForge's TAP-742 workaround) immediately tripped the 401. Consumers can drop that path-rewrite workaround after pulling v3.10.3.

### Compatibility
- Client-only patch. No server, API, schema, or wire-format changes. `openapi.json` version bumped for manifest consistency only.

---

## [3.10.2] — 2026-04-21

### Added
- `/mcp` auth-failure bodies now include `auth_model`, `expected_env`, and best-effort `tool` and `project_id` diagnostics. `McpTenantMiddleware` peeks the JSON-RPC body on rejection to surface the intended tool (e.g. `hive_status`) and emits structured `mcp_auth.missing_bearer` / `mcp_auth.bearer_mismatch` log events so asymmetric-auth reports arrive pre-diagnosed. Shape:

  ```json
  {
    "error": "forbidden",
    "detail": "Invalid token.",
    "auth_model": "global_bearer",
    "expected_env": "TAPPS_BRAIN_AUTH_TOKEN",
    "tool": "hive_status",
    "project_id": "tapps-brain"
  }
  ```

  Status codes and the top-level `error` field are unchanged — purely additive metadata for clients like tapps-mcp's `auth_probe` / `tapps_doctor`.

---

## [3.10.1] — 2026-04-20

### Security
- `integrity.py` key-write path: eliminate TOCTOU race and world-readable key directory (TAP-709). Signing-key file now written atomically (`O_CREAT|O_EXCL`) to a `0o700` directory; removed dangling `os` import in test.
- `compute_integrity_hash`: replace pipe-joined canonical form with JSON encoding to close field-boundary collision attack (TAP-710). Hash inputs now serialized via `json.dumps` — pipe separators allowed forged hashes by injecting `|` into field values.
- `SentenceTransformerProvider`: pin `revision=` to a specific commit SHA on first load to prevent supply-chain substitution via model-hub push (TAP-720).
- Rate-limit `batch_exempt_contexts` bypass closed: replace string-based `batch_context` with `contextvar` `batch_exempt_scope`; callers outside the scope can no longer forge exemptions (TAP-714).
- `/health` endpoint no longer leaks exception text into warning/error strings in response body (TAP-724).

### Fixed
- `TappsBrainClient`: POST to `/mcp/` (trailing slash) to avoid Starlette 307 redirect that drops the request body, breaking all tool calls against a 3.10.0 server (TAP-743). **Critical — shipped client cannot call any tool without this fix.**
- `TappsBrainClient`: add MCP session-initialize handshake for FastMCP 3.10.0 compatibility; `stateless_http=False` default now correctly initializes the session (TAP-744). **Critical — client returns empty responses without this fix.**
- `/v1/remember`: catch `pydantic.ValidationError` on invalid slug keys and return HTTP 400 with `message` key instead of HTTP 500 (TAP-747).
- `_sanitise_content`: sanitize against original content to preserve caller's Unicode rather than normalizing twice (TAP-712).
- `SlidingWindowRateLimiter._session_count` renamed to `_lifetime_writes`; `writes_per_session` → `lifetime_write_warn_at` — the counter was session-scoped in name only (TAP-713).
- `sync_to_markdown`: write `memory.md` atomically via temp-file + rename to prevent mid-write corruption (TAP-715).
- `markdown_sync`: replace deprecated `batch_context(sync_from_markdown)` call path (TAP-716).
- `markdown_sync`: truncation guard on values over 4096 chars is now explicit and logged (TAP-717).
- `_parse_memory_md_sections`: slug collision detection — duplicate keys now raise instead of silently overwriting (TAP-718).
- `embeddings.get_embedding_provider`: log `unavailable` at `warning` instead of `debug` so operators see missing providers (TAP-719).
- `run_health_check`: replaced second `MemoryStore` open with direct `PrivateBackend` call to avoid double-lock contention (TAP-721).
- `run_health_check`: no longer reaches into `MemoryStore._lock` / `_entries` internals (TAP-722).
- `run_health_check.expired_entries`: ISO comparison uses UTC-normalized `datetime` not string comparison (TAP-723).
- `decay.decay_days_since`: swallowed `ValueError` on malformed ISO timestamps now surfaces as `DecayError` (TAP-725).
- `BloomFilter`: add `clear()` / `remove()` and resize on growth beyond load factor (TAP-726).
- `AsyncMemoryStore.__getattr__`: fixed wrapper creation — was creating a new `AsyncMemoryStore` instance per attribute access (TAP-727).
- `MCPTenantMiddleware.dispatch`: no longer reaches into FastMCP internals for tenant extraction (TAP-728).
- `PostgresConnectionManager.get_pool_stats`: swallowed exceptions now re-raised so pool exhaustion is observable (TAP-729).
- Example `coding-project/init/profile.yaml`: synced with `MemoryProfile` schema (TAP-748). Added comment that `extends:` is resolved client-side.

### Changed
- `mcp_server/__init__.py` (2388 lines) split into 7 focused submodules: `context.py`, `server.py`, `tools_brain.py`, `tools_memory.py`, `tools_feedback.py`, `tools_resources.py`, `tools_maintenance.py`, `tools_hive.py`, `tools_agents.py` (TAP-605). All public symbols remain importable from `tapps_brain.mcp_server`. No wire-affecting change.
- `_handle_signal(signum, frame)` in `cli.py` renamed params to `_signum`/`_frame` (TAP-607). Zero behavior change; silences vulture false positives.

### Added
- `MemoryStatus` lifecycle enum (`active` / `stale` / `superseded`) on `MemoryEntry` with GC-protection contract: `superseded` entries are protected from GC until their successor is confirmed stable (TAP-732). Schema migration adds `status` + `stale_reason` columns.
- `MemoryFilter` structured pre-filters on `MemoryRetriever` — tier, tag, date-range, `memory_class` field for coarse categorization before scoring (TAP-733).

## [3.10.0] - 2026-04-20

Security batch (TAP-626–TAP-655) + memory reliability fixes + graph centrality + temporal decay velocity.

### Security
- Per-tenant auth bypass closed (TAP-626). Requests with `TAPPS_BRAIN_PER_TENANT_AUTH=1` that omit `X-Project-Id` previously fell through to the global bearer token, allowing cross-tenant access. Missing header now returns 401; missing DSN (can't look up per-tenant token) fails closed with 503.
- `OriginAllowlistMiddleware` extended to all bearer-auth routes (TAP-627). Previously only `/mcp` paths were protected; all `Authorization: Bearer` routes now enforce the origin allowlist when `TAPPS_BRAIN_ALLOWED_ORIGINS` is set.
- SQL injection via f-string SQL composition eliminated (TAP-653, TAP-654). Hive, federation, and private query helpers that interpolated table/column identifiers directly into SQL strings converted to `psycopg.sql.Identifier` / `psycopg.sql.SQL` composition. `postgres_migrations.py` `version_table` parameter likewise converted.
- `hashlib` MD5/SHA1 calls marked `usedforsecurity=False` (TAP-648). Bloom filter and consolidation similarity hashes are structural, not cryptographic. Removes FIPS-mode `ValueError` and resolves bandit B324.
- `tapps-brain serve` and `tapps-brain-http` now default to binding on `127.0.0.1` instead of `0.0.0.0` (TAP-597). Docker Compose deployments are unaffected — `docker-compose.hive.yaml` sets `TAPPS_BRAIN_HTTP_HOST: "0.0.0.0"` and `TAPPS_BRAIN_MCP_HOST: "0.0.0.0"` explicitly. Operators running the binary outside Docker who need remote access must now set `--host 0.0.0.0` or the `TAPPS_BRAIN_HTTP_HOST` env var.

### Added
- `temporal_sensitivity` field on `MemoryEntry` — per-entry decay velocity override (`high` / `medium` / `low`) independent of tier classification (TAP-735). Schema migration 013 adds `temporal_sensitivity VARCHAR(6) DEFAULT NULL` to `private_memories` (`ADD COLUMN IF NOT EXISTS`; fully backward-compatible). Deployments with `TAPPS_BRAIN_AUTO_MIGRATE=1` apply it automatically at startup.
- Graph centrality scoring activated via lightweight entity co-occurrence index (TAP-734). `ScoringConfig.graph_centrality` weight (default 0.0, profile-tunable) blends a PageRank-style centrality signal into composite retrieval scores. Zero weight = existing behavior unchanged.
- HNSW index startup sanity check — on first `MemoryStore` open, confirms the HNSW index exists and logs `postgres_private.hnsw_index_ok` / `hnsw_index_missing`; missing index increments `tapps_brain_hnsw_index_missing_total` Prometheus counter (TAP-655).
- Prometheus observability for MCP profile filter — `tapps_brain_profile_filter_allowed_total` and `tapps_brain_profile_filter_blocked_total` counters labelled by `profile` and `tool` (TAP-567).
- EPIC-073 profile-filter contract tests and rollout plan under `tests/compat/` (TAP-569).
- End-to-end QA benchmark adapters for LoCoMo (arXiv:2402.17753) and LongMemEval (arXiv:2410.10813) under `src/tapps_brain/benchmarks/` (TAP-557 / STORY-SC01). Ships `AnswerModel` / `AnswerJudge` Protocols, deterministic stand-ins, and `scripts/run_benchmark.py` CLI reproducer. `benchmark-smoke` CI job runs against committed fixtures with no API credentials.
- TypeScript SDK (`@tapps-brain/sdk` v1.0.0) + LangGraph `BaseStore` adapter (`@tapps-brain/langgraph` v1.0.0) under `packages/` (TAP-561 / STORY-SC05). Full `brain_*` + `memory_*` MCP surface over Streamable HTTP. Scorecard D6b 3 → 4.
- `docs/guides/fleet-topology.md` — "N FastAPI containers + 1 brain sidecar" deployment pattern reference (TAP-571).
- `docs/case-studies/` — adopter case-study directory and submission template (TAP-562 / STORY-SC06).
- `docs/benchmarks/` — LoCoMo, LongMemEval methodology docs; numbers tables pending first full run.

### Fixed
- Idempotency check-then-save race condition eliminated (TAP-629). Concurrent requests that passed the check simultaneously could both proceed. Per-key `asyncio.Lock` ensures only one writer proceeds; the second receives the cached result.
- `LLMWritePolicy` rate-limit state guarded with `threading.Lock` (TAP-637). Timestamp list was mutated concurrently across threads, causing dropped or duplicate rate-limit events.
- ISO timestamp comparison in `MemoryEntry.is_temporally_valid` normalised to UTC `datetime` before comparison (TAP-639). Raw string comparison gave wrong ordering for UTC-offset timestamps.
- `session_index` in-memory fallback replaced with O(1) upsert and bounded bucket size (TAP-640). Previous `append`-on-every-call pattern caused unbounded list growth on long-lived stores; each bucket is now capped and old entries evicted.
- `PostgresPrivateBackend.load_all()` streams results via `fetchmany` instead of `fetchall` (TAP-642). Eliminates peak RSS spike proportional to store size on cold start.
- Bloom filter rolled back on `persist()` failure in `MemoryStore.save()` (TAP-644). A failed write previously left the in-memory bloom filter ahead of the DB, causing false-positive duplicate suppression.
- Per-session query-log lists capped to prevent unbounded memory growth (TAP-645). Lists are trimmed to the last N entries on each append.
- `TappsBrainClient` retry backoff capped at 30 s with ±20% jitter (TAP-647). Unbounded exponential backoff could delay retries indefinitely on persistent errors.
- `MemoryEntry.tier` field validates on assignment — coerces known tier strings, rejects unknown values with `ValueError` instead of silently storing invalid tiers (TAP-650).
- `load_profile` requires an explicit `profile:` wrapper key — rejects silently-passing bare YAML dicts that bypassed schema validation (TAP-652).
- `redact_tenant_labels` respected in HNSW index check; `pg_indexes` query scoped to `public` schema to avoid permission errors on restricted roles (TAP-655).
- `BM25Scorer._score_doc` guards against divide-by-zero on empty corpus or all-zero IDF weights (TAP-634).
- `MemoryRetriever._frequency_score` guards against divide-by-zero when `cap <= 0` (TAP-635).

### Documentation
- Scorecard updated: D6b 3 → 4 via STORY-SC05 TypeScript SDK; overall 79.8 → 80.6 (TAP-556).

## [3.9.0] - 2026-04-16

### Security
- `/metrics` tenant-label leak closed (TAP-547). The Prometheus endpoint was unauthenticated and emitted `tapps_brain_mcp_requests_total{project_id,agent_id}` and `tapps_brain_tool_calls_total{project_id,agent_id,tool,status}` counters, letting any host that could reach `:8080` enumerate tenants and active agents. New `TAPPS_BRAIN_METRICS_TOKEN` (or `TAPPS_BRAIN_METRICS_TOKEN_FILE`) env var gates full-label access; anonymous scrapers now receive a body with `project_id` / `agent_id` labels stripped and counters aggregated over those dimensions. `tool` and `status` labels are preserved (not tenant-identifying). **Migration:** Prometheus scrapers that want the full per-tenant surface must now send `Authorization: Bearer $TAPPS_BRAIN_METRICS_TOKEN`. Deployments without the token continue to respond 200 but with the redacted body and log a startup warning `http_adapter.metrics_unauthenticated`. Constant-time comparison (`hmac.compare_digest`) guards the token check.
- operator-tool gate hardened (TAP-545). Removed `contextlib.suppress(Exception)` around `mcp._tool_manager.remove_tool(...)` so startup fails loudly on FastMCP API drift instead of silently leaving operator tools callable. Added a post-loop registry assertion and a per-tool runtime guard (`_require_operator_enabled()`) that refuses to execute any operator tool body when `_tapps_operator_tools_enabled` is not truthy.
- operator tools blocked on unified HTTP adapter (TAP-546). `_build_mcp_server` on port 8080 now always passes `enable_operator_tools=False` and logs a structured warning `http_adapter.operator_tools_ignored` when `TAPPS_BRAIN_OPERATOR_TOOLS=1` is set. Operator tools remain reachable only via the separate `:8090` operator MCP (admin-token-protected).
- HTTP error envelopes no longer leak exception text to clients (TAP-550). All 14 call sites in `http_adapter.py` that embedded `f"Read error: {exc}"`, `f"Invalid JSON: {exc}"`, or `str(exc)` in response bodies now return stable generic messages (`"Failed to read request body."`, `"Request body must be valid JSON."`, `"Invalid profile or project_id."`, etc.) and log the full exception server-side via `logger.exception()`. Regression test confirms no exception class names or parser offsets appear in 4xx/5xx bodies.
- operator MCP port 8090 bound to loopback by default in Docker Compose (TAP-551). `docker-compose.hive.yaml` now publishes port 8090 as `${TAPPS_OPERATOR_MCP_BIND:-127.0.0.1}:8090:8090` instead of `0.0.0.0`. Deployments using 8090 from a remote host or reverse proxy must set `TAPPS_OPERATOR_MCP_BIND=0.0.0.0`. See `docs/guides/hive-deployment.md` for the full migration note.

### Fixed
- `IdempotencyStore` singleton — one `PostgresConnectionManager` per adapter process instead of a fresh raw psycopg connection per request (TAP-548). `IdempotencyStore` is initialised once in `create_app` lifespan and stored on `cfg.idempotency_store`; init failures downgrade to no-op rather than crashing the adapter.
- unbounded session-state growth in `MemoryStore` eliminated (TAP-549). `gc()` now sweeps all four `_session_*` dicts and evicts entries older than `implicit_feedback_window * 2`. Hard LRU cap of 10,000 session IDs (configurable via `TAPPS_BRAIN_MAX_SESSIONS`); evictions emit `store.session_state_evicted` log and increment the new `tapps_brain_store_active_sessions` Prometheus gauge.
- FastMCP DNS-rebinding guard now configurable for Docker/K8s deployments (TAP-507). New `TAPPS_BRAIN_MCP_ALLOWED_HOSTS` env var (comma-separated `host:port` entries) builds a `TransportSecuritySettings` allow-list passed to `FastMCP(transport_security=...)`, overriding the localhost-only default that caused 421 rejections from sibling containers. Unset → FastMCP default unchanged. Unblocks AgentForge TAP-500 workaround removal.
- `/ready` and `/metrics` no longer open a standalone Postgres connection on every hit (TAP-552). `_probe_db` results are cached for 2 s (`_PROBE_CACHE`, keyed on DSN), eliminating ~10 ephemeral connections per minute per adapter replica from Docker healthcheck + Prometheus scrape traffic.

## [3.8.0] - 2026-04-16

Cross-repo review batch — TAP-508 through TAP-514, plus the structured-propagation outcome contract that AgentForge and tapps-mcp depend on. **Wire-breaking** — see `### Changed (BREAKING)` below.

### Changed (BREAKING — wire path)
- public MCP endpoint collapsed from `/mcp/mcp` back to a single `/mcp` (TAP-509). v3.7.2 worked around a FastMCP submount quirk by pointing `TappsBrainClient` at `/mcp/mcp`; the real fix is to pin FastMCP's inner `settings.streamable_http_path = "/"` before building the streamable-HTTP sub-app, so when the adapter mounts it at `/mcp` the public path is just `/mcp`. **Client and brain must move together** — a v3.7.2 client will 404 against a v3.8.0+ brain and vice versa. Hand-rolled HTTP callers must POST to `/mcp`. New `tests/unit/test_mcp_route_path.py` locks the public path so any future FastMCP upgrade or mount-hierarchy change that re-introduces `/mcp/mcp` fails CI.

### Added
- versioned OpenAPI contract published at `/openapi.json` and snapshotted under `docs/contracts/openapi.json` + `docs/contracts/openapi-<brain-version>.json` (TAP-508). The spec is generated from FastAPI's auto-discovered routes and enriched by `tapps_brain.openapi_contract.build_openapi_spec` with the dual auth schemes (`bearerAuth`, `adminBearerAuth`), the standard tenant headers (`X-Project-Id`, `X-Agent-Id`, `X-Tapps-Agent`, `X-Idempotency-Key`), the `Error` envelope schema, and the ASGI-mounted `/mcp` route. CI job `openapi-contract` regenerates the snapshot and `git diff --exit-code`s it so any wire-affecting change forces an explicit spec update.
- `/info` now returns `schema_version` (bundled private-schema migration max) and `build` (from `TAPPS_BRAIN_BUILD`) alongside the existing `version` field. Brain `version` is read from `importlib.metadata.version("tapps-brain")`.
- structured propagation outcomes on `hive_propagate` / `hive_push`: every response now carries a canonical `decision` code (`propagated` | `refused_private_tier` | `refused_client_scope` | `refused_group_not_member`) plus `rule_applied`, `requested_scope`, `effective_scope`, `tier`, and — on refusals — a `would_require` hint (e.g. `{"force": true}` or `{"join_group": "<name>"}`). `hive_push`'s per-entry `pushed` / `skipped` records carry the same taxonomy. Legacy top-level `propagated: bool` and `reason` string remain for backward compatibility.
- public `MemoryStore.save_relations(key, relations)` and `MemoryStore.load_relations(key)` methods (TAP-510). Both wrap the underlying `_persistence` calls and keep the in-memory `_relations` cache consistent under `_lock`.
- `TAPPS_BRAIN_MAX_ENTRIES` env var for the per-project memory cap (TAP-513). Precedence: YAML profile (`limits.max_entries`) > env > default `5000`. Operators of deployed brains can now retune the cap without code changes; invalid values fall back to the default with a `store.max_entries_env_invalid` warning.

### Changed
- the hand-crafted `_OPENAPI_SPEC` dict in `http_adapter.py` is gone; `app.openapi` is overridden to call `build_openapi_spec(app)` so the published spec stays in sync with the route table by construction.
- `auto_consolidation.extract_relations` now uses `store.save_relations()` instead of reaching into `store._persistence` / `store._lock` / `store._relations` directly. Removes the design-debt TODO.
- `PropagationEngine.propagate()` now always returns a `dict[str, Any]` describing the routing decision; previously it returned `None` for refusals. Direct callers that ignored the return value are unaffected; callers that branched on `result is None` should branch on `not result["propagated"]`.
- `tests/conftest.py` honors `TAPPS_BRAIN_TESTS_STRICT=1` (TAP-511): when the env var is set and `TAPPS_BRAIN_DATABASE_URL` is unset, collection fails fast instead of silently skipping every `requires_postgres` test. CI's compat job sets STRICT and adds a post-pytest assertion that no requires_postgres tests were skipped. `scripts/release-ready.sh` runs `tests/compat/` a second time under STRICT so a missing DSN at release time fails the gate.
- internal `_MAX_ENTRIES` constant renamed to `_MAX_ENTRIES_DEFAULT` to make the precedence chain explicit. Callers/tests that patched `tapps_brain.store._MAX_ENTRIES` should patch `_MAX_ENTRIES_DEFAULT` instead.

### Fixed
- `PostgresConnectionManager.project_context()` / `agent_context()` / `admin_context()` / `namespace_context()` switched from `SET LOCAL` (transaction-scoped) to `SET` (session-scoped) so the bound identity survives multiple transactions inside one pool borrow (TAP-514). Earlier semantics let a caller `commit()` mid-`with` block and silently lose RLS context — fail-closed policies then hid every row. The pool now registers a `reset` callback that runs `RESET app.project_id; RESET app.agent_id; RESET app.is_admin; RESET tapps.current_namespace` on connection release, so identity cannot leak across borrows; a failed reset closes the connection rather than recycling.
- pre-existing `private/007_flywheel_meta.sql` migration error (`ON CONFLICT (version)` on a column with no UNIQUE/PK) replaced with a plain INSERT — matches 008-012 and the runner's existing skip-by-version idempotency.
- pre-existing `http_adapter._get_hive_pool_stats` mypy `no-any-return` error: bind to a typed local before returning.

### Security
- `private/012_rls_force.sql` adds `FORCE ROW LEVEL SECURITY` to `private_memories` and `project_profiles` so table-owner connections (e.g. `tapps_migrator`) and superusers can no longer silently bypass tenant isolation (TAP-512). Admin paths continue to work via the existing `app.is_admin='true'` bypass policy on `project_profiles`.
- `PostgresConnectionManager` now refuses to start when the connected role can bypass RLS (`rolsuper=true`, `rolbypassrls=true`, or owns the tenanted tables). Operators that genuinely need a privileged role (CI, dev, one-off maintenance) set `TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1` to acknowledge the risk; production deployments must NOT set it.

## [3.7.2] - 2026-04-16

### Fixed
- `TappsBrainClient` / `AsyncTappsBrainClient` posted to `/mcp` on the brain, but FastMCP's streamable-HTTP sub-app serves its own route at `/mcp` inside itself — so once mounted at `/mcp` by `http_adapter.py`, the actual public endpoint is `/mcp/mcp`. Every client call therefore 404'd. Fixed end-to-end by pointing the client at `/mcp/mcp`. Caught by the first real post-3.7.1 MCP handshake on a probe container.

## [3.7.1] - 2026-04-16

### Fixed
- MCP streamable-HTTP transport at `/mcp`: every request crashed with `RuntimeError: Task group is not initialized. Make sure to use run().` because `_lifespan` accessed `FastMCP.session_manager` **before** calling `streamable_http_app()`, which is what constructs the session manager. The lazy-init guard swallowed the RuntimeError, left `session_cm = None`, so the task group was never started. Reordered the lifespan so `streamable_http_app()` runs first, then `session_manager.run()` — `/mcp` tool calls now work end-to-end.
- `TappsBrainClient` / `AsyncTappsBrainClient` `http://` and `https://` URL schemes: the client posted to `/v1/tools/{tool_name}`, a REST route specced in STORY-070.11 but never shipped server-side, so every call 404'd. Unified both schemes onto the streamable-HTTP MCP transport at `/mcp`: `_post_tool` now sends an MCP `tools/call` JSON-RPC envelope, preserving the existing retry + error-taxonomy behavior. `mcp+http://` behavior unchanged.

## [3.7.0] - 2026-04-15

### Added
- connection pool tuning: `max_waiting` (cap queue depth, env `TAPPS_BRAIN_PG_POOL_MAX_WAITING`, default 20) and `max_lifetime` (recycle old connections, env `TAPPS_BRAIN_PG_POOL_MAX_LIFETIME_SECONDS`, default 3600) params on `PostgresConnectionManager` (story-066.7)
- `pool_min`, `pool_max`, `pool_idle` fields on `StoreHealth` and `HiveHealth` for richer `/ready` and `/health` diagnostics (story-066.7)
- live hive pool stats (`pool_size`, `pool_idle`, `pool_waiting`) emitted to `/metrics` Prometheus output (story-066.7)
- `docs/guides/postgres-tde.md` — pg_tde operator runbook covering transparent data encryption setup, key rotation, and emergency key recovery (story-066.10)
- `docs/guides/postgres-backup.md` — Postgres backup and restore runbook with pg_basebackup, WAL archiving, PITR, and verification procedures (story-066.11)
- brain-visual multi-page dashboard: hash-routed navigation with six pages (Overview, Health, Memory, Retrieval, Agents & Hive, Integrity & Export), persistent side-nav, deep-linkable URLs, nav-badge fail counts, and View Transitions API state changes — zero new npm dependencies (EPIC-068)
- brain-visual Integrity & Privacy / Export page: memory export (JSON/CSV), GC controls, contradiction report, privacy audit log, and agent detail drawer (story-068.7)
- 154 new unit tests covering behavioral parity, pg_tde runbook structure, backup runbook structure, docs drift sweep, and Postgres integration test scaffolding (stories 066.9–066.13)

### Changed
- `_collect_metrics` in `http_adapter.py` accepts optional `store` argument to surface live hive pool counters alongside existing DB and OTel metrics (story-066.7)

### Fixed
- brain-visual dashboard: hardcoded hex colour values replaced with CSS custom properties from NLT token source; keyboard navigation audit pass; reduced-motion pass; zero broken doc links (story-068.8)
- pool connection leaks in integration test fixtures (TAP-362)
- `recall`/`remember` tests correctly marked `requires_postgres` after ADR-007 (TAP-363)

## [3.6.0] - 2026-04-15

### Added
- operator-tool separation: `tapps-brain-mcp` (standard, safe for AGENT.md) and `tapps-brain-operator-mcp` (full operator tools, explicit grant required) are now distinct CLI entry points (story-070.9)
- native async parity: explicit `async def` methods on `AsyncMemoryStore` alongside `gc_run` alias; concurrent benchmark test validates throughput (story-070.10)
- `TappsBrainClient` and `AsyncTappsBrainClient` — typed sync/async HTTP network clients with structured error taxonomy, idempotency keys, and automatic retry (story-070.11)
- OTel + Prometheus label enrichment: `project_id`, `agent_id`, `tool`, and `status` labels on all brain counters; bounded cardinality (story-070.12)
- `examples/agentforge_bridge/` — AgentForge BrainBridge reference implementation showing remote-first brain-as-a-shared-service integration pattern (story-070.13)
- `tests/compat/` — embedded AgentBrain v3.5 API parity test suite gated on `TAPPS_BRAIN_DATABASE_URL` (story-070.14)
- CI `compat` job: ephemeral Postgres service container runs `tests/compat/` on every push/PR (story-070.14)
- `--transport {stdio,streamable-http}` flag on both MCP CLI entry points; `TAPPS_BRAIN_MCP_TRANSPORT`, `TAPPS_BRAIN_MCP_HOST`, `TAPPS_BRAIN_MCP_PORT` env overrides; `docker-compose.hive.yaml` adds operator MCP service on port 8090 (story-070.15)

### Fixed
- 7 pre-existing mypy errors in `postgres_migrations.py` (non-null `fetchone` guard), `postgres_connection.py`, `postgres_hive.py`, `project_registry.py`, and `feedback.py` (stale `type: ignore` suppressions removed now that stubs are present)

## [3.5.1] - 2026-04-14

### Fixed
- `AgentBrain.__init__` now honors `TAPPS_BRAIN_PROJECT` when resolving `project_id`, matching `MemoryStore.__init__`. Previously the library's primary entry point unconditionally called `derive_project_id(project_dir)` and bypassed the project registry — every library-path user got a per-directory hash instead of the registered slug. Caught by dogfood after registering the first live tenant. (epic-069)
- `tapps-brain project {register,list,show,approve,delete}` CLI commands crashed with `NameError: name 'os' is not defined` when invoked against a live DSN. `_open_project_registry` was missing the `os` import in its local scope. (epic-069)
- `tests/integration/test_tenant_isolation.py` fixtures — `MemoryProfile(name="repo-brain")` replaced with `get_builtin_profile("repo-brain")` (layers is required), and `PostgresPrivateBackend.get(key)` replaced with `load_all()` filter (no `.get` method exists). 6/6 tests now pass against live Postgres. (story-069.8)

## [3.5.0] - 2026-04-14

### Added
- multi-tenant project registration — `project_profiles` registry table (migration 008), `ProjectRegistry` module, `project_resolver` with `_meta > X-Tapps-Project > TAPPS_BRAIN_PROJECT > "default"` precedence (epic-069, adr-010)
- `tapps-brain project register|list|show|approve|delete` CLI sub-app for profile authoring against a deployed brain (story-069.5)
- HTTP admin surface `GET/POST /admin/projects`, `POST /admin/projects/{id}/approve`, `DELETE /admin/projects/{id}`, gated by `TAPPS_BRAIN_ADMIN_TOKEN` (story-069.5)
- `MemoryStore` honors `TAPPS_BRAIN_PROJECT` env as a human-readable `project_id` slug and consults the project-profile registry before falling back to built-in defaults (story-069.2)
- per-call MCP dispatch — bounded LRU `_StoreCache` keyed by `_meta.project_id` with close-on-evict; `TAPPS_BRAIN_STORE_CACHE_SIZE` env (default 16); stdio path unchanged (story-069.3)
- structured tenant-rejection errors — HTTP 403 `{"error":"project_not_registered","project_id":...}` and JSON-RPC `-32002` with structured `data` payload (story-069.4)
- `project_id` bound into structlog save/recall/feedback contexts; `/snapshot?project=<id>` filter; project dropdown in brain-visual dashboard (story-069.7)
- migration `009_project_rls.sql` enables RLS on `private_memories` (fail-closed — missing `app.project_id` returns zero rows) and `project_profiles` (admin bypass via `app.is_admin='true'`) (story-069.8)
- `PostgresConnectionManager.project_context()` / `admin_context()` using `SET LOCAL` (transaction-scoped) for RLS session vars (story-069.8)
- `tests/integration/test_tenant_isolation.py` — 6 live-Postgres tenant-isolation tests gated on `TAPPS_TEST_POSTGRES_DSN` (story-069.8)
- Agents page with SVG topology diagram + agent-detail drawer (story-068.6)

### Changed
- Profile selection for deployed brains no longer uses filesystem discovery — `.tapps-brain/profile.yaml` is now a seed document consumed by `tapps-brain project register`; in-process `AgentBrain` / `MemoryStore` usage is unchanged (adr-010)
- ADR-009 revisited (2026-04-14): RLS is now shipped on `private_memories` and `project_profiles`, not deferred, now that ADR-010 makes tenancy explicit end-to-end

### Security
- Row-Level Security enabled on private-backend tenanted tables (`private_memories`, `project_profiles`) as defence-in-depth against app-layer filter bugs. A code path that forgets to pass `project_id` now returns zero rows instead of silently leaking another tenant's data. Relies on the application connecting as a non-owner, non-superuser role; migration 009 does NOT set `FORCE ROW LEVEL SECURITY` (matches existing `hive_memories` pattern). (story-069.8)

### Removed
- Demo snapshot fallback in brain-visual dashboard: deleted `brain-visual.demo.json`, the "Load static demo" button, and the "Load snapshot file" manual upload; dashboard is live-only against the `/snapshot` endpoint

## [3.4.0] - 2026-04-12

### Added
- retrieval pipeline live metrics panel (story-065.7)
- add memory velocity panel to dashboard (story-065.6)
- agent registry live table in dashboard (story-065.5)
- Hive hub deep monitoring panel with per-namespace table (story-065.4)
- purge stale and privacy-gated dashboard components (story-065.3)
- dashboard live polling mode (story-065.2)
- add GET /snapshot live endpoint to HttpAdapter (story-065.1)
- add Postgres integration tests replacing deleted SQLite-coupled tests (story-066.13)
- engineering docs drift sweep — zero stale SQLite refs (story-066.12)
- behavioral parity doc + load smoke benchmark (story-066.9)
- auto-migrate private schema on startup via TAPPS_BRAIN_AUTO_MIGRATE=1 (story-066.8)
- connection pool tuning env vars, health JSON pool fields, DSN validation (story-066.7)
- CI workflow with ephemeral Postgres service container (story-066.6)
- bump distribution version strings from 3.2.0 to 3.3.0 (story-066.5)
- GC archive Postgres table (migration 006) (story-066.3)
- bi-temporal as_of filter on PostgresPrivateBackend.search (story-066.2)
- partial — add delete_relations + audit to backends (story-066.1)
- complete SQLite rip-out — Postgres-only persistence plane (stage 2) (adr-007)
- add demo snapshot and Load demo control to brain-visual (story-064.5)
- add deep insight panels — retrieval pipeline, diagnostics, privacy (story-064.4)
- add CSS motion token system with WCAG 2.3.3-compliant reduced-motion gates (story-064.3)
- narrative & IA refresh — decision-first copy, story beats order, microcopy (story-064.2)
- NLT Labs brand audit — gap matrix + fetch path doc (story-064.1)
- add end-to-end OTel integration tests (story-032.10)
- add privacy controls + OTelConfig.capture_content from environment (story-032.9)
- add feedback and diagnostics OTel span events (story-032.7+032.8)
- add tapps_brain.* custom metrics + export hook (story-032.6)
- add standard GenAI + MCP metrics via GenAIMetricsRecorder (story-032.5)
- add non-retrieval OTel spans (delete, reinforce, save) (story-032.4)
- retrieval document events + MCP params._meta traceparent extraction (story-032.3)
- add GenAI semconv v1.35.0 MCP tool call spans (story-032.2)
- add OTelConfig, HAS_OTEL flag, and bootstrap_tracer() (story-032.1)
- CI epic validation gate + regression runbook (story-062.7+062.8)
- canonical env-var contract + .env.example (story-062.5+062.6)
- gate operator/maintenance MCP tools behind --enable-operator-tools flag (story-062.4)
- freeze MCP core tool list and regenerate manifest (story-062.3)
- strict startup — clean stderr + non-zero exit + not-for-prod docs (story-062.2)
- add unit tests for _get_store Hive backend wiring from unified DSN (story-062.1)
- scope audit matrix doc and code checklist (story-063.5+063.6)
- RLS benchmark script + ADR-009 ship decision (story-063.4)
- RLS spike — namespace isolation on hive_memories (story-063.3)
- add least-privilege DB roles migration and runbook (story-063.1+063.2)
- add MemoryBodyRedactionFilter log handler and OTel metric Views (story-061.7)
- K8s liveness/readiness probe docs + explicit liveness test (story-061.4/061.5)
- metrics gauges, error counters, pool stats, bounded label policy (story-061.2)
- add OTel trace spans to remember/recall/search/hive hot paths (story-061.1)
- rewrite agentforge-integration.md for v3 Postgres DSN (story-060.7+060.8)
- ADR-008 no HTTP without MCP parity + CODEOWNERS guardrails (story-060.5+060.6)
- HTTP adapter optional routes, auth middleware, and OpenAPI spec (story-060.4)
- add minimal HTTP adapter with /health, /ready, /metrics (story-060.3)
- add typed exception taxonomy + v3 breaking changes docs (story-060.2)
- Compose, Makefile, and AGENTS.md onboarding for v3 Postgres dev workflow (story-059.7)
- DSN table, pool idle timeout, pool saturation + migration version in health JSON (story-059.7)
- behavioral parity doc + concurrent-agent load smoke (story-059.6)
- private memory integration tests — round-trip save/recall with N entries (story-059.5)
- private memory Postgres schema + migrations (story-059.4)
- no silent SQLite in runtime + v3 doc sweep (story-059.3)
- remove SQLite hive/federation; move AgentRegistration/AgentRegistry to models/backends (story-059.2)
- add edge-case tests for Postgres-only backend factories (story-059.1)
- remove SQLite backends, add Postgres-only factory and CI (epic-059)
- add tapps-visual nginx service for brain-visual frontend (docker)
- visual snapshot PNG capture with Playwright + scorecard branch coverage (STORY-048.6)
- doc validation strict mode + pluggable lookup engine guide (EPIC-048.5)
- complete stories 048.1–048.4 (session, relations, markdown, eval) (EPIC-048)
- temporal query filtering + consolidation threshold profile-config (#70/#71)
- implement EPIC-053–058 — per-agent brains, Postgres Hive, unified API, Docker deployment

### Changed
- stage-delete scorecard-derive.js missed in prior commit (story-065.3)
- add Postgres backup and restore runbook (story-066.11)
- pg_tde 2.1.2 operator runbook (story-066.10)
- full suite runs at deployment only — never during ralph loops (ralph)
- remove premature QA gates — all testing deferred to 066.14 (ralph)
- session continuity, team mode, effort scaling by task size (ralph)
- raise maxTurns 50→100 for main agent and architect (ralph)
- speed optimizations — stop loop, harden deferred-QA rule (ralph)
- check off 066.1 — 5 consolidation audit tests fixed (story-066.1)
- completed tasks delete from fix_plan, append to archive (ralph)
- archive completed tasks to fix_plan_archive.md (ralph)
- shrink fix_plan to story pointers only — was 11k tokens (ralph)
- reorder fix_plan — EPIC-066 (bug fixes) before EPIC-065 (new feature) (ralph)
- update PROMPT.md for EPIC-065/066 campaign (ralph)
- enable agent mode, bump effort, tighten timeout (ralph)
- WIP private backend, Ralph state, planning updates (checkpoint)
- add EPIC-065 live always-on dashboard epic with 7 stories (065)
- bump to v3.3.0 — Docker infrastructure rebuild (release)
- doc + a11y + MCP gate — EPIC-064 complete (064.CLEAN)
- add "See it in action" CTA and cross-links for brain-visual dashboard (064.6)
- fix mcp.md doc drift — add 6 undocumented core tools, remove phantom tool (062.CLEAN)
- manual security scan + doc cross-ref validation (063.CLEAN)
- add negative scope-enforcement tests (story-063.7)
- add STRIDE threat model one-pager for v3.0 (story-063.8)
- add operator observability runbook with alert examples (story-061.8)
- add telemetry policy doc and PR template review slot (story-061.6)
- check off already-implemented trace context propagation task (061.3)
- add ADR-007/008 to doc index, fix broken db-roles link (060.CLEAN)
- refresh agent-integration guide with full AgentBrain API surface (story-060.1)
- sweep stale SQLite references from docs and source docstrings (059.CLEAN)
- check off story-059.2 in fix_plan (ralph)
- Merge branch 'worktree-agent-a030f3aa'
- use uv sync --group dev; Ralph setup verified (dev)
- Claude MCP for tapps/docs, fix_plan cleanup tasks, roadmap v3 queue (ralph)
- refine ADR-007, greenfield epics EPIC-032/059-063, CLAUDE backend note (planning)
- add v3 greenfield epics and fix review findings (planning)
- add index, contributing, llms.txt; fix internal links and IDE config
- sync docs and Docker to v3.2.0 (release)
- sync all engineering docs to EPIC-053–058 architecture (v3.1.0)
- add agentforge-integration.md — generic guide for connecting projects
- bump version to 3.2.0, finalize CHANGELOG for EPIC-048
- epic status hygiene sweep — mark EPIC-040/042/044/050/053-058 done (planning)
- add EPIC-053–058 — per-agent brains, Postgres Hive, unified API
- bump version to v3.1.0
- phase 11 — replace Cohere reranker with FlashRank local cross-encoder
- phases 7/10/12 — env var docs, embedding model upgrade, SQLite best practices
- execute phases 5-6 — remove sigmoid normalization + collapse schema migrations
- execute phases 3-4 — formalize core deps + remove backwards compat
- reduce GitHub Actions cost — drop cross-platform from PRs, add caching + concurrency
- execute phases 1-2 — dead code removal + dependency updates
- v2.2.0 — sqlite-vec promoted to core, async wrapper fixes + tests

### Fixed
- resolve all 136 unit test failures — zero failures achieved (066.14)
- enable tapps-mcp permissions + upgrade to v2.4.0 (ralph)
- enable operator tools in GC/consolidation MCP test fixtures (story-066.4)
- resolve 18 ruff errors across OTel and HTTP adapter files (lint)
- OTel code quality + span names in architecture doc (061.CLEAN)
- fix remaining test files importing deleted SQLite modules (story-059.2)
- update test expectations for STORY-048.1 and STORY-048.2 (tests)
- install from local wheel + psycopg, fix entrypoint duplication (docker)
- explicitly disable embedding provider in no-embedding test (test)
- additional pre-existing test failures from full suite run
- quality gate — ruff, mypy, format, and pre-existing test failures

## [2.1.0] - 2026-04-06

### Changed
- v2.1.0 — async API, PA extraction, procedural tier, temporal filtering, profile consolidation

## [2.0.4] - 2026-04-05

### Added
- operator docs, observability, verify-integrity CLI (epic-043/045/046/047/049)
- offline save-conflict export; docs: ADR-001-006 and planning sync (044)
- merge undo, per-group entry caps, docs sync (epic-044)
- consolidation sweep CLI, seed version on health/stats, docs sync (epic-044)
- GC metrics, consolidation sweep, seeding version, eviction docs (epic-044)
- embeddings v17, hybrid profile RRF, RO sqlite, conflict exclude_key (epic-042,044,050)
- decay/FSRS decision doc and reinforce stability (epic-042.8)
- injection tokenizer hook and telemetry (epic-042.7)
- align composite scoring weight validation and docs (epic-042.5)
- SQLite busy tuning, locked runbook, lexical retrieval (epic-050, epic-042)
- save-path phase latency histograms for observability (store)
- Hive group agent_scope, recall union, and test alignment (story-041.2)
- engineering Phase 2 (#55-62) (docs,federation,mcp)
- carry publisher memory_group through hive propagation (closes #51) (hive)

### Changed
- v2.0.4 — EPIC-052 code review sweep fixes + doc sync
- add EPIC-052 full codebase code review sweep (planning)
- troubleshoot provenance warning (#65) (openclaw)
- expand help coverage and document help keys (brain-visual)
- refresh next-session prompt with prioritized next slices (planning)
- help pills for Hive, Entries, DB tiles and guide notes (brain-visual)
- record GitHub #52 reopened for checklist alignment (planning)
- sync roadmap after closing GitHub #52 #63 #64 (#51 already closed) (planning)
- close EPIC-041 loop, refresh roadmap, document concurrency (planning)
- add features-and-technologies map and link from architecture (engineering)
- align EPIC-042-051 stories with tests and verification (planning)
- sync CLAUDE, Cursor rules, Ralph AGENT with v16 + manifest (ai)
- remove mem0-review vendored tree

### Fixed
- 2026-Q2 code review sweep — write-through consistency + hygiene (epic-052)

## [2.0.3] - 2026-03-30

### Added
- recall diagnostics, agent integration, OpenClaw capture
- optional memory_group on relay import; plan 49-E federation-only (relay)
- project-local memory_group (schema v16, retrieval, MCP/CLI) (#49)
- GC stale listing and profile tier migrate (#21, #20)
- adaptive hybrid fusion (#40) and hive batch push (#18)
- sub-agent memory relay import/export (GitHub #19) (relay)
- optional SQLCipher at-rest and planning sync (encryption)
- session summarization — CLI, Python API, and MCP tool (#17)
- write notifications, hive watch, MCP poll (#12) (hive)
- sqlite-vec index, health sqlite-vec fields, profile onboarding MCP (week1-2)

### Changed
- tapps-brain v2.0.3 — version and OpenClaw manifest alignment (release)
- restore ≥95% gate for Linux/Python 3.12 (coverage)
- v2.0.2 — changelog, STATUS, OpenClaw manifests (release)
- close epic #49; track backlog #51 and #52 (planning)
- bump to v2.0.1 (PyPI, plugin, manifests) (release)
- sync roadmap and fix_plan with GitHub issue closures (planning)
- feature intake governance, GitHub templates, and agent rules
- update uv.lock
- check off 040.22 in fix_plan

### Fixed
- update stale schema version and entry limit assertions (v15→v16, 500→5000) (tests)
- Merge pull request #50 from wtthornton/fix/openclaw-tier-normalize-ci
- MCP tool text unwrap; feat(store): tier normalization (openclaw)
- singleton McpClient — one MCP process per workspace, not per session (plugin)
- add SIGTERM/SIGINT handler to prevent stray MCP process leak (plugin)
- profile-aware tier validation in MCP memory_save (closes #16) (story-022)

## [2.0.0] - 2026-03-25

### Added
- Groups as first-class Hive layer — create, manage, search across groups (GitHub #37) (040.21)
- per-entry conflict detection on save (GitHub #44) (040.16)
- PageRank scoring for memory relationship graphs (GitHub #33) (040.15)
- Louvain community detection for smarter consolidation (GitHub #36) (040.13)
- tapps-brain openclaw init/upgrade commands (GitHub #26) (040.20)
- assemble() injects memory recall nudge (GitHub #27) (040.19)
- periodic mid-session memory flush every N messages (GitHub #25) (040.18)
- flush recentMessages on dispose() — prevent session context loss (GitHub #24) (040.17)
- write deduplication with Bloom filter fast-path (GitHub #31) (040.14)
- TextRank conversation summarization — no LLM required (GitHub #32) (040.12)
- RAKE keyword extraction for automatic key generation (GitHub #42) (040.11)
- enhanced 6-signal composite scoring formula (GitHub #41) (040.8)
- stability-based promotion/demotion strategy (GitHub #39) (040.7)
- Bayesian confidence updates — learn from actual usage (GitHub #35) (040.6)
- adaptive stability schema + FSRS-style stability updates (GitHub #28) (040.5)
- memory health stats CLI command (GitHub #43) (040.4)
- temporal fact validity — valid_from/valid_until columns, query filtering, historical support (GitHub #29) (040.3)
- add provenance metadata columns — source_session_id, source_channel, source_message_id, triggered_by (GitHub #38) (040.2)
- switch BM25 to BM25+ variant with lower-bound delta (GitHub #34) (040.1)

### Changed
- tapps-brain v2.0.0 — research-driven upgrades (EPIC-040) (release)
- check off 040.21 in fix_plan.md
- check off 040.16 in fix_plan.md
- check off 040.15 in fix_plan.md
- check off 040.13 in fix_plan.md
- check off 040.20 in fix_plan.md
- check off 040.19 in fix_plan.md
- check off 040.18 in fix_plan
- check off 040.17 in fix_plan.md
- check off 040.14 in fix_plan.md
- check off 040.12 in fix_plan.md
- check off 040.11 in fix_plan.md
- check off 040.8 in fix_plan.md
- check off 040.7 in fix_plan.md
- check off 040.6 in fix_plan.md
- check off 040.5 in fix_plan.md
- check off 040.4 in fix_plan
- mark 040.3 complete
- mark 040.2 complete
- mark 040.1 complete

### Fixed
- resolve tool name conflicts, tier fallback, hive status counts (#9, #11, #22)

## [1.4.3] - 2026-03-25

### Added
- recalibrate profile limits based on research benchmarks (v1.4.2)
- replace custom MCP client with official @modelcontextprotocol/sdk (epic-039)
- realign OpenClaw plugin with real SDK, remove dead compat layers (epic-037-038)

### Changed
- bump tapps-brain to v1.4.3 (release)
- fix stale references after EPIC-039 SDK transport migration
- bump tapps-brain to v1.4.0 (release)

### Fixed
- add ephemeral and session tiers to MemoryTier enum
- normalize message.content and improve logging (fixes #8, #10) (openclaw-plugin)
- eliminate top-level require("openclaw") crash (openclaw-plugin)
- harden f-string SQL and replace silent exception swallowing (v1.4.1)
- add all optional ContextEngine methods to ambient types (openclaw-sdk)
- bump minimumVersion, remove stale toolGroups schema, accept bootstrap params (openclaw-plugin)
- fix BootstrapResult field name and compact param types (epic-039)

## [1.3.1] - 2026-03-24

### Added
- QA gate, OpenClaw docs, release automation (epic-034-036)
- diagnostics scorecard, v10 history, MCP/CLI, QA fixes (EPIC-030)
- MCP/CLI feedback tools, Hive propagation, integration test (story-029)
- implicit feedback reformulation and correction detection (story-029.3)
- implicit positive/negative feedback tracking (story-029.3)
- add explicit feedback API to MemoryStore (story-029.2)
- add FeedbackConfig with custom event types and strict validation (story-029.2)
- add FeedbackEvent model, FeedbackStore, and v8→v9 migration (story-029.1a)
- fix migration script to read config.plugins.entries/installs (story-033.4)
- import SDK types and fix API drift in openclaw plugin (story-033.1,033.2,033.3)
- per-agent tool routing and permissions (story-027.8)
- expose MCP resources and prompts as OpenClaw tools (story-027.7)
- register federation tools as OpenClaw native tools (story-027.2)
- register maintenance and config tools (story-027.4)
- register audit, tags, profile tools (story-027.5)
- register knowledge graph tools as OpenClaw native tools (story-027.3)
- register Hive tools as OpenClaw native tools (story-027.1)
- register lifecycle tools as OpenClaw native tools (story-027.6)
- memory-core migration tool (story-026.5)
- bidirectional MEMORY.md sync (story-026.4)
- register tapps-brain as OpenClaw memory slot plugin (story-026.1)
- add OpenClaw version compatibility layer (story-028.6)
- integrate session memory search (story-028.5)
- add citation support to recall results (story-028.4)
- add MCP client auto-reconnection (story-028.1)
- source trust multipliers for per-source scoring (M2)
- add Hive awareness to OpenClaw agents, integrity hashing, and rate limiting

### Changed
- bump tapps-brain to v1.3.1 (release)
- note 41-tool historical scope vs 54 tools today (epic-027)
- reconcile EPIC-034/035/036 and story statuses (planning)
- update STATUS.md — mark EPIC-017–028 done, add missing epics (HK-002.1)
- close resolved GitHub issues #4, #5, #6 (HK-001.1)
- Git-only install and upgrade guide (openclaw)
- v1.3.0 — flywheel/eval, docs, OpenClaw sync
- fix_plan roadmap for EPIC-029 QA through EPIC-032 (ralph)
- add unit tests for FeedbackStore.record/query (story-029.1b)
- add EPICs 029-032 for feedback, diagnostics, flywheel, and OTel
- prune fix_plan.md — all 94 tasks complete
- mark EPICs 017-028 as done with all stories checked off
- add epic planning docs and Ralph runtime artifacts
- fix pre-existing ruff lint and format violations
- complete tool reference and integration guide (story-027.9)
- integration tests for OpenClaw memory replacement (story-026.6)
- mark 026-B and 026-C as done (already implemented in 026-A commit)
- comprehensive OpenClaw integration guide (story-028.8)
- add TypeScript tests for ContextEngine (story-028.3)
- add TypeScript tests for MCP client (story-028.3)
- configuration and manifest files review (story-025.7)
- OpenClaw TypeScript plugin review (story-025.6)
- test infrastructure and benchmarks review (story-025.5)
- remaining integration tests review (story-025.4)
- federation, cross-profile, validation integration tests review (story-025.3)
- OpenClaw, profile, Hive integration tests review (story-025.2)
- MCP and retrieval integration tests review (story-025.1)
- remaining small unit tests review (story-024.14)
- trust, consolidation config, decay, BM25 tests review (story-024.13)
- contradictions, models, GC, relations tests review (story-024.12)
- markdown, reranker, embeddings tests review (story-024.11)
- foundation, promotion, IO tests review (story-024.10)
- concurrency and recall tests review (story-024.9)
- similarity and safety tests review (story-024.8)
- consolidation tests review (story-024.7)
- profile and retrieval tests review (story-024.6)
- federation and hive tests review (story-024.5)
- coverage gaps and validation tests review (story-024.4)
- store and persistence tests review (story-024.3)
- test_cli.py review (story-024.2)
- test_mcp_server.py review (story-024.1)
- auto-reformat 10 files with ruff format
- fix pre-existing lint/format issues from prior epic reviews
- metrics and OTel review (story-023.3)
- profile YAML files review (story-023.2)
- profile.py review (story-023.1)
- markdown_import.py review (story-022.7)
- io.py import/export review (story-022.6)
- cli.py advanced commands review (story-022.5)
- cli.py core commands review (story-022.4)
- mcp_server.py config and agent tools review (story-022.3)
- mcp_server.py Hive and graph tools review (story-022.2)
- mcp_server.py core tools review (lines 1–500) (story-022.1)
- relations.py knowledge graph review (story-021.4)
- hive.py registry and propagation review (story-021.3)
- hive.py HiveStore core review (story-021.2)
- federation.py cross-project review (story-021.1)
- rate limiter review (story-020.5)
- seeding bootstrap review (story-020.4)
- contradictions detection review (story-020.3)
- doc_validation.py review (story-020.2)
- safety and injection defense review (story-020.1)
- reinforcement and extraction review (story-019.5)
- GC and promotion review (story-019.4)
- auto_consolidation.py lifecycle review (story-019.3)
- consolidation.py merging review (story-019.2)
- decay.py exponential decay review (story-019.1)
- embeddings and reranker review (story-018.5)
- similarity computation review (story-018.4)
- BM25 and fusion scoring review (story-018.3)
- recall.py orchestration review (story-018.2)
- retrieval.py scoring engine review (story-018.1)
- integrity verification review (story-017.8)
- audit and session index review (story-017.7)
- protocols and feature flags review (story-017.6)
- __init__.py public API review (story-017.5)
- models.py data model review (story-017.4)
- persistence.py SQLite layer review (story-017.3)
- store.py advanced features review (story-017.2)
- style and quality cleanup from prior review loops
- store.py core CRUD review (story-017.1)
- verify updated consolidation thresholds in repo-brain profile
- sync fix_plan.md — mark BUG-001-B/C/D/E/G complete
- v1.2.0 — modernize README, update docs, bump version

### Fixed
- optional SDK imports for mypy; CliRunner; WSL/Windows venv note
- fix openclaw-plugin test failures — defensive agent guard + test mocks (033-QA)
- resolve plugin load failures and missing migration path (openclaw-plugin)
- add structured error logging to OpenClaw plugin (story-028.7)
- resolve bootstrap race condition in OpenClaw plugin (story-028.2)
- update integrity hash computation for new model fields
- update schema version assertions from v7 to v8
- inject_memories respects profile scoring weights (BUG-002-B)
- thread scoring_config through inject_memories to prevent source trust regression
- narrow exception handling in MCP Hive tools
- include server.json in version consistency check
- log warning on unknown tier fallback in decay
- prevent HiveStore connection leak on MCP handler exceptions
- restore type safety in decay_config_from_profile
- select_tier handles custom profile tier priorities
- rewrite OpenClaw plugin against real ContextEngine API (v2026.3.7)
- update openclaw-plugin/plugin.json version to 1.2.0

## [1.1.0] - 2026-03-22

### Added
- agent lifecycle and Hive stats (story-015.9)
- auto-consolidation config MCP tools and CLI (story-015.8)
- GC config MCP tools and CLI (story-015.7)
- tag management CLI commands (story-015.6)
- tag management MCP tools (story-015.5)
- audit trail CLI command (story-015.4)
- audit trail MCP tool (story-015.3)
- knowledge graph CLI commands (story-015.2)
- knowledge graph MCP tools (story-015.1)
- graceful SQLite corruption handling (story-014.3)
- CLI agent create command (story-014.2)
- validate agent_scope enum values (story-014.1)
- deploy v1.8.7 performance optimizations (ralph)
- OpenClaw plugin agent identity and Hive config (story-013.6)
- agent_create composite MCP tool (story-013.5)
- hive_propagate uses server agent identity (story-013.4)
- Hive tools reuse shared HiveStore (story-013.4)
- source_agent parameter in memory_save (story-013.3)
- agent_scope parameter in memory_save (story-013.2)
- MCP server --agent-id and --enable-hive flags (story-013.1)
- ClawHub skill directory (story-012.6)
- pyproject.toml metadata for PyPI (story-012.6)
- pre-compaction compact hook (story-012.5)
- auto-capture afterTurn hook (story-012.4)
- auto-recall ingest hook (story-012.3)
- bootstrap hook with MCP spawn (story-012.2)
- openclaw plugin skeleton (story-012.2)
- daily note import and workspace importer (story-012.1)
- markdown import parser (story-012.1)
- implement Hive — multi-agent shared brain with domain namespaces (EPIC-011)
- add configurable memory profiles with pluggable layers and scoring (EPIC-010)
- add EPICs 010-012 for configurable profiles, hive, and OpenClaw
- expose session index, search, and capture as MCP tools
- optional OpenTelemetry exporter (story-007.5)
- store.audit() convenience method (story-007.3)
- instrument lifecycle operation metrics (story-007.2)
- instrument save/get/search metrics (story-007.2)
- MCP registry server.json (story-009.4)
- entry points and unified version (story-009.3)
- optional extras for cli and mcp (story-009.1)
- curated __all__ and py.typed (story-009.2)
- merge relations on consolidation (story-006.5)
- transfer relations on supersede (story-006.5)
- graph-based recall boost (story-006.4)
- query_relations filter API (story-006.3)
- find_related graph traversal (story-006.3)
- load relations on cold start (story-006.2)
- auto-extract relations on save/ingest (story-006.2)
- relation persistence methods (story-006.1)
- MCP protocol-level integration tests (story-008.7)
- federation & maintenance MCP tools (story-008.5)
- MCP prompts, console script entry point, fix_plan update (story-008.6)
- schema v6, store health/metrics, MCP deps and tests
- add MCP server interfaces and tighten Ralph task execution (epic-008)
- implement bi-temporal fact versioning with validity windows (epic-004)
- implement auto-recall orchestrator with capture pipeline (epic-003)
- wire standalone modules into MemoryStore runtime — 839 tests, 97.17% coverage (epic-002)
- raise test suite to A+ — 792 tests, 96.59% coverage (epic-001)

### Changed
- final validation and status update (epic-016)
- unicode and boundary value tests (story-016.6)
- concurrent GC and Hive stress tests (story-016.4)
- concurrent save and recall stress tests (story-016.3)
- CLI gc archive and agent create error tests (story-016.2)
- CLI federation command tests (story-016.1)
- final validation and status update (epic-015)
- final validation and status update (epic-014)
- CHANGELOG.md (story-014.5)
- getting started guide (story-014.4)
- add EPIC-015 — Analytics & Operational Surface (graph, audit, tags, GC, consolidation)
- fix grep stat parsing in hooks, strengthen QA-skip rules, add mypy ignores
- add EPIC-014 — hardening (validation, CLI parity, resilience, onboarding docs)
- mark EPIC-013 complete — update all status markers, acceptance criteria, and tool count
- remove stale noqa E501 directive in test_mcp_server.py
- final validation and status update (epic-013)
- sync Ralph config updates and EPIC-013 test/formatting artifacts
- multi-agent Hive round-trip integration tests (story-013.8)
- multi-agent Hive patterns in OpenClaw guide (story-013.7)
- add EPIC-013 — Hive-aware MCP surface for multi-agent OpenClaw wiring
- add Profile Design Guide, Hive Guide, Profile Catalog; rewrite README
- sync pending doc and config updates
- mark EPIC-012 complete — update all status markers and acceptance criteria
- deploy Ralph v1.2.0 hooks, agents, and skills
- clean orphaned temp files, add feedback report, update gitignore
- final validation and status update (epic-012)
- ClawHub submission guide (story-012.6)
- PyPI publish checklist (story-012.6)
- version consistency check (story-012.6)
- openclaw guide with ContextEngine plugin (story-012.7)
- recall capture round-trip integration (story-012.7)
- markdown import integration tests (story-012.7)
- markdown import unit tests (story-012.1)
- add Ralph hooks, Claude Code project config, and updated tooling
- break EPIC-011 into Ralph tasks and update project status
- sync epic statuses — mark EPICs 005-010 stories done, check all acceptance criteria
- mark EPICs 006-009 done, add EPIC-010 tasks to fix_plan
- add MCP protocol integration tests and fix persistence method call
- add configurable memory profiles and hive architecture design
- add OpenClaw integration guide and deployment plan
- rewrite README in polished GitHub style
- update all docs for session index, search, and capture MCP tools
- bump Ralph timeout and document JSONL crash bug
- observability integration tests (story-007.6)
- extras-aware test markers (story-009.5)
- add ralph runtime files to gitignore, stage pending changes
- graph lifecycle integration tests (story-006.6)
- tune for Max Plan unattended operation (ralph)
- configure for unattended overnight runs (ralph)
- MCP server guide with client config examples (story-008)
- sync fix_plan with completed work, require checkoffs (ralph)
- Ralph setup guide, WSL scripts, optimized .ralphrc settings
- default integrated terminal to WSL Ubuntu (workspace) (vscode)
- add WSL Claude Code upgrade script (user-local npm) (scripts)
- add WSL background Ralph launcher and PS1 wrapper (scripts)
- add WSL Ralph setup scripts and CLAUDE.md notes
- add explicit done-when criteria to fix plan (ralph)
- add Ralph autonomous loop configuration
- bump version to 1.1.0, fix lint and format issues
- update README for EPIC-003/004, fix mypy error, plan EPICs 005-007
- add planned EPIC-003 (auto-recall) and EPIC-004 (bi-temporal versioning)
- update README, PLANNING.md for Epic 2 completion
- mark all acceptance criteria complete after CI pass (epic-001)
- fix ruff lint errors (TC001, F841, E501, I001)

### Fixed
- close leaked SQLite connections in tests (story-016.5)
- repair Ralph WSL/Windows version divergence and on-stop hook parsing
- reinforce STATUS rules with scenarios and CRITICAL note (ralph)
- use IN_PROGRESS status for completed tasks with remaining work (ralph)
- run background Ralph in tmux (survives WSL exit) (scripts)

## [1.0.1] - 2026-03-19

### Changed
- run ruff format on all files for CI compliance
- remove PyPI publish workflow (private repo, not needed)

### Fixed
- use venv for build job, add .gitattributes for LF enforcement (ci)
- resolve all ruff, mypy, and formatting issues for CI

## [1.0.0] - 2026-03-19

### Added
- initial tapps-brain v1.0.0 - standalone memory system

### Changed
- add PyPI publish workflow via OIDC trusted publishing
