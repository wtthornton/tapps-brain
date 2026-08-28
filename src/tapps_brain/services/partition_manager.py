"""Monthly partition management for ``experience_events`` (TAP-6698).

No ``pg_partman`` dependency (Ruling 6 — the extension is not installed on
``pgvector/pgvector:pg17`` and the bundled ``022_partman_experience_events.sql``
migration stays an inert, opt-in no-op). This module reimplements the two
operations pg_partman would otherwise provide, following the manual-pruning
SQL already documented in ``docs/engineering/partition-retention.md`` Option B:

* :func:`pre_create_partitions` — ensure a monthly partition exists for every
  month from the current one through *months_ahead* months out.
* :func:`drop_old_partitions` — drop partitions whose entire range falls
  before ``now() - retention_months``.

Both are dry-run by default and operate directly on a psycopg connection
(schema-level DDL, not scoped to any one tenant's ``MemoryStore``).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from psycopg import sql

_PARENT_TABLE = "experience_events"
_DEFAULT_PARTITION = "experience_events_default"
_PARTITION_NAME_RE = re.compile(r"^experience_events_y(\d{4})m(\d{2})$")


def _add_months(d: date, months: int) -> date:
    total = (d.year * 12 + (d.month - 1)) + months
    year, month0 = divmod(total, 12)
    return date(year, month0 + 1, 1)


def _partition_name(month_start: date) -> str:
    return f"experience_events_y{month_start.year:04d}m{month_start.month:02d}"


def _current_month(conn: Any) -> date:
    """Return the DB's own current month start (not the client clock)."""
    with conn.cursor() as cur:
        cur.execute("SELECT date_trunc('month', now())::date")
        row = cur.fetchone()
    result: date = row[0]
    return result


def list_monthly_partitions(conn: Any) -> list[dict[str, Any]]:
    """List every ``experience_events_yYYYYmMM`` partition, oldest first.

    Excludes the ``DEFAULT`` partition, which carries no year/month.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT inhrelid::regclass::text AS partition_name
            FROM pg_inherits
            WHERE inhparent = %s::regclass
            """,
            (_PARENT_TABLE,),
        )
        rows = cur.fetchall()

    partitions: list[dict[str, Any]] = []
    for (name,) in rows:
        match = _PARTITION_NAME_RE.match(name)
        if not match:
            continue
        year, month = int(match.group(1)), int(match.group(2))
        month_start = date(year, month, 1)
        partitions.append(
            {
                "name": name,
                "month_start": month_start,
                "month_end": _add_months(month_start, 1),
            }
        )
    partitions.sort(key=lambda p: p["month_start"])
    return partitions


def newest_partition_horizon(conn: Any) -> date | None:
    """Return the upper bound (exclusive) of the newest monthly partition."""
    partitions = list_monthly_partitions(conn)
    if not partitions:
        return None
    result: date = partitions[-1]["month_end"]
    return result


def default_partition_row_count(conn: Any) -> int:
    """Row count of ``experience_events_default`` — the retention SLO expects 0."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT count(*) FROM {name}").format(name=sql.Identifier(_DEFAULT_PARTITION))
        )
        row = cur.fetchone()
    return int(row[0])


def pre_create_partitions(
    conn: Any,
    *,
    months_ahead: int = 3,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Ensure a monthly partition exists through *months_ahead* months out.

    Returns ``{"dry_run", "existing", "would_create"/"created", "horizon"}``.
    """
    current_month = _current_month(conn)
    existing = {p["name"] for p in list_monthly_partitions(conn)}
    to_create: list[dict[str, Any]] = []
    for offset in range(months_ahead + 1):
        month_start = _add_months(current_month, offset)
        name = _partition_name(month_start)
        if name in existing:
            continue
        to_create.append(
            {
                "name": name,
                "month_start": month_start,
                "month_end": _add_months(month_start, 1),
            }
        )

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "current_month": current_month.isoformat(),
        "horizon": _add_months(current_month, months_ahead + 1).isoformat(),
        "existing_count": len(existing),
    }
    if dry_run:
        result["would_create"] = [p["name"] for p in to_create]
        return result

    created: list[str] = []
    with conn.cursor() as cur:
        for p in to_create:
            # PARTITION OF ... FOR VALUES FROM/TO requires constant expressions —
            # Postgres rejects protocol-level bind parameters ($1/$2) in this DDL
            # clause ("there is no parameter $1"). sql.Literal() safely inlines a
            # properly quoted constant instead of a bind param.
            cur.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {name} PARTITION OF {parent} "
                    "FOR VALUES FROM ({start}) TO ({end})"
                ).format(
                    name=sql.Identifier(p["name"]),
                    parent=sql.Identifier(_PARENT_TABLE),
                    start=sql.Literal(p["month_start"]),
                    end=sql.Literal(p["month_end"]),
                )
            )
            created.append(p["name"])
    result["created"] = created
    return result


def drop_old_partitions(
    conn: Any,
    *,
    retention_months: int,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Drop monthly partitions whose entire range is older than the retention window.

    ``retention_months`` must be a positive int (the env var is validated by
    the caller before this is invoked). The ``DEFAULT`` partition is never a
    candidate — it has no bounded range to compare against a cutoff.
    """
    current_month = _current_month(conn)
    cutoff = _add_months(current_month, -retention_months)
    partitions = list_monthly_partitions(conn)
    candidates = [p for p in partitions if p["month_end"] <= cutoff]

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "cutoff": cutoff.isoformat(),
        "retention_months": retention_months,
    }
    if dry_run:
        result["would_drop"] = [p["name"] for p in candidates]
        return result

    dropped: list[str] = []
    with conn.cursor() as cur:
        for p in candidates:
            cur.execute(
                sql.SQL("DROP TABLE IF EXISTS {name}").format(name=sql.Identifier(p["name"]))
            )
            dropped.append(p["name"])
    result["dropped"] = dropped
    return result
