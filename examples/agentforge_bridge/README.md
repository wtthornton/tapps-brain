# AgentForge BrainBridge — Reference Port (STORY-070.13)

This directory contains a **documentation artefact** — a reference port of
AgentForge's internal `BrainBridge` class using `AsyncTappsBrainClient`.

> **This code does not become a runtime dependency of tapps-brain.**
> It lives in `examples/` to show maintainers that the client surface is
> actually sufficient to replace an embedded-library integration.

---

## What was the original BrainBridge?

Inside AgentForge, `backend/memory/brain.py` (`BrainBridge`) was ~925 LOC.  It
handled:

| Feature | How the original did it |
|---|---|
| Connection pooling | `BrainPool` — a bespoke `threading.local` pool over a sync `MemoryStore` |
| Async bridging | `asyncio.to_thread` wrappers around every sync call |
| Circuit breaker | 3-state FSM with lock and `time.monotonic` |
| Bounded write queue | `asyncio.Queue(maxsize=…)` + drain task |
| Exponential backoff | Inline retry loops with `await asyncio.sleep(2**attempt)` |
| Session-local agents | `agent_id` passed to every `MemoryStore` call |
| Error mapping | `try/except` on every sync call, manually mapped to agent errors |

---

## What the port eliminates

This port targets **< 250 non-blank, non-comment lines** (vs ~925 original).

### 1 — BrainPool removed

`TappsBrainClient` handles connection pooling internally via `httpx.AsyncClient`.
AgentForge workers get a single shared `BrainBridge` instance instead of one
pool entry per thread.

### 2 — asyncio.to_thread removed

`AsyncTappsBrainClient` is natively async — there is no sync `MemoryStore` to
wrap.

### 3 — Inline retry loops removed

The client handles retries transparently (up to `max_retries`, default 2)
with idempotency keys.  The bridge does not retry independently.

### 4 — Error mapping removed

`AsyncTappsBrainClient` raises typed exceptions from `tapps_brain.errors`
(`BrainDegradedError`, `BrainRateLimitedError`, etc.).  AgentForge error
handlers map those directly — no manual `try/except` in the bridge.

---

## What the port keeps

### Circuit breaker (`_CircuitBreaker`)

Three-state FSM (CLOSED → OPEN → HALF_OPEN) that protects **recall** and
**health** calls.  Fire-and-forget writes go through the write queue, which
handles failures via the drain worker.

```
CLOSED ──(threshold failures)──► OPEN ──(recovery_timeout)──► HALF_OPEN
  ▲                                                                 │
  └─────────────── success ────────────────────────────────────────┘
```

### Bounded write queue (`_BoundedWriteQueue`)

`asyncio.Queue(maxsize=…)` with a background drain task.  When the queue is
full, new writes are **dropped** (not blocked) and logged.  This prevents
worker coroutines from stalling under backpressure.

---

## Usage

```python
import asyncio
import os
from examples.agentforge_bridge.brain_bridge import BrainBridge

async def main() -> None:
    async with BrainBridge(
        url="http://brain.internal:8080",
        project_id="agentforge-prod",
        agent_id="worker-42",
        auth_token=os.environ["TAPPS_BRAIN_AUTH_TOKEN"],
    ) as bridge:
        # Fire-and-forget memory write (never blocks the worker)
        await bridge.remember("prefer ruff over flake8", tier="procedural")

        # Synchronous recall with circuit breaker protection
        results = await bridge.recall("linting conventions", max_results=5)
        for r in results:
            print(r["key"], "→", r["value"])

        # Record outcomes
        await bridge.learn_success("Ran CI pipeline", task_id="run-123")
        await bridge.learn_failure("Deploy timed out", error="Timeout", task_id="deploy-456")

        # Health check (includes circuit state)
        h = await bridge.health()
        print(h)  # {"status": "ok", "circuit_state": "closed", "write_queue_dropped": 0}

asyncio.run(main())
```

---

## Configuration

| Parameter | Default | Description |
|---|---|---|
| `url` | `http://localhost:8080` | tapps-brain HTTP adapter URL |
| `project_id` | (required) | tapps-brain project identifier |
| `agent_id` | (required) | Worker / agent identifier |
| `auth_token` | `None` | Bearer token for the HTTP adapter |
| `write_queue_size` | `256` | Max queued fire-and-forget writes |
| `circuit_failure_threshold` | `5` | Consecutive failures before circuit opens |
| `circuit_recovery_timeout` | `30.0` | Seconds before OPEN → HALF_OPEN |

---

## Running the tests

**Unit tests only** (no live brain required):

```bash
pytest examples/agentforge_bridge/test_brain_bridge.py -v -m "not requires_brain"
```

**Integration tests** (requires a running tapps-brain HTTP adapter):

```bash
# Start the brain
docker compose -f docker/docker-compose.hive.yaml up -d
# or: tapps-brain serve

# Run all tests
pytest examples/agentforge_bridge/test_brain_bridge.py -v
```

---

## Gaps filed as follow-up stories

During the port, no blocking gaps were found in the client surface.  The
following optional enhancements were noted for potential follow-up:

- **Metrics / Prometheus export** — the original exposed Prometheus counters for
  `brain_write_dropped_total` and `brain_circuit_opens_total`.  A thin
  OpenTelemetry integration could be added if AgentForge's observability stack
  requires it.
- **Structured logging** — the original used structlog with bound context.
  This port uses the stdlib `logging` module with `extra={}`.
- **Per-worker agent_id override** — this port accepts a single `agent_id` at
  construction time; if AgentForge workers need per-task identity, a `context`
  parameter could be added to `remember` / `recall`.

These are improvements, not blockers.  The client surface is sufficient for a
full port.
