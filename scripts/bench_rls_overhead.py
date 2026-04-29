#!/usr/bin/env python3
"""Benchmark RLS overhead on hive_memories — EPIC-063 STORY-063.4.

Measures query latency on a representative mix of SELECT and INSERT operations
against ``hive_memories`` before (RLS bypassed) and after (RLS enforced with
session namespace) enabling Row Level Security.

Usage
-----
::

    # Requires a live Postgres instance (apply hive migrations first):
    export TAPPS_TEST_POSTGRES_DSN="postgres://user:pass@localhost:5432/tapps_test"
    python scripts/bench_rls_overhead.py

    # Apply hive migrations manually if needed:
    python - <<'EOF'
    from tapps_brain.postgres_migrations import apply_hive_migrations
    import os
    apply_hive_migrations(os.environ["TAPPS_TEST_POSTGRES_DSN"])
    EOF

Environment
-----------
TAPPS_TEST_POSTGRES_DSN
    PostgreSQL connection string for the test database.  Required.

BENCH_RLS_ITERATIONS
    Number of query iterations per phase (default: 500).

BENCH_RLS_WARMUP
    Number of warmup iterations discarded before measurement (default: 50).

Output
------
Prints a markdown table to stdout with mean, p50, p95, and p99 latencies for
each operation, plus an overall overhead % comparing RLS-bypassed vs
RLS-enforced paths.  Example::

    ## RLS Overhead Benchmark — hive_memories (EPIC-063.4)

    ### SELECT single row by (namespace, key)

    | Mode              | Mean (ms) | p50 (ms) | p95 (ms) | p99 (ms) |
    |-------------------|-----------|----------|----------|----------|
    | RLS bypassed (admin) | 0.38   | 0.35     | 0.52     | 0.74     |
    | RLS enforced         | 0.41   | 0.38     | 0.57     | 0.80     |
    | **Overhead**         | +7.9%  |          |          |          |

    ### INSERT single row (namespace = session var)

    | Mode              | Mean (ms) | p50 (ms) | p95 (ms) | p99 (ms) |
    |-------------------|-----------|----------|----------|----------|
    | RLS bypassed (admin) | 0.72   | 0.68     | 0.99     | 1.21     |
    | RLS enforced         | 0.77   | 0.73     | 1.07     | 1.35     |
    | **Overhead**         | +6.9%  |          |          |          |

    Overall mean overhead: 7.4% — within the acceptable 15% threshold.
    ADR-009 decision: SHIP RLS in GA.
"""

from __future__ import annotations

import os
import statistics
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
ITERATIONS = int(os.environ.get("BENCH_RLS_ITERATIONS", "500"))
WARMUP = int(os.environ.get("BENCH_RLS_WARMUP", "50"))
OVERHEAD_THRESHOLD_PCT = 15.0  # % — ADR-009 ship threshold


def _require_dsn() -> str:
    if not DSN:
        raise SystemExit(
            "TAPPS_TEST_POSTGRES_DSN is not set.\n"
            "Example: export TAPPS_TEST_POSTGRES_DSN=postgres://user:pass@localhost/tapps_test"
        )
    return DSN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_ns() -> str:
    return f"bench-rls-{uuid.uuid4().hex[:8]}"


def _unique_key(i: int) -> str:
    return f"bench-key-{i:06d}-{uuid.uuid4().hex[:4]}"


def _percentile(data: list[float], pct: float) -> float:
    sorted_data = sorted(data)
    index = (pct / 100.0) * (len(sorted_data) - 1)
    lower = int(index)
    upper = lower + 1
    if upper >= len(sorted_data):
        return sorted_data[-1]
    frac = index - lower
    return sorted_data[lower] + frac * (sorted_data[upper] - sorted_data[lower])


def _format_row(label: str, samples: list[float]) -> str:
    mean_ms = statistics.mean(samples) * 1000
    p50_ms = _percentile(samples, 50) * 1000
    p95_ms = _percentile(samples, 95) * 1000
    p99_ms = _percentile(samples, 99) * 1000
    return f"| {label:<34} | {mean_ms:9.3f} | {p50_ms:8.3f} | {p95_ms:8.3f} | {p99_ms:8.3f} |"


def _format_overhead(bypassed: list[float], enforced: list[float]) -> str:
    mean_bypassed = statistics.mean(bypassed) * 1000
    mean_enforced = statistics.mean(enforced) * 1000
    if mean_bypassed > 0:
        overhead_pct = ((mean_enforced - mean_bypassed) / mean_bypassed) * 100.0
    else:
        overhead_pct = 0.0
    sign = "+" if overhead_pct >= 0 else ""
    return (
        f"| **Overhead**"
        f"{'':23} | {sign}{overhead_pct:+.1f}%  |           |          |          |"
    )


@contextmanager
def _get_conn(dsn: str) -> Generator[object, None, None]:
    """Minimal context-manager wrapper around psycopg3 connection."""
    import psycopg  # type: ignore[import]

    with psycopg.connect(dsn, autocommit=False) as conn:
        yield conn


# ---------------------------------------------------------------------------
# Benchmark phases
# ---------------------------------------------------------------------------


def _bench_select_rls_bypassed(dsn: str, namespace: str, keys: list[str]) -> list[float]:
    """SELECT with admin bypass (session var = '' → no RLS filter)."""
    samples: list[float] = []
    with _get_conn(dsn) as conn:
        for i, key in enumerate(keys):
            start = time.perf_counter()
            with conn.cursor() as cur:  # type: ignore[attr-defined]
                cur.execute("SET LOCAL tapps.current_namespace = %s", ("",))
                cur.execute(
                    "SELECT key, value FROM hive_memories"
                    " WHERE namespace = %s AND key = %s",
                    (namespace, key),
                )
                cur.fetchone()
            conn.commit()  # type: ignore[attr-defined]
            elapsed = time.perf_counter() - start
            if i >= WARMUP:
                samples.append(elapsed)
    return samples


def _bench_select_rls_enforced(dsn: str, namespace: str, keys: list[str]) -> list[float]:
    """SELECT with namespace session var set (RLS isolation policy active)."""
    samples: list[float] = []
    with _get_conn(dsn) as conn:
        for i, key in enumerate(keys):
            start = time.perf_counter()
            with conn.cursor() as cur:  # type: ignore[attr-defined]
                cur.execute("SET LOCAL tapps.current_namespace = %s", (namespace,))
                cur.execute(
                    "SELECT key, value FROM hive_memories"
                    " WHERE namespace = %s AND key = %s",
                    (namespace, key),
                )
                cur.fetchone()
            conn.commit()  # type: ignore[attr-defined]
            elapsed = time.perf_counter() - start
            if i >= WARMUP:
                samples.append(elapsed)
    return samples


def _bench_insert_rls_bypassed(dsn: str, namespace: str) -> list[float]:
    """INSERT with admin bypass (session var = '')."""
    samples: list[float] = []
    total = ITERATIONS + WARMUP
    with _get_conn(dsn) as conn:
        for i in range(total):
            key = _unique_key(i)
            start = time.perf_counter()
            with conn.cursor() as cur:  # type: ignore[attr-defined]
                cur.execute("SET LOCAL tapps.current_namespace = %s", ("",))
                cur.execute(
                    "INSERT INTO hive_memories (namespace, key, value)"
                    " VALUES (%s, %s, %s)"
                    " ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value",
                    (namespace, key, f"bench-value-{i}"),
                )
            conn.commit()  # type: ignore[attr-defined]
            elapsed = time.perf_counter() - start
            if i >= WARMUP:
                samples.append(elapsed)
    return samples


def _bench_insert_rls_enforced(dsn: str, namespace: str) -> list[float]:
    """INSERT with namespace session var set (RLS WITH CHECK active)."""
    samples: list[float] = []
    total = ITERATIONS + WARMUP
    with _get_conn(dsn) as conn:
        for i in range(total):
            key = _unique_key(i)
            start = time.perf_counter()
            with conn.cursor() as cur:  # type: ignore[attr-defined]
                cur.execute("SET LOCAL tapps.current_namespace = %s", (namespace,))
                cur.execute(
                    "INSERT INTO hive_memories (namespace, key, value)"
                    " VALUES (%s, %s, %s)"
                    " ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value",
                    (namespace, key, f"bench-value-{i}"),
                )
            conn.commit()  # type: ignore[attr-defined]
            elapsed = time.perf_counter() - start
            if i >= WARMUP:
                samples.append(elapsed)
    return samples


# ---------------------------------------------------------------------------
# Seed data helpers
# ---------------------------------------------------------------------------


def _seed_rows(dsn: str, namespace: str, n: int) -> list[str]:
    """Seed N rows in namespace and return their keys (admin bypass)."""
    keys: list[str] = []
    with _get_conn(dsn) as conn:
        for i in range(n):
            key = _unique_key(i)
            keys.append(key)
            with conn.cursor() as cur:  # type: ignore[attr-defined]
                cur.execute("SET LOCAL tapps.current_namespace = %s", ("",))
                cur.execute(
                    "INSERT INTO hive_memories (namespace, key, value)"
                    " VALUES (%s, %s, %s)"
                    " ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value",
                    (namespace, key, f"seed-value-{i}"),
                )
            conn.commit()  # type: ignore[attr-defined]
    return keys


def _cleanup_namespace(dsn: str, namespace: str) -> None:
    """Delete all rows in namespace using admin bypass."""
    with _get_conn(dsn) as conn:
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute("SET LOCAL tapps.current_namespace = %s", ("",))
            cur.execute("DELETE FROM hive_memories WHERE namespace = %s", (namespace,))
        conn.commit()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    dsn = _require_dsn()

    # Apply migrations to ensure RLS is active.
    print("Applying hive migrations (001_initial + 002_rls_spike)...", flush=True)
    from tapps_brain.postgres_migrations import apply_hive_migrations

    apply_hive_migrations(dsn)

    namespace = _unique_ns()
    print(f"Test namespace: {namespace}")
    print(f"Iterations per phase: {ITERATIONS}  (+ {WARMUP} warmup discarded)")
    print()

    total_keys = ITERATIONS + WARMUP
    print(f"Seeding {total_keys} rows in namespace {namespace!r}...", flush=True)
    keys = _seed_rows(dsn, namespace, total_keys)

    # ------------------------------------------------------------------ SELECT
    print("Benchmarking SELECT (RLS bypassed)...", flush=True)
    sel_bypassed = _bench_select_rls_bypassed(dsn, namespace, keys)

    print("Benchmarking SELECT (RLS enforced)...", flush=True)
    sel_enforced = _bench_select_rls_enforced(dsn, namespace, keys)

    # ------------------------------------------------------------------ INSERT
    ins_ns_bypass = _unique_ns()
    print("Benchmarking INSERT (RLS bypassed)...", flush=True)
    ins_bypassed = _bench_insert_rls_bypassed(dsn, ins_ns_bypass)

    ins_ns_enforced = _unique_ns()
    print("Benchmarking INSERT (RLS enforced)...", flush=True)
    ins_enforced = _bench_insert_rls_enforced(dsn, ins_ns_enforced)

    # Cleanup
    _cleanup_namespace(dsn, namespace)
    _cleanup_namespace(dsn, ins_ns_bypass)
    _cleanup_namespace(dsn, ins_ns_enforced)

    # ----------------------------------------------------------------- Output
    header = (
        "| Mode                               |  Mean (ms) |  p50 (ms) |  p95 (ms) |  p99 (ms) |"
    )
    divider = (
        "|------------------------------------|------------|-----------|-----------|-----------|"
    )

    sel_mean_bypassed = statistics.mean(sel_bypassed) * 1000
    sel_mean_enforced = statistics.mean(sel_enforced) * 1000
    ins_mean_bypassed = statistics.mean(ins_bypassed) * 1000
    ins_mean_enforced = statistics.mean(ins_enforced) * 1000

    sel_overhead_pct = (
        ((sel_mean_enforced - sel_mean_bypassed) / sel_mean_bypassed * 100)
        if sel_mean_bypassed > 0
        else 0.0
    )
    ins_overhead_pct = (
        ((ins_mean_enforced - ins_mean_bypassed) / ins_mean_bypassed * 100)
        if ins_mean_bypassed > 0
        else 0.0
    )
    overall_pct = (sel_overhead_pct + ins_overhead_pct) / 2.0

    print()
    print("## RLS Overhead Benchmark — hive_memories (EPIC-063.4)")
    print()
    print("### SELECT single row by (namespace, key)")
    print()
    print(header)
    print(divider)
    print(_format_row("RLS bypassed (admin, session var='')", sel_bypassed))
    print(_format_row("RLS enforced (session var=namespace)", sel_enforced))
    sign = "+" if sel_overhead_pct >= 0 else ""
    print(f"| **Overhead**                       | {sign}{sel_overhead_pct:.1f}%     |           |           |           |")
    print()
    print("### INSERT single row (namespace = session var)")
    print()
    print(header)
    print(divider)
    print(_format_row("RLS bypassed (admin, session var='')", ins_bypassed))
    print(_format_row("RLS enforced (session var=namespace)", ins_enforced))
    sign = "+" if ins_overhead_pct >= 0 else ""
    print(f"| **Overhead**                       | {sign}{ins_overhead_pct:.1f}%     |           |           |           |")
    print()
    print(f"Overall mean overhead: {overall_pct:+.1f}%")
    print()
    if overall_pct <= OVERHEAD_THRESHOLD_PCT:
        print(
            f"✓ Overhead {overall_pct:.1f}% is within the {OVERHEAD_THRESHOLD_PCT:.0f}% threshold."
        )
        print("  ADR-009 evidence: SHIP RLS in GA.")
    else:
        print(
            f"✗ Overhead {overall_pct:.1f}% exceeds the {OVERHEAD_THRESHOLD_PCT:.0f}% threshold."
        )
        print("  ADR-009 evidence: DEFER — review compensating controls.")


if __name__ == "__main__":
    main()
