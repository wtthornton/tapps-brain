---
id: EPIC-071
title: "TappsBrainClient & AsyncTappsBrainClient — SDK Hardening and Documentation"
status: planned
priority: high
created: 2026-04-15
tags: [sdk, client, http, async, documentation, v3]
depends_on: [EPIC-070]
blocks: []
---

# EPIC-071: TappsBrainClient & AsyncTappsBrainClient — SDK Hardening and Documentation

## Goal

Harden the `TappsBrainClient` and `AsyncTappsBrainClient` HTTP clients shipped in v3.6.0 with proper error classification, retry semantics, connection lifecycle management, and a complete usage guide — so any remote agent or application can reliably consume tapps-brain over HTTP without bespoke networking code.

## Motivation

v3.6.0 added `TappsBrainClient` and `AsyncTappsBrainClient` as thin HTTP wrappers over the REST API. These are the primary integration path for remote agents (AgentForge, future cloud deployments), but shipped without:
- Documented exception taxonomy (transient vs permanent vs auth errors)
- Retry semantics (exponential backoff, circuit breaking)
- Connection pool lifecycle guidance for long-running agents
- A short quickstart guide for integrators

Without this hardening, every integrator reimplements retries and error handling differently, and the SDK is effectively unusable in production.

## Acceptance Criteria

- [ ] Documented exception hierarchy: `TappsBrainError` → `TappsBrainAuthError` / `TappsBrainTransientError` / `TappsBrainNotFoundError`
- [ ] Configurable retry: max attempts, backoff, jitter — off by default, opt-in via `retry_config` param
- [ ] `AsyncTappsBrainClient` uses `httpx.AsyncClient` with proper `async with` lifecycle (not a new client per call)
- [ ] Connection timeout and read timeout documented and configurable
- [ ] Integration tests: auth failure → `TappsBrainAuthError`; server 503 → retry behavior; clean close
- [ ] `docs/guides/client.md` updated with quickstart, error handling, retry, and pool lifecycle sections
- [ ] `examples/` contains a minimal script showing async client usage with `async with`

## Stories

### STORY-071.1: Exception taxonomy and error classification

**Status:** planned
**Size:** S
**Depends on:** —

#### Why

Integrators need typed exceptions to write correct error-handling code without parsing status codes.

#### Acceptance criteria

- [ ] `exceptions.py` defines `TappsBrainError`, `TappsBrainAuthError` (401/403), `TappsBrainTransientError` (429/5xx), `TappsBrainNotFoundError` (404), `TappsBrainValidationError` (422).
- [ ] `TappsBrainClient` and `AsyncTappsBrainClient` raise typed exceptions instead of raw `httpx` errors.
- [ ] Unit tests: each HTTP status code maps to the correct exception type.

#### Verification

- `pytest tests/unit/test_client.py` — exception mapping tests.

---

### STORY-071.2: Retry semantics with exponential backoff

**Status:** planned
**Size:** M
**Depends on:** STORY-071.1

#### Why

Transient failures (503, 429) are expected in prod; retrying in the SDK beats every integrator writing their own loop.

#### Acceptance criteria

- [ ] `RetryConfig(max_attempts=3, base_delay=0.5, jitter=True)` — off by default (`RetryConfig(max_attempts=1)`).
- [ ] Only retries `TappsBrainTransientError`; passes through permanent errors immediately.
- [ ] Respects `Retry-After` header when present.
- [ ] Unit tests: retry count, backoff interval bounds, no retry on 4xx.

#### Verification

- `pytest tests/unit/test_client_retry.py`

---

### STORY-071.3: AsyncTappsBrainClient lifecycle fix

**Status:** planned
**Size:** S
**Depends on:** —

#### Why

Creating a new `httpx.AsyncClient` per call defeats connection pooling and leaks resources in long-running agents.

#### Acceptance criteria

- [ ] `AsyncTappsBrainClient` uses a single `httpx.AsyncClient` across calls; supports `async with` context manager.
- [ ] `aclose()` method for explicit cleanup outside `async with`.
- [ ] Existing `TappsBrainClient` reviewed for equivalent `httpx.Client` session reuse.
- [ ] Unit test: same client instance used across multiple calls.

#### Verification

- Code review + unit test for instance identity.

---

### STORY-071.4: Connection and timeout configuration

**Status:** planned
**Size:** S
**Depends on:** STORY-071.3

#### Why

Operators must tune timeouts for their deployment; defaults must be safe for agent hot paths.

#### Acceptance criteria

- [ ] `connect_timeout` (default 5s), `read_timeout` (default 30s) configurable in constructor.
- [ ] Timeouts documented in `docs/guides/client.md`.
- [ ] Unit test: timeout config propagated to `httpx`.

#### Verification

- Code review + docs review.

---

### STORY-071.5: Integration tests — auth, transient, and lifecycle

**Status:** planned
**Size:** M
**Depends on:** STORY-071.1, STORY-071.2, STORY-071.3

#### Why

Unit tests can mock; integration tests catch wiring bugs between retry, exceptions, and the real HTTP stack.

#### Acceptance criteria

- [ ] Integration test: wrong token → `TappsBrainAuthError` on first try; no retry.
- [ ] Integration test: server returns 503 twice then 200 → succeeds after retry (with `RetryConfig(max_attempts=3)`).
- [ ] Integration test: client used as `async with` → connection properly closed.
- [ ] Uses `httpx` mock transport or `respx` — no live server required.

#### Verification

- `pytest tests/integration/test_client_integration.py`

---

### STORY-071.6: Client guide and quickstart example

**Status:** planned
**Size:** S
**Depends on:** STORY-071.4, STORY-071.5

#### Why

Discovery and onboarding are part of the SDK product.

#### Acceptance criteria

- [ ] `docs/guides/client.md` updated with: quickstart (sync + async), error handling, retry config, timeout, pool lifecycle, `async with` pattern.
- [ ] `examples/client_quickstart.py` — minimal script demonstrating async client with `async with`, `remember`, `recall`, error handling.
- [ ] Cross-linked from `README.md` and `docs/guides/agentforge-integration.md`.

#### Verification

- Doc review; script runs without errors against a local Docker deployment.

## Out of scope

- WebSocket or gRPC transport (REST + MCP cover current needs)
- OAuth / OIDC authentication flows (bearer token is sufficient for v3)
- Client-side caching of recall results

## References

- `src/tapps_brain/client.py` (or `http_client.py`) — v3.6.0 implementation
- `docs/guides/client.md`
- [EPIC-070](EPIC-070.md) — HTTP/MCP transport parity (foundation)
- [EPIC-062](EPIC-062.md) — env contract (DSN / auth token vars)
