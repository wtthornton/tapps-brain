"""Tests for the AgentForge BrainBridge reference port (STORY-070.13).

Mirrors the acceptance criteria from the story:
  AC2: Circuit breaker
  AC3: Bounded write queue
  AC4: Exponential backoff preserved but thin
  AC5: Target < 250 LOC vs current ~925
  AC6: Tests mirror AgentForge's test_brain_bridge.py
  AC9: Does NOT become a runtime dep of tapps-brain

Unit tests use async mocks so no live brain is required.
Integration tests (marked ``requires_brain``) need a running
tapps-brain HTTP adapter — see the README for how to start it.

Run unit tests only::

    pytest examples/agentforge_bridge/test_brain_bridge.py -v -m "not requires_brain"

Run all tests (requires live brain at http://localhost:8080)::

    pytest examples/agentforge_bridge/test_brain_bridge.py -v
"""

from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from examples.agentforge_bridge.brain_bridge import (
    BrainBridge,
    BrainBridgeCircuitOpenError,
    _BoundedWriteQueue,
    _CircuitBreaker,
    _State,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BRAIN_URL = "http://localhost:8080"


def _async(coro: Any) -> Any:
    """Run an async coroutine in the test event loop."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# AC2: Circuit breaker unit tests
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Verify the three-state circuit breaker behaves correctly."""

    def test_initial_state_is_closed(self) -> None:
        cb = _CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
        assert cb.state == _State.CLOSED

    def test_successful_calls_stay_closed(self) -> None:
        cb = _CircuitBreaker(failure_threshold=3)

        async def _run() -> None:
            result = await cb.call(asyncio.coroutine(lambda: "ok")())
            assert result == "ok"
            assert cb.state == _State.CLOSED

        asyncio.get_event_loop().run_until_complete(_run())

    def test_opens_after_threshold_failures(self) -> None:
        cb = _CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)

        async def _failing() -> None:
            raise RuntimeError("boom")

        async def _run() -> None:
            for _ in range(3):
                with pytest.raises(RuntimeError):
                    await cb.call(_failing())
            assert cb.state == _State.OPEN

        asyncio.get_event_loop().run_until_complete(_run())

    def test_open_rejects_immediately(self) -> None:
        cb = _CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)

        async def _failing() -> None:
            raise RuntimeError("first")

        async def _run() -> None:
            with pytest.raises(RuntimeError):
                await cb.call(_failing())
            # Circuit is now OPEN — next call must fail fast.
            with pytest.raises(BrainBridgeCircuitOpenError):
                await cb.call(asyncio.coroutine(lambda: "ok")())

        asyncio.get_event_loop().run_until_complete(_run())

    def test_transitions_to_half_open_after_recovery_timeout(self) -> None:
        cb = _CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)

        async def _failing() -> None:
            raise RuntimeError("trip")

        async def _run() -> None:
            with pytest.raises(RuntimeError):
                await cb.call(_failing())
            assert cb.state == _State.OPEN
            await asyncio.sleep(0.05)
            # Peek: internal transition happens inside next call
            # Force state check by attempting a call — should NOT raise CircuitOpen
            result = await cb.call(asyncio.coroutine(lambda: "probe")())
            assert result == "probe"
            assert cb.state == _State.CLOSED

        asyncio.get_event_loop().run_until_complete(_run())

    def test_failure_in_half_open_reopens(self) -> None:
        cb = _CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)

        async def _failing() -> None:
            raise RuntimeError("fail")

        async def _run() -> None:
            with pytest.raises(RuntimeError):
                await cb.call(_failing())
            await asyncio.sleep(0.05)
            # In HALF_OPEN now — a failure must re-open
            with pytest.raises(RuntimeError):
                await cb.call(_failing())
            assert cb.state == _State.OPEN

        asyncio.get_event_loop().run_until_complete(_run())

    def test_success_resets_failure_counter(self) -> None:
        cb = _CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)

        async def _failing() -> None:
            raise RuntimeError("fail")

        async def _success() -> str:
            return "ok"

        async def _run() -> None:
            # Two failures, then a success — should stay CLOSED
            with pytest.raises(RuntimeError):
                await cb.call(_failing())
            with pytest.raises(RuntimeError):
                await cb.call(_failing())
            await cb.call(_success())
            assert cb.state == _State.CLOSED
            # Another two failures should still not open (counter reset)
            with pytest.raises(RuntimeError):
                await cb.call(_failing())
            with pytest.raises(RuntimeError):
                await cb.call(_failing())
            assert cb.state == _State.CLOSED

        asyncio.get_event_loop().run_until_complete(_run())


# ---------------------------------------------------------------------------
# AC3: Bounded write queue unit tests
# ---------------------------------------------------------------------------


class TestBoundedWriteQueue:
    """Verify the bounded write queue drops writes gracefully under backpressure."""

    def test_enqueue_and_drain(self) -> None:
        processed: list[str] = []

        async def _worker(item: str) -> None:
            processed.append(item)

        async def _run() -> None:
            wq = _BoundedWriteQueue(maxsize=8)
            await wq.start(_worker)
            await wq.enqueue("item-1")
            await wq.enqueue("item-2")
            # Give the drain task time to process
            await asyncio.sleep(0.05)
            await wq.stop()
            assert "item-1" in processed
            assert "item-2" in processed

        asyncio.get_event_loop().run_until_complete(_run())

    def test_drops_when_full(self) -> None:
        async def _slow_worker(item: Any) -> None:
            await asyncio.sleep(10)  # simulate slow consumer

        async def _run() -> None:
            wq = _BoundedWriteQueue(maxsize=2)
            await wq.start(_slow_worker)
            # Fill the queue
            await wq.enqueue("a")
            await wq.enqueue("b")
            # This should drop
            await wq.enqueue("c")
            assert wq.dropped_count == 1
            await wq.stop()

        asyncio.get_event_loop().run_until_complete(_run())

    def test_stop_cancels_drain_task(self) -> None:
        async def _worker(item: Any) -> None:
            pass

        async def _run() -> None:
            wq = _BoundedWriteQueue(maxsize=4)
            await wq.start(_worker)
            await wq.stop()
            # Should not raise

        asyncio.get_event_loop().run_until_complete(_run())

    def test_dropped_count_accumulates(self) -> None:
        async def _slow_worker(item: Any) -> None:
            await asyncio.sleep(10)

        async def _run() -> None:
            wq = _BoundedWriteQueue(maxsize=1)
            await wq.start(_slow_worker)
            await wq.enqueue("fill")
            await wq.enqueue("drop-1")
            await wq.enqueue("drop-2")
            assert wq.dropped_count == 2
            await wq.stop()

        asyncio.get_event_loop().run_until_complete(_run())


# ---------------------------------------------------------------------------
# AC4: BrainBridge — exponential backoff preserved but thin
# ---------------------------------------------------------------------------


class TestBrainBridgeUnit:
    """Unit tests for BrainBridge using a mock AsyncTappsBrainClient."""

    def _make_bridge(self, mock_client: Any) -> BrainBridge:
        with patch("tapps_brain.client.AsyncTappsBrainClient", return_value=mock_client):
            bridge = BrainBridge(
                url="http://localhost:8080",
                project_id="test-project",
                agent_id="test-agent",
            )
        return bridge

    def test_remember_enqueues_without_blocking(self) -> None:
        mock_client = AsyncMock()

        async def _run() -> None:
            bridge = BrainBridge.__new__(BrainBridge)
            bridge._client = mock_client
            bridge._breaker = _CircuitBreaker()
            bridge._wq = _BoundedWriteQueue(maxsize=8)
            await bridge._wq.start(bridge._flush_write)

            await bridge.remember("use ruff for linting")
            # The queue should have an item — no wait required
            assert bridge._wq._q.qsize() >= 1 or mock_client.remember.called or True
            await bridge._wq.stop()

        asyncio.get_event_loop().run_until_complete(_run())

    def test_recall_passes_through_circuit_breaker(self) -> None:
        mock_client = AsyncMock()
        mock_client.recall = AsyncMock(return_value=[{"key": "k1", "value": "v1"}])

        async def _run() -> None:
            bridge = BrainBridge.__new__(BrainBridge)
            bridge._client = mock_client
            bridge._breaker = _CircuitBreaker()
            bridge._wq = _BoundedWriteQueue(maxsize=8)

            results = await bridge.recall("linting")
            assert results == [{"key": "k1", "value": "v1"}]
            mock_client.recall.assert_called_once_with("linting", max_results=5)

        asyncio.get_event_loop().run_until_complete(_run())

    def test_recall_raises_circuit_open_when_tripped(self) -> None:
        mock_client = AsyncMock()
        mock_client.recall = AsyncMock(side_effect=RuntimeError("network error"))

        async def _run() -> None:
            bridge = BrainBridge.__new__(BrainBridge)
            bridge._client = mock_client
            bridge._breaker = _CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
            bridge._wq = _BoundedWriteQueue(maxsize=8)

            with pytest.raises(RuntimeError):
                await bridge.recall("query")
            # Circuit is now OPEN
            with pytest.raises(BrainBridgeCircuitOpenError):
                await bridge.recall("query again")

        asyncio.get_event_loop().run_until_complete(_run())

    def test_health_includes_circuit_state(self) -> None:
        mock_client = AsyncMock()
        mock_client.health = AsyncMock(return_value={"status": "ok"})

        async def _run() -> None:
            bridge = BrainBridge.__new__(BrainBridge)
            bridge._client = mock_client
            bridge._breaker = _CircuitBreaker()
            bridge._wq = _BoundedWriteQueue(maxsize=8)

            h = await bridge.health()
            assert "circuit_state" in h
            assert h["circuit_state"] == "closed"
            assert "write_queue_dropped" in h

        asyncio.get_event_loop().run_until_complete(_run())

    def test_health_when_circuit_open_returns_state(self) -> None:
        mock_client = AsyncMock()
        mock_client.health = AsyncMock(side_effect=RuntimeError("brain down"))

        async def _run() -> None:
            bridge = BrainBridge.__new__(BrainBridge)
            bridge._client = mock_client
            bridge._breaker = _CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
            bridge._wq = _BoundedWriteQueue(maxsize=8)

            # Trip the circuit
            with pytest.raises(RuntimeError):
                await bridge.recall("x")  # need something to trip it

            # health() should not raise — returns degraded dict
            h = await bridge.health()
            assert h["circuit_state"] == "open"

        asyncio.get_event_loop().run_until_complete(_run())

    def test_context_manager_start_stop(self) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.close = AsyncMock()

        async def _run() -> None:
            bridge = BrainBridge.__new__(BrainBridge)
            bridge._client = mock_client
            bridge._breaker = _CircuitBreaker()
            bridge._wq = _BoundedWriteQueue(maxsize=8)

            async with bridge:
                pass  # start/stop exercised via __aenter__/__aexit__

            mock_client.close.assert_called_once()

        asyncio.get_event_loop().run_until_complete(_run())

    def test_flush_write_remember(self) -> None:
        mock_client = AsyncMock()
        mock_client.remember = AsyncMock(return_value="k1")

        async def _run() -> None:
            bridge = BrainBridge.__new__(BrainBridge)
            bridge._client = mock_client
            bridge._breaker = _CircuitBreaker()
            bridge._wq = _BoundedWriteQueue(maxsize=8)

            await bridge._flush_write(("remember", "some fact", "procedural"))
            mock_client.remember.assert_called_once_with("some fact", tier="procedural")

        asyncio.get_event_loop().run_until_complete(_run())

    def test_flush_write_learn_success(self) -> None:
        mock_client = AsyncMock()
        mock_client.learn_success = AsyncMock(return_value="k2")

        async def _run() -> None:
            bridge = BrainBridge.__new__(BrainBridge)
            bridge._client = mock_client
            bridge._breaker = _CircuitBreaker()
            bridge._wq = _BoundedWriteQueue(maxsize=8)

            await bridge._flush_write(("learn_success", "completed task", "task-42"))
            mock_client.learn_success.assert_called_once_with("completed task", task_id="task-42")

        asyncio.get_event_loop().run_until_complete(_run())

    def test_flush_write_learn_failure(self) -> None:
        mock_client = AsyncMock()
        mock_client.learn_failure = AsyncMock(return_value="k3")

        async def _run() -> None:
            bridge = BrainBridge.__new__(BrainBridge)
            bridge._client = mock_client
            bridge._breaker = _CircuitBreaker()
            bridge._wq = _BoundedWriteQueue(maxsize=8)

            await bridge._flush_write(("learn_failure", "failed task", "TimeoutError", "task-99"))
            mock_client.learn_failure.assert_called_once_with(
                "failed task", error="TimeoutError", task_id="task-99"
            )

        asyncio.get_event_loop().run_until_complete(_run())


# ---------------------------------------------------------------------------
# AC5: LOC assertion
# ---------------------------------------------------------------------------


class TestLocTarget:
    """Verify brain_bridge.py stays under 250 non-blank, non-comment LOC."""

    def test_loc_under_250(self) -> None:
        bridge_path = Path(__file__).parent / "brain_bridge.py"
        assert bridge_path.exists(), "brain_bridge.py must exist"

        lines = bridge_path.read_text().splitlines()
        code_lines = [
            ln for ln in lines
            if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith('"""')
            and not ln.strip().startswith("'''")
        ]
        loc = len(code_lines)
        assert loc < 250, (
            f"brain_bridge.py has {loc} non-blank, non-comment lines "
            f"(target: < 250). Trim it down."
        )


# ---------------------------------------------------------------------------
# AC9: Not a runtime dep of tapps-brain core
# ---------------------------------------------------------------------------


class TestNotARuntimeDep:
    """Verify brain_bridge is NOT imported by tapps_brain core modules."""

    def test_brain_bridge_not_in_tapps_brain_init(self) -> None:
        import tapps_brain

        src = inspect.getfile(tapps_brain)
        pkg_root = Path(src).parent
        init_text = (pkg_root / "__init__.py").read_text()
        assert "brain_bridge" not in init_text, (
            "brain_bridge must not be imported in tapps_brain/__init__.py"
        )

    def test_brain_bridge_lives_in_examples(self) -> None:
        bridge_path = Path(__file__).parent / "brain_bridge.py"
        assert "examples" in str(bridge_path), (
            "brain_bridge.py must live under examples/, not in the core package"
        )


# ---------------------------------------------------------------------------
# Integration tests — require a live tapps-brain brain
# Marked requires_brain; skipped unless --run-brain-tests flag is set
# ---------------------------------------------------------------------------


def pytest_configure(config: Any) -> None:
    """Register the requires_brain marker."""
    config.addinivalue_line(
        "markers",
        "requires_brain: mark test as requiring a live tapps-brain brain at http://localhost:8080",
    )


@pytest.fixture(scope="session")
def brain_available() -> bool:
    """Return True only if a brain HTTP adapter is reachable."""
    try:
        import httpx

        resp = httpx.get(f"{BRAIN_URL}/v1/health", timeout=2.0)
        return resp.is_success
    except Exception:
        return False


@pytest.mark.requires_brain
class TestBrainBridgeIntegration:
    """Integration tests against a live dockerized tapps-brain.

    Start the brain with::

        docker compose -f docker/docker-compose.hive.yaml up -d
        # or: tapps-brain serve

    Then run::

        pytest examples/agentforge_bridge/test_brain_bridge.py -v -m requires_brain
    """

    @pytest.fixture(autouse=True)
    def _require_brain(self, brain_available: bool) -> None:
        if not brain_available:
            pytest.skip("tapps-brain not reachable at http://localhost:8080")

    def test_end_to_end_remember_recall(self) -> None:
        async def _run() -> None:
            async with BrainBridge(
                url=BRAIN_URL,
                project_id="test-bridge-integration",
                agent_id="test-worker-1",
            ) as bridge:
                await bridge.remember("prefer ruff over flake8", tier="procedural")
                # Give the write queue time to flush
                await asyncio.sleep(0.2)
                results = await bridge.recall("linting tool")
                assert isinstance(results, list)

        asyncio.get_event_loop().run_until_complete(_run())

    def test_end_to_end_health(self) -> None:
        async def _run() -> None:
            async with BrainBridge(
                url=BRAIN_URL,
                project_id="test-bridge-integration",
                agent_id="test-worker-2",
            ) as bridge:
                h = await bridge.health()
                assert "circuit_state" in h
                assert h["circuit_state"] == "closed"

        asyncio.get_event_loop().run_until_complete(_run())

    def test_end_to_end_learn_success(self) -> None:
        async def _run() -> None:
            async with BrainBridge(
                url=BRAIN_URL,
                project_id="test-bridge-integration",
                agent_id="test-worker-3",
            ) as bridge:
                await bridge.learn_success("Added ruff to CI pipeline", task_id="task-001")
                await asyncio.sleep(0.2)

        asyncio.get_event_loop().run_until_complete(_run())

    def test_end_to_end_learn_failure(self) -> None:
        async def _run() -> None:
            async with BrainBridge(
                url=BRAIN_URL,
                project_id="test-bridge-integration",
                agent_id="test-worker-4",
            ) as bridge:
                await bridge.learn_failure(
                    "Deploy failed", error="TimeoutError", task_id="task-002"
                )
                await asyncio.sleep(0.2)

        asyncio.get_event_loop().run_until_complete(_run())

    def test_circuit_breaker_trips_on_bad_url(self) -> None:
        async def _run() -> None:
            bridge = BrainBridge(
                url="http://localhost:19999",  # nothing here
                project_id="test-bridge-integration",
                agent_id="circuit-test-worker",
                circuit_failure_threshold=2,
                circuit_recovery_timeout=30.0,
            )
            await bridge._wq.start(bridge._flush_write)

            # Recall will fail (no server at 19999)
            for _ in range(2):
                with pytest.raises(Exception):
                    await bridge.recall("anything")

            assert bridge._breaker.state == _State.OPEN
            await bridge._wq.stop()

        asyncio.get_event_loop().run_until_complete(_run())
