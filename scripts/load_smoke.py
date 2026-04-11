#!/usr/bin/env python3
"""Concurrent-agent load smoke for tapps-brain v3 (EPIC-059 STORY-059.6).

Tests N concurrent agents writing and recalling memories against one Postgres
instance, recording per-operation latency percentiles.

Results are **informational only** (pre-SLO) — no hard budget is enforced.

Usage
-----
    # Minimal: 10 agents, 50 ops each
    export TAPPS_TEST_POSTGRES_DSN="postgres://tapps:tapps@localhost:5432/tapps_test"
    python scripts/load_smoke.py

    # Custom
    python scripts/load_smoke.py --agents 20 --ops 100 --dsn "postgres://..."

    # Without Postgres (in-memory only, no Postgres backend wired):
    python scripts/load_smoke.py --no-postgres

Requirements
------------
- uv/pip: tapps_brain[cli] + psycopg[binary]  (already in dev deps)
- A reachable Postgres with the private-memory schema applied
  (run `tapps-brain migrate --dsn <DSN>` or docker compose up first)

Output
------
Prints a latency summary table (p50/p90/p95/p99/max) for save, recall, and
overall wall-time per agent.  Exits 0 if all agents completed without error.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Latency recording
# ---------------------------------------------------------------------------


@dataclass
class LatencyBucket:
    name: str
    samples: list[float] = field(default_factory=list)
    errors: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, seconds: float) -> None:
        with self._lock:
            self.samples.append(seconds)

    def record_error(self) -> None:
        with self._lock:
            self.errors += 1

    def summary(self) -> dict[str, Any]:
        if not self.samples:
            return {
                "name": self.name,
                "count": 0,
                "errors": self.errors,
                "p50_ms": None,
                "p90_ms": None,
                "p95_ms": None,
                "p99_ms": None,
                "max_ms": None,
            }
        s = sorted(self.samples)
        n = len(s)

        def pct(p: float) -> float:
            idx = max(0, min(n - 1, int(p / 100.0 * n)))
            return round(s[idx] * 1000, 2)

        return {
            "name": self.name,
            "count": n,
            "errors": self.errors,
            "p50_ms": pct(50),
            "p90_ms": pct(90),
            "p95_ms": pct(95),
            "p99_ms": pct(99),
            "max_ms": round(max(s) * 1000, 2),
        }


# ---------------------------------------------------------------------------
# Agent workload
# ---------------------------------------------------------------------------


def _timed(bucket: LatencyBucket, fn: Callable[[], None]) -> None:
    t0 = time.perf_counter()
    try:
        fn()
    except Exception:  # noqa: BLE001
        bucket.record_error()
        raise
    finally:
        bucket.record(time.perf_counter() - t0)


def run_agent_workload(
    *,
    agent_id: str,
    project_id: str,
    ops: int,
    dsn: str | None,
    save_bucket: LatencyBucket,
    recall_bucket: LatencyBucket,
    wall_bucket: LatencyBucket,
    errors: list[str],
    barrier: threading.Barrier,
) -> None:
    """One agent's workload: ops saves + ops recalls."""
    try:
        from tapps_brain.store import MemoryStore

        # Build store — with Postgres backend if DSN provided, else fallback path
        store_kwargs: dict[str, Any] = {}
        conn_mgr: Any = None

        if dsn:
            try:
                from tapps_brain.postgres_connection import PostgresConnectionManager
                from tapps_brain.postgres_private import PostgresPrivateBackend

                conn_mgr = PostgresConnectionManager(dsn)
                private_backend = PostgresPrivateBackend(
                    conn_mgr, project_id=project_id, agent_id=agent_id
                )
                store_kwargs["private_backend"] = private_backend
            except Exception as exc:
                errors.append(f"[{agent_id}] backend init failed: {exc}")

        tmp_dir = Path(f"/tmp/tapps_smoke_{agent_id}")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        store = MemoryStore(tmp_dir, agent_id=agent_id, **store_kwargs)

        # Wait for all threads to be ready before starting
        barrier.wait()

        t_wall = time.perf_counter()
        for i in range(ops):
            key = f"{agent_id}-entry-{i:04d}"
            value = (
                f"Memory entry {i} for agent {agent_id}: architectural decision "
                f"about component {i % 10} in project {project_id}."
            )
            _timed(save_bucket, lambda k=key, v=value: store.save(k, v, tier="pattern"))

        for i in range(ops):
            query = f"architectural decision component {i % 10}"
            _timed(recall_bucket, lambda q=query: store.search(q))

        wall_bucket.record(time.perf_counter() - t_wall)
        store.close()

        if conn_mgr is not None:
            with contextlib.suppress(Exception):
                conn_mgr.close()

    except Exception as exc:  # noqa: BLE001
        errors.append(f"[{agent_id}] fatal: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _print_summary(buckets: list[LatencyBucket], n_agents: int, ops: int) -> None:
    print(f"\n{'─' * 72}")
    print(f"  tapps-brain v3 load smoke  |  {n_agents} agents × {ops} ops  (pre-SLO / informational)")
    print(f"{'─' * 72}")
    header = f"{'Operation':<18} {'count':>6} {'errors':>6} {'p50 ms':>8} {'p90 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'max ms':>8}"
    print(header)
    print("─" * 72)
    for b in buckets:
        s = b.summary()
        count = s["count"] or 0
        errs = s["errors"]
        p50 = f"{s['p50_ms']:.1f}" if s["p50_ms"] is not None else "—"
        p90 = f"{s['p90_ms']:.1f}" if s["p90_ms"] is not None else "—"
        p95 = f"{s['p95_ms']:.1f}" if s["p95_ms"] is not None else "—"
        p99 = f"{s['p99_ms']:.1f}" if s["p99_ms"] is not None else "—"
        mx = f"{s['max_ms']:.1f}" if s["max_ms"] is not None else "—"
        print(
            f"{s['name']:<18} {count:>6} {errs:>6} {p50:>8} {p90:>8} {p95:>8} {p99:>8} {mx:>8}"
        )
    print(f"{'─' * 72}")
    print("NOTE: Results are informational only. No SLO budget is enforced in v3.0.")
    print()


def main(argv: list[str] | None = None) -> int:
    import contextlib

    parser = argparse.ArgumentParser(
        description="tapps-brain v3 concurrent-agent load smoke (EPIC-059 STORY-059.6)"
    )
    parser.add_argument(
        "--agents",
        type=int,
        default=10,
        help="Number of concurrent agent threads (default: 10)",
    )
    parser.add_argument(
        "--ops",
        type=int,
        default=50,
        help="Save + recall operations per agent (default: 50 each)",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("TAPPS_TEST_POSTGRES_DSN"),
        help="Postgres DSN (default: $TAPPS_TEST_POSTGRES_DSN)",
    )
    parser.add_argument(
        "--no-postgres",
        action="store_true",
        help="Skip Postgres backend — use in-memory store only (no DSN required)",
    )
    args = parser.parse_args(argv)

    dsn: str | None = None if args.no_postgres else args.dsn
    if not args.no_postgres and not dsn:
        print(
            "WARNING: No Postgres DSN found (set $TAPPS_TEST_POSTGRES_DSN or pass --dsn).\n"
            "Running without Postgres backend (in-memory only). Use --no-postgres to silence this.",
            file=sys.stderr,
        )

    project_id = f"smoke-{uuid.uuid4().hex[:8]}"
    save_bucket = LatencyBucket("save")
    recall_bucket = LatencyBucket("recall")
    wall_bucket = LatencyBucket("wall (per-agent)")

    errors: list[str] = []
    barrier = threading.Barrier(args.agents)
    threads = []

    print(f"Starting {args.agents} agents × {args.ops} ops (project_id={project_id}) …")
    t_total = time.perf_counter()

    for idx in range(args.agents):
        agent_id = f"agent-{idx:03d}"
        t = threading.Thread(
            target=run_agent_workload,
            kwargs=dict(
                agent_id=agent_id,
                project_id=project_id,
                ops=args.ops,
                dsn=dsn,
                save_bucket=save_bucket,
                recall_bucket=recall_bucket,
                wall_bucket=wall_bucket,
                errors=errors,
                barrier=barrier,
            ),
            daemon=True,
            name=agent_id,
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    elapsed = time.perf_counter() - t_total
    print(f"All agents finished in {elapsed:.2f}s.")

    _print_summary([save_bucket, recall_bucket, wall_bucket], args.agents, args.ops)

    if errors:
        print(f"ERRORS ({len(errors)}):", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    import contextlib  # noqa: E402 (re-import ok in __main__ guard)

    sys.exit(main())
