# TappsBrainClient — official Python client

`TappsBrainClient` (sync) and `AsyncTappsBrainClient` (async) let you consume a
**remote** tapps-brain deployment from any Python process — Claude Code sessions,
AgentForge workers, CI scripts, your own code — without embedding a local
`MemoryStore`.

---

## Quickstart

### Install

```bash
pip install "tapps-brain[client]"   # pulls in httpx
```

### Sync client

```python
from tapps_brain.client import TappsBrainClient

with TappsBrainClient(
    "http://brain.internal:8080",
    project_id="my-project",
    agent_id="my-agent",
    auth_token="<token>",        # optional in local-dev deployments
) as brain:
    brain.remember("Use ruff for linting")
    results = brain.recall("linting conventions", max_results=3)
    brain.learn_success("Lint pass on PR #42")
```

### Async client

```python
import asyncio
from tapps_brain.client import AsyncTappsBrainClient

async def main() -> None:
    async with AsyncTappsBrainClient(
        "http://brain.internal:8080",
        project_id="my-project",
        agent_id="my-agent",
        auth_token="<token>",
    ) as brain:
        await brain.remember("Use ruff for linting")
        results = await brain.recall("linting conventions")

asyncio.run(main())
```

A runnable end-to-end async script lives at
[`examples/client_quickstart.py`](../../examples/client_quickstart.py) — it picks
up `TAPPS_BRAIN_URL` / `TAPPS_BRAIN_AUTH_TOKEN` / `TAPPS_BRAIN_PROJECT` /
`TAPPS_BRAIN_AGENT_ID` from the environment and runs a `remember` → `recall` →
`forget` round trip against a local Docker brain.

---

## Transport selection

The URL scheme picks the transport. **v3.7.3+:** both schemes route to the same
Streamable-HTTP MCP endpoint at `/mcp/` on the deployed brain container
(TAP-509 collapsed the v3.7.2 `/mcp/mcp` workaround by pinning FastMCP's inner
`streamable_http_path` to `/`). The `mcp+stdio://` subprocess transport was
removed in v3.7.0 — see [Migration 3.6 → 3.7](migration-3.6-to-3.7.md).

| URL prefix | Transport |
|------------|-----------|
| `http://` / `https://` | Streamable-HTTP MCP (alias for `mcp+http://`) |
| `mcp+http://` | Streamable-HTTP MCP (STORY-070.1) |

```python
# Either form works — both hit /mcp/ on the deployed container.
TappsBrainClient("http://brain.internal:8080", ...)
TappsBrainClient("mcp+http://brain.internal:8080", ...)
```

> **Required headers (v3.5+).** Every request carries `X-Project-Id` (must match
> a project in the brain's registry — otherwise 403 `project_not_registered`)
> and `Authorization: Bearer $TAPPS_BRAIN_AUTH_TOKEN`. Both are set by the
> client from its constructor args / env vars — no manual header wiring needed.

---

## Configuration via environment variables

All identity constructor parameters fall back to environment variables:

| Parameter | Env var | Default |
|-----------|---------|---------|
| `project_id` | `TAPPS_BRAIN_PROJECT` | `"default"` |
| `agent_id` | `TAPPS_BRAIN_AGENT_ID` | `"unknown"` |
| `auth_token` | `TAPPS_BRAIN_AUTH_TOKEN` | *(none)* |

Networking parameters (`connect_timeout`, `read_timeout`, `retry_config`) have
constructor-only defaults — see [Timeouts](#timeouts) and [Retry](#retry).

---

## Method reference

Both `TappsBrainClient` and `AsyncTappsBrainClient` expose the same methods
(async variants return `Awaitable`):

| Method | Description |
|--------|-------------|
| `remember(fact, *, tier, share, share_with, agent_scope, memory_group, agent_id)` | Save a memory; returns key |
| `recall(query, *, max_results, agent_id)` | Search memories |
| `forget(key, agent_id)` | Archive a memory by key |
| `learn_success(description, *, task_id, agent_id)` | Record success |
| `learn_failure(description, *, task_id, error, agent_id)` | Record failure |
| `memory_save(key, value, **kwargs)` | Save a raw entry |
| `memory_get(key)` | Retrieve an entry by key |
| `memory_search(query, **kwargs)` | Full-text / semantic search |
| `memory_recall(query, **kwargs)` | Auto-recall for a query |
| `memory_reinforce(key, *, confidence_boost)` | Reinforce a memory |
| `memory_save_many(entries, agent_id)` | Bulk save |
| `memory_recall_many(queries, agent_id)` | Bulk recall |
| `memory_reinforce_many(entries, agent_id)` | Bulk reinforce |
| `status(agent_id)` | Return agent status |
| `health()` | Return brain health report |

---

## Error handling

Errors raised by both clients fall into one of two layers that compose:

- **Semantic taxonomy** (`tapps_brain.exceptions`) — catch by *intent*. Stable
  across wire-format changes; this is what you should pattern-match on in
  production code.
- **Wire-code aliases** (`tapps_brain.errors`) — catch by specific HTTP status
  + body `error` code (e.g. 503 + `brain_degraded`). Each alias multi-inherits
  from its matching semantic supertype, so old `except BrainDegradedError`
  blocks still fire and new code can catch the broader type without rewriting.

### Hierarchy

```
TappsBrainError                       ← root; catch this for "any SDK error"
├── TappsBrainTransportError          ← network failed before reaching the brain
├── TappsBrainAuthError               ← 401 / 403
│   └── ProjectNotFoundError          ← 403 + error=project_not_registered
├── TappsBrainTransientError          ← 429 / 5xx — safe to retry
│   ├── BrainDegradedError            ← 503 + error=brain_degraded
│   ├── BrainRateLimitedError         ← 429 + error=brain_rate_limited
│   └── InternalError                 ← 500 + error=internal_error
├── TappsBrainNotFoundError           ← 404
│   └── NotFoundError                 ← 404 + error=not_found
└── TappsBrainValidationError         ← 400 / 409 / 422
    ├── InvalidRequestError           ← 400 + error=invalid_request
    └── IdempotencyConflictError      ← 409 + error=idempotency_conflict
```

`TappsBrainTransportError` wraps `httpx.RequestError` (DNS, TCP refused, TLS,
timeout) — the request never reached the brain, so no HTTP status is attached.

### Catch by intent

```python
from tapps_brain.client import AsyncTappsBrainClient
from tapps_brain.exceptions import (
    TappsBrainAuthError,
    TappsBrainTransientError,
    TappsBrainTransportError,
    TappsBrainValidationError,
)

async with AsyncTappsBrainClient(url, auth_token=tok) as brain:
    try:
        await brain.remember("Use ruff for linting")
    except TappsBrainAuthError:
        # Bad token or unregistered project_id. Fix config and stop.
        raise
    except TappsBrainValidationError:
        # The request body is malformed or conflicts with server state.
        raise
    except TappsBrainTransientError:
        # SDK retry layer already gave up — escalate to a human.
        log.warning("brain transient failure; retries exhausted")
    except TappsBrainTransportError:
        # Brain is unreachable. Degrade gracefully.
        log.warning("brain unreachable; degrading")
```

### Catch by wire code

When you need to react to a specific code (e.g. surface the rate limit to a
user), catch the wire-code alias. It's still a `TappsBrainTransientError`, so
broader handlers will still fire if you let it propagate:

```python
from tapps_brain.errors import BrainRateLimitedError, ProjectNotFoundError

try:
    brain.remember("fact")
except BrainRateLimitedError:
    log.info("rate limited; waiting for the next window")
except ProjectNotFoundError as exc:
    log.error("project_id %r is not registered with the brain", exc.project_id)
```

See [`errors.md`](errors.md) for the complete wire-code table (status, JSON-RPC
code, documented retry policy).

---

## Retry

Retries are **off by default**. Pass a `RetryConfig` to opt in:

```python
from tapps_brain.client import TappsBrainClient, RetryConfig

client = TappsBrainClient(
    "http://brain.internal:8080",
    retry_config=RetryConfig(),     # 3 attempts, 0.5s base, ±20% jitter, 30s cap
)
```

### `RetryConfig` fields

| Field | Default | Meaning |
|-------|---------|---------|
| `max_attempts` | `3` | Total attempts including the first. Set to `1` to disable retry. |
| `base_delay` | `0.5` | Initial backoff in seconds; doubles each attempt. |
| `jitter` | `True` | Multiply the delay by `random.uniform(0.8, 1.2)` to break thundering herds. |
| `max_delay` | `30.0` | Hard cap on the computed delay. Server-supplied `Retry-After` hints bypass this cap. |

### What gets retried

| Exception | Retried? |
|-----------|----------|
| `TappsBrainTransientError` (429 / 5xx) | yes |
| `TappsBrainAuthError`, `TappsBrainValidationError`, `TappsBrainNotFoundError` | no — permanent until you fix the request |
| `TappsBrainTransportError` (DNS / TCP / TLS) | no — usually a misconfigured URL or downed brain |

### Backoff precedence

For each retry the SDK picks the longest of three values, in order:

1. Server `Retry-After` HTTP header (delta-seconds form).
2. `retry_after` field in the JSON response body.
3. Computed `base_delay * 2 ** attempt`, capped at `max_delay`.

Server-supplied hints (1) and (2) bypass `max_delay` — the server knows better
than the client when it will be ready. Jitter, when enabled, applies to all
three.

### Idempotency on retry

Write operations (`remember`, `learn_success`, `memory_save`,
`memory_reinforce`, plus their `_many` variants) auto-generate a UUID
idempotency key on the first attempt and **reuse the same key on every retry**.
The server dedups by key, so the visible side effect is at-most-once even when
the network drops a response mid-flight. You do not manage these keys yourself.

### Legacy `max_retries`

The pre-STORY-071.2 single-knob `max_retries` parameter still works:

```python
TappsBrainClient("http://brain.internal:8080", max_retries=5)
```

It derives a back-compat `RetryConfig(max_attempts=max_retries + 1,
base_delay=1.0, jitter=True, max_delay=30.0)` matching the v3.6.x sleep schedule
exactly. Prefer `retry_config=` in new code; `max_retries` is kept only so
callers built against the v3.6.x line keep working.

---

## Timeouts

Two per-leg timeouts, both in seconds:

| Parameter | Default | Applies to |
|-----------|---------|-----------|
| `connect_timeout` | `5.0` | TCP / TLS handshake. |
| `read_timeout` | `30.0` | Each `recv()` while reading the response. Also covers write and pool acquisition. |

```python
TappsBrainClient(
    "http://brain.internal:8080",
    connect_timeout=2.0,
    read_timeout=10.0,
)
```

The brain is normally on the same host or LAN, so a 5-second TCP handshake is
already an outlier worth failing fast on. Bump `read_timeout` for tools that
fan out into large MCP responses (`memory_search` with a high `limit`,
`hive_status` on a brain with many namespaces).

A blown timeout raises `TappsBrainTransportError` — the underlying
`httpx.TimeoutException` is translated by the SDK so you only have to catch
one type.

### Legacy `timeout=`

The single-knob `timeout=` parameter still works and applies to both legs unless
a per-leg parameter is also set:

```python
TappsBrainClient("http://brain.internal:8080", timeout=15.0)   # connect=read=15s
```

Prefer per-leg parameters in new code.

---

## Pool lifecycle

Both clients hold an `httpx.Client` (sync) or `httpx.AsyncClient` (async) with
a connection pool. **Reuse one client across calls** — constructing a new
client per call defeats pooling, re-runs the MCP `initialize` handshake every
time, and leaks sockets in long-running agents.

### Sync — `close()` or `with`

```python
# Context manager — preferred for scoped lifetimes:
with TappsBrainClient(url, ...) as brain:
    brain.remember("...")
    brain.recall("...")

# Explicit close — for clients pinned to an outer scope:
brain = TappsBrainClient(url, ...)
try:
    brain.remember("...")
finally:
    brain.close()
```

### Async — `aclose()` or `async with`

```python
# Context manager — preferred:
async with AsyncTappsBrainClient(url, ...) as brain:
    await brain.remember("...")
    await brain.recall("...")

# Explicit aclose() — for clients that outlive their construction site
# (FastAPI app, agent worker, queue consumer):
brain = AsyncTappsBrainClient(url, ...)
try:
    await brain.remember("...")
finally:
    await brain.aclose()
```

`AsyncTappsBrainClient.close()` is a back-compat alias for `aclose()` so callers
that share a code path with the sync client don't need to special-case the
async surface. Both `close()` and `aclose()` are idempotent — calling them
twice is a no-op.

### Don't do this

```python
# ❌  New client per call → new TCP connection, new MCP handshake, leaked
#     sockets on early exception. Especially bad inside a hot loop.
async def remember_once(fact: str) -> None:
    async with AsyncTappsBrainClient(url) as brain:
        await brain.remember(fact)

# ✅  Reuse one client.
brain = AsyncTappsBrainClient(url)
try:
    for fact in facts:
        await brain.remember(fact)
finally:
    await brain.aclose()
```

For long-running services, construct one `AsyncTappsBrainClient` at startup and
call `await brain.aclose()` during shutdown (FastAPI `lifespan`, a `try/finally`
in your worker's main loop, etc.).

---

## Protocol

Both clients implement `BrainClientProtocol`, a runtime-checkable
[`typing.Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)
so shared agent code can type-hint against the protocol instead of the concrete
class:

```python
from tapps_brain.client import BrainClientProtocol

def run_agent(brain: BrainClientProtocol) -> None:
    brain.remember("Starting task")
    results = brain.recall("relevant context")
    ...
```

---

## AgentForge / AGENT.md integration

See [`agentforge-integration.md`](agentforge-integration.md) for a complete
example of wiring `TappsBrainClient` into an AgentForge worker via `AGENT.md`.
