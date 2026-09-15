#!/usr/bin/env python3
"""Read-only triage report for hive rows with no live private counterpart (TAP-6816).

Orphan definition, derived from the propagation path in ``backends.py``
(``PropagationEngine.propagate``, which calls ``hive_store.save(key=key,
source_agent=agent_id, ...)`` — see backends.py:408) and the tables in
``migrations/hive/001_initial.sql`` / ``migrations/private/001_initial.sql``:

* A hive row propagated from a private row carries the *same* ``key`` value
  and ``source_agent = <private row's agent_id>``. ``hive_memories.namespace``
  is derived from agent_scope/profile (backends.py:96-131), not project_id —
  there is no project_id column on hive_memories at all.
* A hive row is therefore an **orphan** when no row in ``private_memories``
  matches on ``(agent_id, key) == (hive.source_agent, hive.key)`` with
  ``status = 'active'`` (migrations/private/027_memory_status.sql defines the
  status CHECK: active/stale/superseded/archived/contradicted — 'active' is
  the only "live" state).
* For each orphan we further state whether a private counterpart exists at
  all:
    - "archived": a private_memories row matches (agent_id, key) but its
      status is not 'active', OR a gc_archive row matches (agent_id, key)
      (migrations/private/006_gc_archive.sql — GC archives rows instead of
      deleting them).
    - "absent": no private_memories row and no gc_archive row match at all.

This script issues only ``SELECT`` statements against both databases (the
hive DSN and the private DSN may be the same Postgres instance or, per
TAPPS_BRAIN_HIVE_DSN, two different ones — see CLAUDE.md's env var table).
It has no apply/reap/write path of any kind. The join itself is done in
Python because the two backends are not guaranteed to be the same database.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

HIVE_SELECT_SQL = "SELECT namespace, key, source_agent, created_at FROM hive_memories"
HIVE_COUNT_SQL = "SELECT count(*) FROM hive_memories"
PRIVATE_SELECT_SQL = "SELECT project_id, agent_id, key, status FROM private_memories"
GC_ARCHIVE_SELECT_SQL = "SELECT project_id, agent_id, key FROM gc_archive"

AGE_BUCKETS: tuple[tuple[str, int], ...] = (
    ("0-7d", 7),
    ("7-30d", 30),
    ("30-90d", 90),
    ("90-365d", 365),
)
AGE_BUCKET_OVERFLOW = "365d+"


class _Cursor(Protocol):
    """The subset of a DB-API cursor this script relies on."""

    def execute(self, query: str, params: Sequence[Any] | None = None) -> object: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...


@dataclass(frozen=True)
class HiveRow:
    namespace: str
    key: str
    source_agent: str
    created_at: datetime


@dataclass(frozen=True)
class OrphanRecord:
    namespace: str
    key: str
    source_agent: str
    created_at: datetime
    counterpart: str  # "archived" | "absent"
    project_id: str  # "unknown" when counterpart == "absent"
    age_bucket: str


def fetch_hive_rows(cur: _Cursor) -> list[HiveRow]:
    cur.execute(HIVE_SELECT_SQL)
    return [
        HiveRow(namespace=row[0], key=row[1], source_agent=row[2], created_at=row[3])
        for row in cur.fetchall()
    ]


def fetch_hive_total(cur: _Cursor) -> int:
    cur.execute(HIVE_COUNT_SQL)
    row = cur.fetchone()
    return int(row[0]) if row is not None else 0


def fetch_private_status(cur: _Cursor) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Return {(agent_id, key): [(project_id, status), ...]}."""
    cur.execute(PRIVATE_SELECT_SQL)
    out: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for project_id, agent_id, key, status in cur.fetchall():
        out[(agent_id, key)].append((project_id, status))
    return dict(out)


def fetch_gc_archive_projects(cur: _Cursor) -> dict[tuple[str, str], str]:
    """Return {(agent_id, key): project_id} — first match wins, deterministically."""
    cur.execute(GC_ARCHIVE_SELECT_SQL)
    out: dict[tuple[str, str], str] = {}
    for project_id, agent_id, key in cur.fetchall():
        existing = out.get((agent_id, key))
        if existing is None or project_id < existing:
            out[(agent_id, key)] = project_id
    return out


def age_bucket(created_at: datetime, now: datetime) -> str:
    age_days = (now - created_at).total_seconds() / 86400
    for label, max_days in AGE_BUCKETS:
        if age_days <= max_days:
            return label
    return AGE_BUCKET_OVERFLOW


def classify(
    hive_rows: list[HiveRow],
    private_status: dict[tuple[str, str], list[tuple[str, str]]],
    gc_archive_projects: dict[tuple[str, str], str],
    now: datetime,
) -> list[OrphanRecord]:
    """Classify hive rows, returning only the orphans.

    A hive row is skipped (not an orphan) when any matching private row has
    status == 'active'.
    """
    orphans: list[OrphanRecord] = []
    for row in hive_rows:
        matches = private_status.get((row.source_agent, row.key), [])
        if any(status == "active" for _project_id, status in matches):
            continue  # live private counterpart exists — not an orphan

        non_active = [(pid, status) for pid, status in matches if status != "active"]
        if non_active:
            project_id, _status = sorted(non_active)[0]
            counterpart = "archived"
        else:
            archived_project = gc_archive_projects.get((row.source_agent, row.key))
            if archived_project is not None:
                project_id = archived_project
                counterpart = "archived"
            else:
                project_id = "unknown"
                counterpart = "absent"

        orphans.append(
            OrphanRecord(
                namespace=row.namespace,
                key=row.key,
                source_agent=row.source_agent,
                created_at=row.created_at,
                counterpart=counterpart,
                project_id=project_id,
                age_bucket=age_bucket(row.created_at, now),
            )
        )
    return orphans


@dataclass(frozen=True)
class TriageReport:
    hive_total: int
    orphans: list[OrphanRecord]

    @property
    def orphan_count(self) -> int:
        return len(self.orphans)

    @property
    def by_project(self) -> Counter[str]:
        return Counter(o.project_id for o in self.orphans)

    @property
    def by_age(self) -> Counter[str]:
        return Counter(o.age_bucket for o in self.orphans)

    @property
    def by_counterpart(self) -> Counter[str]:
        return Counter(o.counterpart for o in self.orphans)


def build_report(report: TriageReport, generated_at: datetime) -> str:
    lines = [
        "# Hive orphan triage report (TAP-6816, b4 report half)",
        "",
        f"Generated: {generated_at.isoformat()}",
        "",
        "## Orphan definition",
        "",
        "A hive_memories row is an orphan when no private_memories row matches "
        "on (agent_id, key) == (hive.source_agent, hive.key) with status='active' "
        "(see scripts/triage_hive_orphans.py module docstring for the full "
        "derivation from backends.py:408 and the private status CHECK in "
        "migrations/private/027_memory_status.sql). Read-only; this report "
        "makes no reap/archive/apply decision.",
        "",
        f"Hive rows counted (total population): {report.hive_total}",
        f"Orphans found: {report.orphan_count}",
        "",
        "## By project",
        "",
    ]
    for project_id, count in sorted(report.by_project.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- {project_id}: {count}")

    lines += ["", "## By age", ""]
    bucket_order = [label for label, _ in AGE_BUCKETS] + [AGE_BUCKET_OVERFLOW]
    for label in bucket_order:
        count = report.by_age.get(label, 0)
        if count:
            lines.append(f"- {label}: {count}")

    lines += ["", "## Private counterpart", ""]
    for counterpart, count in sorted(report.by_counterpart.items()):
        lines.append(f"- {counterpart}: {count}")

    lines += [
        "",
        "## Decision",
        "",
        "Not made here. Reaping is an operator decision recorded later on TAP-6816.",
        "",
    ]
    return "\n".join(lines)


def _connect_readonly(dsn: str) -> Any:  # noqa: ANN401 - psycopg import is lazy
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - environment guard
        msg = "psycopg is required: pip install 'psycopg[binary]'"
        raise SystemExit(msg) from exc

    # Read-only is enforced at the connection level (a GUC set via the
    # connection string), not by issuing a SQL statement from this script —
    # every statement this script executes is a SELECT.
    conn = psycopg.connect(dsn, options="-c default_transaction_read_only=on")
    conn.read_only = True
    return conn


def run_triage(
    hive_cur: _Cursor,
    private_cur: _Cursor,
    now: datetime | None = None,
) -> TriageReport:
    now = now or datetime.now(tz=UTC)
    hive_total = fetch_hive_total(hive_cur)
    hive_rows = fetch_hive_rows(hive_cur)
    private_status = fetch_private_status(private_cur)
    gc_archive_projects = fetch_gc_archive_projects(private_cur)
    orphans = classify(hive_rows, private_status, gc_archive_projects, now)
    return TriageReport(hive_total=hive_total, orphans=orphans)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_hive_dsn = os.environ.get("TAPPS_BRAIN_HIVE_DSN") or os.environ.get(
        "TAPPS_BRAIN_DATABASE_URL"
    )
    parser.add_argument(
        "--hive-dsn",
        default=default_hive_dsn,
        help="Postgres DSN for the hive store (default: TAPPS_BRAIN_HIVE_DSN or DATABASE_URL)",
    )
    parser.add_argument(
        "--private-dsn",
        default=os.environ.get("TAPPS_BRAIN_DATABASE_URL"),
        help="Postgres DSN for the private store (default: TAPPS_BRAIN_DATABASE_URL)",
    )
    parser.add_argument("--output", default=None, help="Optional path to write the report to")
    args = parser.parse_args(argv)

    if not args.hive_dsn or not args.private_dsn:
        print(
            "Both --hive-dsn and --private-dsn (or their env vars) are required.",
            file=sys.stderr,
        )
        return 2

    hive_conn = _connect_readonly(args.hive_dsn)
    private_conn = (
        hive_conn if args.private_dsn == args.hive_dsn else _connect_readonly(args.private_dsn)
    )
    try:
        with hive_conn.cursor() as hive_cur, private_conn.cursor() as private_cur:
            report = run_triage(hive_cur, private_cur)
    finally:
        hive_conn.close()
        if private_conn is not hive_conn:
            private_conn.close()

    text = build_report(report, datetime.now(tz=UTC))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
