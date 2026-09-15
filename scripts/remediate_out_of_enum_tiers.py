#!/usr/bin/env python3
"""Dry-run remediation proposal for out-of-enum ``private_memories.tier`` rows.

TAP-6753.  Some rows in ``private_memories`` carry a ``tier`` value that is
neither a :class:`~tapps_brain.models.MemoryTier` member nor a layer name of
the profile the reading process is configured with (e.g. ``identity``,
``long-term``, ``short-term`` — personal-assistant layer names persisted by a
process that never wrote its profile to ``project_profiles``). The write-time
ingress that let those rows in has since been closed (TAP-6698,
``tapps_brain.tier_normalize.normalize_save_tier``, wired into every save
path — ``store.py:1801``, ``memory_relay.py``, ``services/memory_service.py``,
``cli/memory.py``, ``experience.py``'s ``MemorySpec`` validator) — no *new*
row can land with an unpriceable tier. The rows already written before that
fix still need a remediation plan.

This script is READ-ONLY. It:

1. Selects the current out-of-enum rows (a fresh count, never trusted from a
   prior report).
2. Proposes, per row, the canonical tier ``normalize_save_tier`` would assign
   today (the same deterministic mapping every ingress now enforces).
3. Prints the backup-table DDL and the per-row ``UPDATE`` statements it would
   run under ``--apply`` — but ``--apply`` is intentionally NOT implemented.
   This script cannot mutate ``private_memories`` no matter which flags are
   passed. Turning the proposal into an executed migration is a deliberate,
   reviewed follow-up (a real migration file under
   ``src/tapps_brain/migrations/private/``), never a flag on this script.

Usage:
    uv run python scripts/remediate_out_of_enum_tiers.py

Reads the DSN from ``TAPPS_BRAIN_DATABASE_URL`` (never prints it).
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

import psycopg

from tapps_brain.models import MemoryTier
from tapps_brain.tier_normalize import normalize_save_tier

_ENUM_TIERS = tuple(t.value for t in MemoryTier)


def _backup_table_name(now: datetime) -> str:
    stamp = now.strftime("%Y%m%d_%H%M%S")
    return f"private_memories_tier_backfill_backup_{stamp}"


def main() -> int:
    dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL")
    if not dsn:
        print("TAPPS_BRAIN_DATABASE_URL is not set; nothing to inspect.", file=sys.stderr)
        return 1

    now = datetime.now(tz=UTC)
    backup_table = _backup_table_name(now)

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT project_id, agent_id, key, tier FROM private_memories "
            "WHERE tier NOT IN %s ORDER BY project_id, agent_id, key",
            (_ENUM_TIERS,),
        )
        rows = cur.fetchall()

    print(f"# Dry-run remediation proposal — TAP-6753 ({now.isoformat()})")
    print(f"# Out-of-enum row count (fresh, this run): {len(rows)}")
    print()

    if not rows:
        print("No out-of-enum rows found. Nothing to propose.")
        return 0

    print("## Step 1 — backup table (proposed DDL, NOT executed by this script)")
    print(
        f"CREATE TABLE {backup_table} AS\n"
        "SELECT * FROM private_memories\n"
        f"WHERE tier NOT IN {_ENUM_TIERS!r};"
    )
    print()

    print("## Step 2 — per-row remediation (proposed UPDATEs, NOT executed by this script)")
    for project_id, agent_id, key, tier in rows:
        proposed = normalize_save_tier(tier, None)
        print(
            "UPDATE private_memories SET tier = "
            f"{proposed!r} WHERE project_id = {project_id!r} "
            f"AND agent_id = {agent_id!r} AND key = {key!r}; "
            f"-- was {tier!r}"
        )
    print()
    print(
        "## Step 3 — verification query to run after a REVIEWED, EXECUTED migration\n"
        "SELECT count(*) FROM private_memories WHERE tier NOT IN "
        f"{_ENUM_TIERS!r};  -- expect 0 after remediation lands"
    )
    print()
    print(
        "This script performed no writes. Turning this proposal into an "
        "applied change requires a reviewed migration file — never a flag "
        "on this script."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
