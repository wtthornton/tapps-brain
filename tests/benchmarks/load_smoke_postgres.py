"""Load smoke benchmark: 50 concurrent agents × 60 s against one Postgres.

Simulates a realistic multi-agent workload (save → recall → hive_search) and
records p95 latencies.  Results are **informational only** (pre-SLO in v3.0) —
no hard budget is enforced.

Two test variants
-----------------
``test_load_smoke_50_agents``
    Original thread-based benchmark (50 threads × ``TAPPS_SMOKE_DURATION`` s).

``test_load_smoke_async_comparison``
    STORY-072.4 addition: 50 concurrent ``asyncio`` tasks, comparing
    (a) ``asyncio.to_thread`` baseline and (b) async-native
    (``AsyncPostgresPrivateBackend``).  Results are printed side-by-side so
    the p95 delta is immediately visible.

Marks
-----
- ``requires_postgres`` — skipped unless ``TAPPS_BRAIN_DATABASE_URL`` is set
- ``benchmark`` — excluded from the unit suite ``-m "not benchmark"`` filter

Usage
-----
    # Full 60-second run (default):
    TAPPS_BRAIN_DATABASE_URL=postgres://tapps:tapps@localhost:5432/tapps_test \\
        pytest tests/benchmarks/load_smoke_postgres.py -v -s

    # Shorter run for quick validation (override wall-clock seconds):
    TAPPS_SMOKE_DURATION=10 \\
    TAPPS_BRAIN_DATABASE_URL=postgres://tapps:tapps@localhost:5432/tapps_test \\
        pytest tests/benchmarks/load_smoke_postgres.py -v -s

    # Via Makefile helper (see AGENTS.md § benchmark-postgres):
    make benchmark-postgres

Stories: STORY-066.9 / EPIC-059 STORY-059.6 / EPIC-072 STORY-072.4
"""

from __future__ import annotations

import asyncio
import os
import statistics
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Number of concurrent agent threads.  The story acceptance criteria specify
#: 50 concurrent agents.  Set ``TAPPS_SMOKE_AGENTS`` to override.
_DEFAULT_AGENTS: int = 50

#: Wall-clock duration (seconds) each agent thread runs its workload loop.
#: Set ``TAPPS_SMOKE_DURATION`` to a smaller value for quick CI validation.
_DEFAULT_DURATION: int = 60

#: Namespace used for all Hive entries written during the benchmark.
_HIVE_NAMESPACE: str = "load-smoke"


# ---------------------------------------------------------------------------
# Latency bucket
# ---------------------------------------------------------------------------


@dataclass
class _LatencyBucket:
    name: str
    _samples: list[float] = field(default_factory=list, init=False, repr=False)
    _errors: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def record(self, seconds: float) -> None:
        with self._lock:
            self._samples.append(seconds)

    def record_error(self) -> None:
        with self._lock:
            self._errors += 1

    @property
    def count(self) -> int:
        return len(self._samples)

    @property
    def error_count(self) -> int:
        return self._errors

    def percentile(self, p: float) -> float | None:
        """Return *p*-th percentile in **milliseconds**, or None when empty."""
        if not self._samples:
            return None
        s = sorted(self._samples)
        idx = max(0, min(len(s) - 1, int(p / 100.0 * len(s))))
        return s[idx] * 1000.0

    def summary(self) -> dict[str, Any]:
        def _fmt(v: float | None) -> str:
            return f"{v:.2f}" if v is not None else "—"

        return {
            "name": self.name,
            "count": self.count,
            "errors": self.error_count,
            "p50_ms": _fmt(self.percentile(50)),
            "p90_ms": _fmt(self.percentile(90)),
            "p95_ms": _fmt(self.percentile(95)),
            "p99_ms": _fmt(self.percentile(99)),
            "max_ms": _fmt(max(self._samples) * 1000.0 if self._samples else None),
            "ops_per_sec": (
                f"{self.count / max(statistics.mean(self._samples), 1e-9) / self.count:.0f}"
                if self._samples
                else "—"
            ),
        }


# ---------------------------------------------------------------------------
# Agent workload
# ---------------------------------------------------------------------------


def _run_agent(
    *,
    agent_id: str,
    project_id: str,
    dsn: str,
    duration_s: int,
    hive_backend: Any,
    save_bucket: _LatencyBucket,
    recall_bucket: _LatencyBucket,
    hive_search_bucket: _LatencyBucket,
    errors: list[str],
    barrier: threading.Barrier,
) -> None:
    """One agent thread: timed loop of save → recall → hive_search."""
    try:
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.postgres_migrations import apply_private_migrations
        from tapps_brain.postgres_private import PostgresPrivateBackend
        from tapps_brain.store import MemoryStore

        apply_private_migrations(dsn)
        cm = PostgresConnectionManager(dsn)
        backend = PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)
        store = MemoryStore(
            f"/tmp/tapps_bench_{agent_id}",
            agent_id=agent_id,
            private_backend=backend,
        )

        # Seed one Hive entry per agent so hive_search has something to find.
        try:
            hive_backend.save(
                {
                    "key": f"smoke-{agent_id}",
                    "value": f"Architectural decision from {agent_id}: "
                    "prefer Postgres for all durable state",
                    "tier": "architectural",
                    "source": "agent",
                    "agent_id": agent_id,
                    "agent_scope": "hive",
                    "namespace": _HIVE_NAMESPACE,
                    "tags": ["architectural", "postgres"],
                    "confidence": 0.85,
                    "access_count": 0,
                }
            )
        except Exception:
            pass  # Hive may not be fully wired; hive_search will return []

        # Synchronise: wait for all threads before starting the timed loop.
        barrier.wait()

        op_index = 0
        deadline = time.perf_counter() + duration_s
        while time.perf_counter() < deadline:
            key = f"{agent_id}-op-{op_index:06d}"
            value = (
                f"Entry {op_index} from agent {agent_id}: "
                f"architectural decision about component {op_index % 20} "
                f"in project {project_id}."
            )

            # --- save ---
            t0 = time.perf_counter()
            try:
                store.save(key, value, tier="pattern")
                save_bucket.record(time.perf_counter() - t0)
            except Exception:
                save_bucket.record_error()

            # --- recall ---
            query = f"architectural decision component {op_index % 20}"
            t0 = time.perf_counter()
            try:
                store.search(query)
                recall_bucket.record(time.perf_counter() - t0)
            except Exception:
                recall_bucket.record_error()

            # --- hive_search (every 5th op to avoid overwhelming the pool) ---
            if op_index % 5 == 0:
                t0 = time.perf_counter()
                try:
                    hive_backend.search(
                        "architectural decision postgres",
                        namespace=_HIVE_NAMESPACE,
                    )
                    hive_search_bucket.record(time.perf_counter() - t0)
                except Exception:
                    hive_search_bucket.record_error()

            op_index += 1

        store.close()
        try:
            cm.close()
        except Exception:
            pass

    except Exception as exc:
        errors.append(f"[{agent_id}] fatal: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_results(
    buckets: list[_LatencyBucket],
    *,
    n_agents: int,
    duration_s: int,
    total_elapsed: float,
) -> None:
    """Print a formatted latency summary table."""
    width = 82
    print(f"\n{'─' * width}")
    print(
        f"  tapps-brain v3 load smoke  |  {n_agents} agents × {duration_s}s  "
        "(pre-SLO / informational)"
    )
    print(f"  Total wall time: {total_elapsed:.2f}s")
    print(f"{'─' * width}")
    hdr = (
        f"{'Operation':<20} {'count':>7} {'errors':>6} "
        f"{'p50 ms':>8} {'p90 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'max ms':>8}"
    )
    print(hdr)
    print("─" * width)
    for b in buckets:
        s = b.summary()
        print(
            f"{s['name']:<20} {s['count']:>7} {s['errors']:>6} "
            f"{s['p50_ms']:>8} {s['p90_ms']:>8} {s['p95_ms']:>8} "
            f"{s['p99_ms']:>8} {s['max_ms']:>8}"
        )
    print("─" * width)
    print("NOTE: Results are informational only. No hard SLO budget is enforced in v3.0.")
    print()


# ---------------------------------------------------------------------------
# Pytest test
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.requires_postgres, pytest.mark.benchmark]


@pytest.mark.requires_postgres
@pytest.mark.benchmark
def test_load_smoke_50_agents(capsys: pytest.CaptureFixture[str]) -> None:  # type: ignore[type-arg]
    """50 concurrent agents write + recall + hive_search for a wall-clock window.

    Acceptance criteria (STORY-066.9):
    - 50 concurrent agent threads run against one Postgres instance
    - Wall-clock duration: TAPPS_SMOKE_DURATION env var (default 60 s)
    - p95 latency recorded for save, recall, hive_search
    - All agents complete without fatal errors
    - Results printed to stdout (informational only — no hard SLO in v3.0)
    """
    dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")
    if not dsn:
        pytest.skip("TAPPS_BRAIN_DATABASE_URL not set — requires live Postgres")

    n_agents = int(os.environ.get("TAPPS_SMOKE_AGENTS", str(_DEFAULT_AGENTS)))
    duration_s = int(os.environ.get("TAPPS_SMOKE_DURATION", str(_DEFAULT_DURATION)))

    project_id = f"smoke-{uuid.uuid4().hex[:8]}"

    # --- Set up a shared Hive backend ---
    hive_backend: Any = None
    try:
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.postgres_hive import PostgresHiveBackend
        from tapps_brain.postgres_migrations import apply_hive_migrations

        apply_hive_migrations(dsn)
        _hive_cm = PostgresConnectionManager(dsn)
        hive_backend = PostgresHiveBackend(_hive_cm)
    except Exception:
        # Hive may not be available in all test environments.
        # hive_search ops will record errors but the test still runs.
        pass

    # Shared latency buckets (thread-safe)
    save_bucket = _LatencyBucket("save")
    recall_bucket = _LatencyBucket("recall")
    hive_search_bucket = _LatencyBucket("hive_search")
    errors: list[str] = []

    barrier = threading.Barrier(n_agents)
    threads: list[threading.Thread] = []

    print(f"\nStarting {n_agents} agent threads (duration={duration_s}s, project={project_id}) …")
    t_total = time.perf_counter()

    for idx in range(n_agents):
        agent_id = f"agent-{idx:03d}"
        t = threading.Thread(
            target=_run_agent,
            kwargs={
                "agent_id": agent_id,
                "project_id": project_id,
                "dsn": dsn,
                "duration_s": duration_s,
                "hive_backend": hive_backend,
                "save_bucket": save_bucket,
                "recall_bucket": recall_bucket,
                "hive_search_bucket": hive_search_bucket,
                "errors": errors,
                "barrier": barrier,
            },
            daemon=True,
            name=agent_id,
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=duration_s + 30)

    total_elapsed = time.perf_counter() - t_total

    # Tear down shared Hive backend
    if hive_backend is not None:
        try:
            hive_backend.close()
        except Exception:
            pass

    _print_results(
        [save_bucket, recall_bucket, hive_search_bucket],
        n_agents=n_agents,
        duration_s=duration_s,
        total_elapsed=total_elapsed,
    )

    # --- Assertions ---
    # All agents must complete (threads joined above within timeout).
    still_alive = [t.name for t in threads if t.is_alive()]
    assert not still_alive, f"Timed-out agent threads: {still_alive}"

    # Fatal errors (backend init failures, uncaught exceptions) must be zero.
    assert not errors, "Agent fatal errors:\n" + "\n".join(errors)

    # At least one save and recall recorded per agent (sanity check).
    assert save_bucket.count >= n_agents, (
        f"Expected ≥{n_agents} save samples, got {save_bucket.count}"
    )
    assert recall_bucket.count >= n_agents, (
        f"Expected ≥{n_agents} recall samples, got {recall_bucket.count}"
    )

    # p95 latencies must be measurable (non-None) when ops were recorded.
    if save_bucket.count:
        p95_save = save_bucket.percentile(95)
        assert p95_save is not None
        print(f"  save   p95 = {p95_save:.2f} ms  (informational)")

    if recall_bucket.count:
        p95_recall = recall_bucket.percentile(95)
        assert p95_recall is not None
        print(f"  recall p95 = {p95_recall:.2f} ms  (informational)")

    if hive_search_bucket.count:
        p95_hive = hive_search_bucket.percentile(95)
        assert p95_hive is not None
        print(f"  hive_search p95 = {p95_hive:.2f} ms  (informational)")


# ---------------------------------------------------------------------------
# STORY-072.4: Async benchmark — asyncio tasks instead of threads
# ---------------------------------------------------------------------------


async def _run_agent_async(
    *,
    agent_id: str,
    project_id: str,
    dsn: str,
    duration_s: int,
    save_bucket: _LatencyBucket,
    recall_bucket: _LatencyBucket,
    async_native: bool,
    errors: list[str],
    barrier: asyncio.Barrier,
) -> None:
    """One async agent task: timed loop of save → recall using AsyncMemoryStore."""
    try:
        from tapps_brain.aio import AsyncMemoryStore
        from tapps_brain.backends import create_async_private_backend, create_private_backend
        from tapps_brain.postgres_migrations import apply_private_migrations
        from tapps_brain.store import MemoryStore

        await asyncio.to_thread(apply_private_migrations, dsn)

        if async_native:
            sync_backend = await asyncio.to_thread(
                create_private_backend, dsn, project_id=project_id, agent_id=agent_id
            )
            sync_store = await asyncio.to_thread(
                MemoryStore,
                f"/tmp/tapps_bench_async_{agent_id}",
                agent_id=agent_id,
                private_backend=sync_backend,
            )
            async_backend = create_async_private_backend(
                dsn, project_id=project_id, agent_id=agent_id
            )
            store = AsyncMemoryStore(sync_store, async_backend=async_backend)
        else:
            sync_backend = await asyncio.to_thread(
                create_private_backend, dsn, project_id=project_id, agent_id=agent_id
            )
            sync_store = await asyncio.to_thread(
                MemoryStore,
                f"/tmp/tapps_bench_thread_{agent_id}",
                agent_id=agent_id,
                private_backend=sync_backend,
            )
            store = AsyncMemoryStore(sync_store)

        await barrier.wait()

        op_index = 0
        deadline = time.perf_counter() + duration_s
        while time.perf_counter() < deadline:
            key = f"{agent_id}-op-{op_index:06d}"
            value = (
                f"Entry {op_index} from async agent {agent_id}: "
                f"architectural decision about component {op_index % 20}."
            )

            t0 = time.perf_counter()
            try:
                await store.save(key, value, tier="pattern")
                save_bucket.record(time.perf_counter() - t0)
            except Exception:
                save_bucket.record_error()

            query = f"architectural decision component {op_index % 20}"
            t0 = time.perf_counter()
            try:
                await store.search(query)
                recall_bucket.record(time.perf_counter() - t0)
            except Exception:
                recall_bucket.record_error()

            op_index += 1

        await store.close()

    except Exception as exc:
        errors.append(f"[{agent_id}] fatal: {exc}\n{traceback.format_exc()}")


async def _run_async_phase(
    *,
    dsn: str,
    project_id: str,
    n_agents: int,
    duration_s: int,
    async_native: bool,
) -> tuple[_LatencyBucket, _LatencyBucket]:
    """Run a single async phase (to_thread or native) and return save/recall buckets."""
    save_bucket = _LatencyBucket("save")
    recall_bucket = _LatencyBucket("recall")
    errors: list[str] = []
    barrier = asyncio.Barrier(n_agents)

    tasks = [
        _run_agent_async(
            agent_id=f"agent-{idx:03d}",
            project_id=project_id,
            dsn=dsn,
            duration_s=duration_s,
            save_bucket=save_bucket,
            recall_bucket=recall_bucket,
            async_native=async_native,
            errors=errors,
            barrier=barrier,
        )
        for idx in range(n_agents)
    ]
    await asyncio.gather(*tasks)

    if errors:
        for e in errors[:3]:
            print(f"  ERROR: {e[:200]}")

    return save_bucket, recall_bucket


@pytest.mark.requires_postgres
@pytest.mark.benchmark
def test_load_smoke_async_comparison(capsys: pytest.CaptureFixture[str]) -> None:  # type: ignore[type-arg]
    """STORY-072.4: Compare asyncio.to_thread baseline vs async-native.

    Runs two phases of 50 concurrent asyncio tasks each:
      Phase A — to_thread baseline (TAPPS_BRAIN_ASYNC_NATIVE unset)
      Phase B — async-native (AsyncPostgresPrivateBackend)

    p95 save and recall latencies are printed side-by-side.
    Results are informational only — no hard SLO is enforced.
    """
    dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")
    if not dsn:
        pytest.skip("TAPPS_BRAIN_DATABASE_URL not set — requires live Postgres")

    n_agents = int(os.environ.get("TAPPS_SMOKE_AGENTS", str(_DEFAULT_AGENTS)))
    duration_s = int(os.environ.get("TAPPS_SMOKE_DURATION", str(_DEFAULT_DURATION)))
    project_id = f"smoke-async-{uuid.uuid4().hex[:8]}"

    width = 82
    print(f"\n{'─' * width}")
    print(
        f"  STORY-072.4 async comparison  |  {n_agents} tasks × {duration_s}s  "
        "(pre-SLO / informational)"
    )
    print(f"  Project: {project_id}")
    print(f"{'─' * width}")

    print("\n  Phase A: asyncio.to_thread baseline …")
    t0 = time.perf_counter()
    save_a, recall_a = asyncio.run(
        _run_async_phase(
            dsn=dsn,
            project_id=f"{project_id}-a",
            n_agents=n_agents,
            duration_s=duration_s,
            async_native=False,
        )
    )
    elapsed_a = time.perf_counter() - t0

    print("\n  Phase B: async-native (AsyncPostgresPrivateBackend) …")
    t0 = time.perf_counter()
    save_b, recall_b = asyncio.run(
        _run_async_phase(
            dsn=dsn,
            project_id=f"{project_id}-b",
            n_agents=n_agents,
            duration_s=duration_s,
            async_native=True,
        )
    )
    elapsed_b = time.perf_counter() - t0

    def _p95(b: _LatencyBucket) -> str:
        v = b.percentile(95)
        return f"{v:.2f}" if v is not None else "—"

    print(f"\n{'─' * width}")
    print(f"  {'Metric':<30} {'Phase A (to_thread)':>20} {'Phase B (native)':>20} {'Delta':>8}")
    print("─" * width)
    for label, b_a, b_b in [
        ("save p95 (ms)", save_a, save_b),
        ("recall p95 (ms)", recall_a, recall_b),
    ]:
        p_a = b_a.percentile(95)
        p_b = b_b.percentile(95)
        if p_a is not None and p_b is not None:
            delta = f"{(p_b - p_a):+.2f}"
        else:
            delta = "—"
        print(f"  {label:<30} {_p95(b_a):>20} {_p95(b_b):>20} {delta:>8}")
    print(f"  {'wall time (s)':<30} {elapsed_a:>20.2f} {elapsed_b:>20.2f}")
    print("─" * width)
    print("NOTE: Results are informational only. No hard SLO budget is enforced.")
    print()

    # Assertions: both phases must complete with ops recorded.
    assert save_a.count >= n_agents, f"Phase A save count too low: {save_a.count}"
    assert save_b.count >= n_agents, f"Phase B save count too low: {save_b.count}"

    # p95 must be computable when ops were recorded.
    if save_a.count:
        assert save_a.percentile(95) is not None
    if save_b.count:
        assert save_b.percentile(95) is not None
