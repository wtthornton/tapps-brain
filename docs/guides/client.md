# TappsBrainClient — official Python client

`TappsBrainClient` (sync) and `AsyncTappsBrainClient` (async) let you consume a
**remote** tapps-brain deployment from any Python process — Claude Code sessions,
AgentForge workers, CI scripts, or your own code — without embedding a local
`MemoryStore`.

---

## Quick start

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
    auth_token="<token>",        # optional
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
    ) as brain:
        await brain.remember("Use ruff for linting")
        results = await brain.recall("linting conventions")

asyncio.run(main())
```

---

## Transport selection

The URL scheme picks the transport automatically:

| URL prefix | Transport |
|------------|-----------|
| `http://` / `https://` | Direct HTTP to the HTTP adapter (STORY-070.3) |
| `mcp+stdio://` | Spawns `tapps-brain-mcp` subprocess |
| `mcp+http://` | Streamable-HTTP MCP (STORY-070.1) |

```python
# HTTP adapter (most common for deployed brains)
TappsBrainClient("http://brain.internal:8080", ...)

# MCP subprocess (local / offline)
TappsBrainClient("mcp+stdio://localhost", ...)

# Streamable-HTTP MCP
TappsBrainClient("mcp+http://brain.internal:8080", ...)
```

---

## Configuration via environment variables

All constructor parameters fall back to environment variables:

| Parameter | Env var | Default |
|-----------|---------|---------|
| `project_id` | `TAPPS_BRAIN_PROJECT` | `"default"` |
| `agent_id` | `TAPPS_BRAIN_AGENT_ID` | `"unknown"` |
| `auth_token` | `TAPPS_BRAIN_AUTH_TOKEN` | *(none)* |

---

## Method reference

Both `TappsBrainClient` and `AsyncTappsBrainClient` expose the same methods
(async variants return `Awaitable`):

| Method | Description |
|--------|-------------|
| `remember(fact, *, tier, share, share_with, agent_id)` | Save a memory; returns key |
| `recall(query, *, max_results, agent_id)` | Search memories |
| `forget(key, agent_id)` | Archive a memory by key |
| `learn_success(task_description, *, task_id, agent_id)` | Record success |
| `learn_failure(description, *, task_id, error, agent_id)` | Record failure |
| `memory_save(key, value, **kwargs)` | Save a raw entry |
| `memory_get(key)` | Retrieve an entry by key |
| `memory_search(query, **kwargs)` | Full text / semantic search |
| `memory_recall(message, **kwargs)` | Auto-recall for a message |
| `memory_reinforce(key, *, confidence_boost)` | Reinforce a memory |
| `memory_save_many(entries, agent_id)` | Bulk save |
| `memory_recall_many(queries, agent_id)` | Bulk recall |
| `memory_reinforce_many(entries, agent_id)` | Bulk reinforce |
| `status(agent_id)` | Return agent status |
| `health()` | Return brain health report |

---

## Error handling

Server errors are translated into typed exceptions from `tapps_brain.errors`:

```python
from tapps_brain.errors import (
    BrainDegradedError,       # 503 — Postgres unavailable, retry safe
    BrainRateLimitedError,    # 429 — rate limit, honour Retry-After
    ProjectNotFoundError,     # 403 — unregistered project_id
    InvalidRequestError,      # 400 — bad request
    IdempotencyConflictError, # 409 — idempotency key conflict
    NotFoundError,            # 404 — resource not found
    InternalError,            # 500 — unexpected server error
    TaxonomyError,            # base for all of the above
)
```

Example:

```python
from tapps_brain.client import TappsBrainClient
from tapps_brain.errors import BrainDegradedError, BrainRateLimitedError

with TappsBrainClient("http://brain.internal:8080", project_id="p") as brain:
    try:
        brain.remember("fact")
    except BrainRateLimitedError as exc:
        print(f"Rate limited, retry after {exc.details.get('retry_after')}s")
    except BrainDegradedError:
        print("Brain unavailable — will retry on next invocation")
```

---

## Idempotency

Write operations (`remember`, `learn_success`, `memory_save`, etc.) automatically
generate a **UUID idempotency key** before the first attempt.  If the call is
retried due to a `503 brain_degraded` or `429 brain_rate_limited` response, the
**same key** is reused.  This ensures the server can deduplicate the write even
when the client cannot distinguish "request lost in transit" from "request
processed but response lost".

You do not need to manage idempotency keys yourself.

---

## Retry behaviour

| Error code | Retry policy | Max retries |
|------------|-------------|-------------|
| `brain_degraded` (503) | Exponential back-off | `max_retries` (default 2) |
| `brain_rate_limited` (429) | Honour `Retry-After` | `max_retries` (default 2) |
| `internal_error` (500) | Once | `max_retries` (default 2) |
| All others | Never | — |

Customise `max_retries`:

```python
TappsBrainClient("http://...", max_retries=5)
```

---

## Protocol

Both clients implement `BrainClientProtocol`, a runtime-checkable
[`typing.Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)
so you can type-hint shared agent code against the protocol instead of a
concrete class:

```python
from tapps_brain.client import BrainClientProtocol

def run_agent(brain: BrainClientProtocol) -> None:
    brain.remember("Starting task")
    results = brain.recall("relevant context")
    ...
```

---

## AgentForge / AGENT.md integration

See [`docs/guides/agentforge-integration.md`](agentforge-integration.md) for a
complete example of wiring `TappsBrainClient` into an AgentForge worker via
`AGENT.md`.
