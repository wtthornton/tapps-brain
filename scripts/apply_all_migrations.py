#!/usr/bin/env python3
"""Apply all pending tapps-brain schema migrations (private, hive, federation).

Usage
-----
    # Reads DSN from env var (preferred)
    export TAPPS_BRAIN_DATABASE_URL=postgresql://tapps:tapps@localhost:5432/tapps_dev
    python scripts/apply_all_migrations.py

    # Or pass DSN as first positional argument
    python scripts/apply_all_migrations.py postgresql://tapps:tapps@localhost:5432/tapps_dev

Exit codes
----------
    0 — all migrations applied (or already current)
    1 — error (missing DSN, migration failure, import error)
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    dsn = (
        sys.argv[1]
        if len(sys.argv) > 1
        else (
            os.environ.get("TAPPS_BRAIN_DATABASE_URL")
            or os.environ.get("TAPPS_TEST_POSTGRES_DSN")
        )
    )
    if not dsn:
        print(
            "ERROR: no DSN supplied. Set TAPPS_BRAIN_DATABASE_URL or pass it as the first argument.",
            file=sys.stderr,
        )
        return 1

    try:
        from tapps_brain.postgres_migrations import (
            apply_federation_migrations,
            apply_hive_migrations,
            apply_private_migrations,
        )
    except ImportError as exc:
        print(f"ERROR: could not import tapps_brain: {exc}", file=sys.stderr)
        print("Ensure the package is installed (e.g. `uv sync --group dev`).", file=sys.stderr)
        return 1

    steps = [
        ("private",     apply_private_migrations),
        ("hive",        apply_hive_migrations),
        ("federation",  apply_federation_migrations),
    ]

    print(f"Applying migrations to: {dsn!r}")
    for name, apply_fn in steps:
        print(f"  [{name}] running…", end=" ", flush=True)
        try:
            applied = apply_fn(dsn)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED\nERROR: {exc}", file=sys.stderr)
            return 1
        if applied:
            print(f"applied versions {applied}")
        else:
            print("already current")

    print("All migrations applied successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
