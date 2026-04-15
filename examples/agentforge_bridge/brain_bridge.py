"""AgentForge BrainBridge — reference port using TappsBrainClient (STORY-070.13).

This is a **documentation artefact**, not a runtime dependency.  It shows how
AgentForge's ~925 LOC BrainBridge can be replaced by a thin adapter over
:class:`~tapps_brain.client.AsyncTappsBrainClient` while keeping the same
resilience features (circuit breaker, bounded write queue).

Key reductions vs the original embedded-library BrainBridge:
- No BrainPool: TappsBrainClient handles connection pooling internally.
- No asyncio.to_thread wrapping: AsyncTappsBrainClient is natively async.
- Circuit breaker stays: wraps the client, not the store.
- Bounded write queue stays: prevents unbounded memory under backpressure.

Target LOC: < 250 (vs ~925 original).

Usage::

    bridge = BrainBridge(
        url="http://brain.internal:8080",
        project_id="agentforge-prod",
        agent_id="worker-42",
        auth_token=os.environ["TAPPS_BRAIN_AUTH_TOKEN"],
    )
    await bridge.start()

    # In each AgentForge worker coroutine:
    await bridge.remember("preference: use ruff for Python linting")
    results = await bridge.recall("linting setup")
    await bridge.learn_success("Set up ruff in CI")

    await bridge.stop()
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class _State(enum.Enum):
    CLOSED = "closed"       # healthy — requests pass through
    OPEN = "open"           # tripped — requests fail fast
    HALF_OPEN = "half_open"  # probing — one request allowed through


class _CircuitBreaker:
    """Simple three-state circuit breaker.

    Opens after *failure_threshold* consecutive failures; resets after
    *recovery_timeout* seconds in OPEN state.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ) -> None:
        self._threshold = failure_threshold
        self._recovery = recovery_timeout
        self._state = _State.CLOSED
        self._failures = 0
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> _State:
        return self._state

    async def call(self, coro: Any) -> Any:
        async with self._lock:
            if self._state == _State.OPEN:
                if time.monotonic() - self._opened_at >= self._recovery:
                    self._state = _State.HALF_OPEN
                else:
                    raise BrainBridgeCircuitOpenError("Circuit is OPEN; request rejected")
        try:
            result = await coro
            async with self._lock:
                self._failures = 0
                self._state = _State.CLOSED
            return result
        except Exception as exc:
            async with self._lock:
                self._failures += 1
                if self._failures >= self._threshold or self._state == _State.HALF_OPEN:
                    self._state = _State.OPEN
                    self._opened_at = time.monotonic()
                    logger.warning(
                        "brain_bridge.circuit_opened",
                        extra={"failures": self._failures, "error": str(exc)},
                    )
            raise


class BrainBridgeCircuitOpenError(RuntimeError):
    """Raised when the circuit breaker is open and a call is rejected."""


# ---------------------------------------------------------------------------
# Bounded write queue
# ---------------------------------------------------------------------------

_QUEUE_FULL_SENTINEL = object()


class _BoundedWriteQueue:
    """Bounded async queue for fire-and-forget memory writes.

    Drops writes when the queue is full rather than blocking workers.  A
    background task drains the queue in order.
    """

    def __init__(self, maxsize: int = 256) -> None:
        self._q: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0

    async def start(self, worker: Any) -> None:
        self._task = asyncio.create_task(self._drain(worker))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def enqueue(self, item: Any) -> None:
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull:
            self._dropped += 1
            logger.warning("brain_bridge.write_queue_full", extra={"dropped_total": self._dropped})

    async def _drain(self, worker: Any) -> None:
        while True:
            item = await self._q.get()
            try:
                await worker(item)
            except Exception as exc:
                logger.error("brain_bridge.write_failed", extra={"error": str(exc)})
            finally:
                self._q.task_done()

    @property
    def dropped_count(self) -> int:
        return self._dropped


# ---------------------------------------------------------------------------
# BrainBridge
# ---------------------------------------------------------------------------


class BrainBridge:
    """AgentForge ↔ tapps-brain bridge using :class:`AsyncTappsBrainClient`.

    Drop-in replacement for the original embedded-library BrainBridge.
    Resilience: circuit breaker + bounded write queue.

    Parameters
    ----------
    url:
        tapps-brain HTTP adapter URL (e.g. ``"http://brain.internal:8080"``).
    project_id:
        tapps-brain project identifier.
    agent_id:
        Worker / agent identifier.  Used for per-call identity (STORY-070.7).
    auth_token:
        Bearer token for the HTTP adapter auth.
    write_queue_size:
        Maximum number of queued fire-and-forget writes (default 256).
    circuit_failure_threshold:
        Consecutive failures before the circuit opens (default 5).
    circuit_recovery_timeout:
        Seconds before the circuit transitions from OPEN to HALF_OPEN.
    """

    def __init__(
        self,
        url: str = "http://localhost:8080",
        *,
        project_id: str,
        agent_id: str,
        auth_token: str | None = None,
        write_queue_size: int = 256,
        circuit_failure_threshold: int = 5,
        circuit_recovery_timeout: float = 30.0,
    ) -> None:
        # Lazy import so the examples dir doesn't hard-require the package.
        from tapps_brain.client import AsyncTappsBrainClient

        self._client = AsyncTappsBrainClient(
            url,
            project_id=project_id,
            agent_id=agent_id,
            auth_token=auth_token,
        )
        self._breaker = _CircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            recovery_timeout=circuit_recovery_timeout,
        )
        self._wq = _BoundedWriteQueue(maxsize=write_queue_size)

    async def start(self) -> None:
        """Start the background write-queue worker."""
        await self._client.__aenter__()
        await self._wq.start(self._flush_write)

    async def stop(self) -> None:
        """Drain the write queue and close the client."""
        await self._wq.stop()
        await self._client.close()

    # --- context manager ---

    async def __aenter__(self) -> BrainBridge:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # --- public API (mirrors AgentBrain) ---

    async def remember(self, fact: str, *, tier: str = "procedural") -> None:
        """Queue a fire-and-forget memory write."""
        await self._wq.enqueue(("remember", fact, tier))

    async def recall(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        """Recall memories with circuit breaker protection."""
        return await self._breaker.call(
            self._client.recall(query, max_results=max_results)
        )

    async def learn_success(self, task_description: str, *, task_id: str = "") -> None:
        """Queue a fire-and-forget success record."""
        await self._wq.enqueue(("learn_success", task_description, task_id))

    async def learn_failure(
        self, description: str, *, error: str = "", task_id: str = ""
    ) -> None:
        """Queue a fire-and-forget failure record."""
        await self._wq.enqueue(("learn_failure", description, error, task_id))

    async def health(self) -> dict[str, Any]:
        """Return brain health with circuit breaker state included."""
        try:
            h: dict[str, Any] = await self._breaker.call(self._client.health())
        except BrainBridgeCircuitOpenError:
            h = {}
        h["circuit_state"] = self._breaker.state.value
        h["write_queue_dropped"] = self._wq.dropped_count
        return h

    # --- internal write flush ---

    async def _flush_write(self, item: tuple[Any, ...]) -> None:
        """Apply a queued write through the circuit breaker."""
        kind = item[0]
        if kind == "remember":
            _, fact, tier = item
            await self._breaker.call(self._client.remember(fact, tier=tier))
        elif kind == "learn_success":
            _, desc, task_id = item
            await self._breaker.call(self._client.learn_success(desc, task_id=task_id))
        elif kind == "learn_failure":
            _, desc, error, task_id = item
            await self._breaker.call(
                self._client.learn_failure(desc, error=error, task_id=task_id)
            )
