#!/usr/bin/env python3
"""Analyze Ralph per-loop startup-duration metrics.

Reads JSONL files from .ralph/metrics/startup-YYYY-MM.jsonl and prints a
p50/p95/p99 summary per timing phase.  Written for TAP-1852 (WS4.2).

Usage:
    python3 scripts/analyze-ralph-startup.py [--days 7] [--metrics-dir .ralph/metrics]

Each JSONL line contains:
    {"ts": "2026-05-16T06:00:00Z",
     "mcp_probe_ms": 312, "tools_list_ms": 45,
     "session_start_ms": 0, "linear_count_ms": 0, "total_ms": 380}

Fields with value 0 are excluded from percentile calculations (not measured
that loop) but counted in the sample totals.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Summarise Ralph startup-phase latencies from JSONL metrics files."
    )
    p.add_argument(
        "--days",
        type=int,
        default=7,
        help="Look-back window in days (default: 7).",
    )
    p.add_argument(
        "--metrics-dir",
        default=".ralph/metrics",
        help="Path to the metrics directory (default: .ralph/metrics).",
    )
    return p.parse_args()


def percentile(values: list[float], p: int) -> float:
    """Return the p-th percentile of *values* (0–100).  Returns 0.0 if empty."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def load_entries(metrics_dir: Path, since: datetime) -> list[dict]:  # type: ignore[type-arg]
    """Load all JSONL entries from startup-*.jsonl files newer than *since*."""
    entries: list[dict] = []  # type: ignore[type-arg]
    for path in sorted(metrics_dir.glob("startup-*.jsonl")):
        try:
            with path.open() as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    ts_raw = obj.get("ts", "")
                    try:
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if ts >= since:
                        entries.append(obj)
        except OSError:
            continue
    return entries


PHASES = [
    ("mcp_probe_ms", "pre-warm /healthz"),
    ("tools_list_ms", "/v1/tools/list"),
    ("session_start_ms", "tapps_session_start"),
    ("linear_count_ms", "Linear count probe"),
    ("total_ms", "total hook duration"),
]


def main() -> int:
    args = parse_args()
    metrics_dir = Path(args.metrics_dir)

    if not metrics_dir.exists():
        print(
            f"Metrics directory not found: {metrics_dir}\n"
            "No data has been recorded yet.  Run Ralph once with the TAP-1852 hook\n"
            "changes applied, then rerun this script.",
            file=sys.stderr,
        )
        return 1

    since = datetime.now(tz=timezone.utc) - timedelta(days=args.days)
    entries = load_entries(metrics_dir, since)

    if not entries:
        print(
            f"No startup entries found in the last {args.days} days.\n"
            f"Files searched: {metrics_dir}/startup-*.jsonl"
        )
        return 0

    print(f"Ralph startup metrics — last {args.days} days  ({len(entries)} samples)\n")
    header = f"{'Phase':<26} {'samples':>7} {'p50':>8} {'p95':>8} {'p99':>8} {'min':>8} {'max':>8}"
    print(header)
    print("-" * len(header))

    for field, label in PHASES:
        all_vals = [float(e.get(field, 0)) for e in entries]
        measured = [v for v in all_vals if v > 0]
        n = len(measured)
        if n:
            p50 = percentile(measured, 50)
            p95 = percentile(measured, 95)
            p99 = percentile(measured, 99)
            mn = min(measured)
            mx = max(measured)
        else:
            p50 = p95 = p99 = mn = mx = 0.0

        def _fmt(ms: float) -> str:
            return f"{ms:>6.0f}ms" if ms > 0 else "       —"

        print(
            f"{label:<26} {n:>7}  {_fmt(p50)} {_fmt(p95)} {_fmt(p99)} {_fmt(mn)} {_fmt(mx)}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
